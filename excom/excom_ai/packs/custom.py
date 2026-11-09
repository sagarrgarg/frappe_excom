"""Custom agents (HLD-007 §8.5, Phase 2A): built from keywords and a short description, with up to
four abilities, always in Review and with read and draft tools only.

    watch   a daily scan of what its keyword recipes find: each record that newly appears gets one
            ToDo for the owner (what the agent knows is rebuilt by the nightly builder already)
    answer  questions on its one channel, answered from its playbook (a reply for a person to approve)
    chase   a sequence over one record type: the records its first keyword recipe finds get a
            follow-up ToDo for the owner, at most once a week each
    draft   documents or messages on request (a note for a person to use)

Nothing here sends, and nothing is Auto: a Custom pack's ceiling is Review (constants.ceiling).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, nowdate

from excom.excom_ai.engine import proposals, registry, runner
from excom.excom_ai.engine.prompts import untrusted

CHASE_EVERY_DAYS = 7
WATCH_LIMIT = 20  # at most this many new-record ToDos a day
CHASE_LIMIT = 20


def abilities(agent) -> set[str]:
	return {a.strip() for a in (agent.get("abilities") or "watch, draft").split(",") if a.strip()}


@registry.planner("custom")
def plan(agent) -> list[dict]:
	if agent.status not in ("Shadow", "Live"):
		return []
	can = abilities(agent)
	specs = []
	today = nowdate()
	if "watch" in can and _record_recipes(agent):
		specs.append({"kind": "custom_watch", "dedupe_key": f"watch:{today}"})
	if "chase" in can and _chase_recipe(agent):
		specs.append({"kind": "custom_chase", "dedupe_key": f"chase:{today}"})
	return specs


def _owner(agent) -> str:
	return agent.acting_owner or agent.owner_user


def _record_recipes(agent) -> list:
	"""The agent's included keyword recipes that list records (not built-ins, not grouped counts)."""
	out = []
	for row in agent.keyword_rows or []:
		if not (row.included and row.get("recipe")):
			continue
		recipe = frappe.get_doc("Excom AI Recipe", row.recipe)
		if recipe.kind == "Built-in":
			continue
		spec = recipe.spec()
		if spec.get("group_by"):
			continue
		out.append((recipe, {**spec, "fields": ["name"]}))
	return out


def _chase_recipe(agent):
	recipes = _record_recipes(agent)
	return recipes[0] if recipes else None


def _scan(agent, recipes, limit: int) -> list[tuple]:
	"""(recipe, doctype, name) for what the recipes find, read as the owner (team permissions apply)."""
	from excom.excom_ai.doctype.excom_ai_recipe.excom_ai_recipe import run_spec
	from excom.excom_ai.engine.identity import as_user

	found = []
	with as_user(agent.owner_user):
		for recipe, spec in recipes:
			for r in run_spec(spec, limit=limit):
				if r.get("name"):
					found.append((recipe, spec["doctype"], r["name"]))
	return found


def _told(agent, doctype: str, name: str, since=None) -> bool:
	filters = {"agent": agent.name, "action": "create_todo", "subject_doctype": doctype, "subject_name": name}
	if since:
		filters["creation"] = [">=", since]
	return bool(frappe.db.exists("Excom AI Proposal", filters))


@registry.task_kind("custom_watch", priority=registry.PRIORITY["refresh"], effort="classify")
def custom_watch(task, agent) -> str:
	"""A record the keywords newly find gets one ToDo for the owner (once, ever). What the agent
	knows is rebuilt by the nightly builder, which already tells the owner when that changes."""
	made = 0
	for recipe, doctype, name in _scan(agent, _record_recipes(agent), WATCH_LIMIT):
		if made >= WATCH_LIMIT or _told(agent, doctype, name):
			continue
		if proposals.propose(task, agent, "create_todo", subject=(doctype, name), risk="Low",
				payload={"allocated_to": _owner(agent), "text": _("{0} (AI) noticed a new {1}: {2} ({3})").format(
					agent.agent_name, doctype, name, recipe.label)}):
			made += 1
	return _("{0} new records reported").format(made)


@registry.task_kind("custom_chase", priority=registry.PRIORITY["check"], effort="classify")
def custom_chase(task, agent) -> str:
	"""A follow-up ToDo for each record the first recipe finds, at most once a week each."""
	first = _chase_recipe(agent)
	if not first:
		return _("no record list to chase")
	since = add_days(nowdate(), -CHASE_EVERY_DAYS)
	made = 0
	for recipe, doctype, name in _scan(agent, [first], CHASE_LIMIT):
		if _told(agent, doctype, name, since):
			continue
		if proposals.propose(task, agent, "create_todo", subject=(doctype, name), risk="Low",
				payload={"allocated_to": _owner(agent), "text": _("Follow up {0} {1} ({2})").format(doctype, name, recipe.label)}):
			made += 1
	return _("{0} follow-ups proposed").format(made)


DRAFT_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "text": {"type": "string"}},
	"required": ["title", "text"], "additionalProperties": False}


@registry.task_kind("custom_draft", priority=registry.PRIORITY["write"], effort="write")
def custom_draft(task, agent) -> str:
	"""A document or message drafted on request, from the playbook and the named record."""
	params = frappe.parse_json(task.params or "{}") or {}
	context = ""
	if task.subject_doctype and task.subject_name and frappe.has_permission(task.subject_doctype, "read", task.subject_name,
			user=agent.owner_user):  # this record, as its owner sees it
		doc = frappe.get_doc(task.subject_doctype, task.subject_name)
		context = untrusted(frappe.as_json({k: v for k, v in doc.as_dict().items() if isinstance(v, str | int | float)})[:4000],
			f"{task.subject_doctype} {task.subject_name}")
	r = runner.ai(task, agent, output_schema=DRAFT_SCHEMA,
		pack_instructions=_("Draft what the owner asked for, plainly, from the playbook and the record given. Invent nothing."),
		prompt=_("The request: {0}").format(params.get("instruction") or "") + ("\n\n" + context if context else ""))
	proposals.propose(task, agent, "note", subject=(task.subject_doctype, task.subject_name) if task.subject_name else
		("Excom AI Agent", agent.name), risk="Low", payload={"text": r.data["text"], "title": r.data["title"]},
		preview_html=f"<b>{frappe.utils.escape_html(r.data['title'])}</b><br>"
			+ frappe.utils.escape_html(r.data["text"]).replace("\n", "<br>"))
	return _("drafted: {0}").format(r.data["title"])


ANSWER_SCHEMA = {"type": "object", "properties": {"body": {"type": "string"}, "answerable": {"type": "boolean"}},
	"required": ["body", "answerable"], "additionalProperties": False}


@registry.responder("custom")
def answer(task, agent) -> str:
	"""Questions on its channel, answered from the playbook; what the playbook does not cover goes to a
	person. Without the answer ability, the message stays with people."""
	from excom.excom_ai import responder as sales_responder
	from excom.excom_ai.engine.inbound import hand_to_person

	if "answer" not in abilities(agent):
		return _("this agent does not answer messages")
	params = frappe.parse_json(task.params or "{}") or {}
	text = sales_responder.clean(sales_responder.full_text(params.get("message"))) if params.get("message") else ""
	if not text or not task.thread:
		return _("nothing to answer")
	if sales_responder.rule_intent(text):
		hand_to_person(task.thread, agent, text[:500], params.get("message"), reason="escalate")
		return _("handed to a person")
	r = runner.ai(task, agent, output_schema=ANSWER_SCHEMA,
		pack_instructions=_("Answer only from the playbook. If it does not cover the question, set answerable false."),
		prompt=untrusted(text[:4000], _("the question")))
	if not r.data.get("answerable"):
		hand_to_person(task.thread, agent, text[:500], params.get("message"), reason="escalate")
		return _("not in the playbook: handed to a person")
	thread = frappe.get_doc("Excom Thread", task.thread)
	name = proposals.propose(task, agent, "reply", thread=thread.name, channel_account=thread.account, kind="Reply",
		subject=("Excom Thread", thread.name), payload={"subject": "", "body": r.data["body"], "intent": "question"},
		preview_html=frappe.utils.escape_html(r.data["body"]).replace("\n", "<br>"))
	return _("answer proposed: {0}").format(name)


def request_draft(agent_name: str, instruction: str, subject_doctype: str | None = None, subject_name: str | None = None) -> str:
	from excom.excom_ai.engine import tasks

	return tasks.add(agent_name, "custom_draft", subject=(subject_doctype, subject_name) if subject_name else None,
		dedupe_key=f"draft:{frappe.generate_hash(length=8)}", params={"instruction": instruction})
