"""Built-in task kinds and executors, available to every pack.

- `echo`: a self-test. The agent asks its model to restate its own role in one sentence and
  proposes it as a note on itself. It exercises the whole path (gates, the agent's user, prompt
  assembly with caching, ai_interface with the agent's budget scope, a proposal under policy,
  execution, audit) and is how Phase 0.3 is accepted.
- executors `note` and `create_todo`: a comment on the subject, or a ToDo for whoever is in charge.
"""

from __future__ import annotations

import frappe
from frappe import _

from excom.excom_ai.engine import proposals, registry, runner

ECHO_SCHEMA = {
	"type": "object",
	"properties": {"summary": {"type": "string"}},
	"required": ["summary"],
	"additionalProperties": False,
}


@registry.task_kind("echo", priority=registry.PRIORITY["refresh"], effort="classify")
def echo(task, agent) -> str:
	result = runner.ai(
		task, agent,
		prompt="In one sentence, say who you are and what you are here to do.",
		output_schema=ECHO_SCHEMA,
	)
	name = proposals.propose(
		task, agent, "note",
		subject=("Excom AI Agent", agent.name),
		payload={"text": result.data["summary"]},
		preview_html=frappe.utils.escape_html(result.data["summary"]),
		risk="Low",
	)
	return f"echo: {result.data['summary'][:120]} ({'proposal ' + name if name else 'policy Off'})"


@registry.executor("note")
def add_note(proposal, agent):
	payload = frappe.parse_json(proposal.payload or "{}")
	if not (proposal.subject_doctype and proposal.subject_name):
		frappe.throw(_("A note needs a record to go on."))
	ref = frappe.get_doc(proposal.subject_doctype, proposal.subject_name)
	comment = ref.add_comment("Comment", f"{frappe.utils.escape_html(payload.get('text') or '')}<br>"
		f"<span class='text-muted'>{agent.agent_name} (AI)</span>")
	return ("Comment", comment.name)


@registry.executor("create_todo")
def create_todo(proposal, agent):
	payload = frappe.parse_json(proposal.payload or "{}")
	# Only to the agent's own people: an edited payload cannot hand work to anyone else.
	people = set(proposals.reviewers(agent)) | {agent.owner_user}
	allocated = payload.get("allocated_to") if payload.get("allocated_to") in people else None
	todo = frappe.get_doc({
		"doctype": "ToDo",
		"allocated_to": allocated or agent.acting_owner or agent.owner_user,
		"description": payload.get("text") or proposal.action,
		"reference_type": proposal.subject_doctype,
		"reference_name": proposal.subject_name,
		"date": payload.get("date"),
		"priority": payload.get("priority") or "Medium",
		"assigned_by": agent.service_user,
	}).insert(ignore_permissions=True)
	return ("ToDo", todo.name)
