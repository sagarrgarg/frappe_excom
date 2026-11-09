"""The learning loop (HLD-007 Phase 4).

- Coach suggestions become playbook versions: monitor.coach proposes, an accepted suggestion is a new
  playbook version in review, and prompts keep the last approved version until then (the playbook's
  approved_sections).
- Subject-line tests: a sequence step lists subject-line styles (subject_test); each buyer gets one
  (writer.subject_variant). Here: the replies each style got, and, when one clearly wins, a proposal
  to keep it (a person decides).
- Cost tuning: cheaper model and effort pairs are scored on the evaluation set; the cheapest one that
  still passes is recommended. Nothing switches on its own: a change still goes through
  monitor.gate_change.
"""

from __future__ import annotations

import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt

from excom.excom_ai.engine import proposals, registry

MIN_SENDS = 30  # per style, before a test can be concluded
MAX_P = 0.1  # how sure: a two-sided test of two proportions


# ── subject-line tests ───────────────────────────────────────────────────────


def subject_results(agent_name: str) -> list[dict]:
	"""Per sequence step under test, per style: emails sent and buyers who answered after them."""
	rows = frappe.db.sql("""SELECT p.name, p.enrollment, p.step, p.payload, p.sent_on, t.last_inbound_at
		FROM `tabExcom AI Proposal` p LEFT JOIN `tabExcom Thread` t ON t.name = p.thread
		WHERE p.agent = %s AND p.status = 'Sent' AND p.action = 'send_email' AND p.payload LIKE %s""",
		(agent_name, '%"subject_variant"%'), as_dict=True)
	if not rows:
		return []
	sequences = dict(frappe.get_all("Excom AI Enrollment", {"name": ["in", list({r.enrollment for r in rows if r.enrollment})]},
		["name", "sequence"], as_list=True))
	out: dict[tuple, dict] = {}
	for r in rows:
		style = (json.loads(r.payload or "{}") or {}).get("subject_variant")
		sequence = sequences.get(r.enrollment)
		if not style or not sequence:
			continue
		cell = out.setdefault((sequence, cint(r.step), style), {"sequence": sequence, "step": cint(r.step), "style": style,
			"sent": 0, "answered": 0})
		cell["sent"] += 1
		if r.last_inbound_at and r.sent_on and r.last_inbound_at > r.sent_on:
			cell["answered"] += 1
	return sorted(out.values(), key=lambda c: (c["sequence"], c["step"], c["style"]))


def _p_value(a_sent, a_won, b_sent, b_won) -> float:
	"""Two-sided p-value for a difference of two proportions (normal approximation)."""
	if not a_sent or not b_sent:
		return 1.0
	pooled = (a_won + b_won) / (a_sent + b_sent)
	se = math.sqrt(pooled * (1 - pooled) * (1 / a_sent + 1 / b_sent))
	if not se:
		return 1.0
	z = abs(a_won / a_sent - b_won / b_sent) / se
	return math.erfc(z / math.sqrt(2))


def winners(agent_name: str) -> list[dict]:
	"""Steps whose test is decided: every style sent at least MIN_SENDS times, and the best one's
	reply rate beats the runner-up's with p <= MAX_P."""
	by_step: dict[tuple, list] = {}
	for cell in subject_results(agent_name):
		by_step.setdefault((cell["sequence"], cell["step"]), []).append(cell)
	out = []
	for (sequence, step), cells in by_step.items():
		live = _styles_now(sequence, step)
		cells = [c for c in cells if c["style"] in live]
		if len(cells) < 2 or len(cells) < len(live) or any(c["sent"] < MIN_SENDS for c in cells):
			continue
		cells.sort(key=lambda c: c["answered"] / c["sent"], reverse=True)
		best, second = cells[0], cells[1]
		p = _p_value(best["sent"], best["answered"], second["sent"], second["answered"])
		if p <= MAX_P:
			out.append({"sequence": sequence, "step": step, "keep": best["style"], "p": round(p, 3),
				"results": [{k: c[k] for k in ("style", "sent", "answered")} for c in cells]})
	return out


def _styles_now(sequence: str, step: int) -> list[str]:
	from excom.excom_ai.writer import subject_styles

	row = frappe.db.get_value("Excom AI Sequence Step", {"parent": sequence, "step": step}, ["subject_test", "step"], as_dict=True)
	return subject_styles(row) if row else []


def propose_winners(task, agent) -> int:
	"""For the coach: one proposal per decided test, to keep the winning style."""
	made = 0
	for w in winners(agent.name):
		lines = "<br>".join(f"{frappe.utils.escape_html(r['style'])}: {r['answered']} replies of {r['sent']}" for r in w["results"])
		if proposals.propose(task, agent, "keep_subject", policy_action="playbook_change", subject=("Excom AI Sequence", w["sequence"]),
				risk="Low", payload=w, preview_html=_("Step {0}: keep <b>{1}</b><br>{2}").format(
					w["step"], frappe.utils.escape_html(w["keep"]), lines)):
			made += 1
	return made


@registry.executor("keep_subject")
def keep_subject(proposal, agent):
	"""The test ends: the step keeps the winning style only."""
	p = frappe.parse_json(proposal.payload or "{}")
	seq = frappe.get_doc("Excom AI Sequence", p.get("sequence") or proposal.subject_name)
	row = next((s for s in seq.steps if cint(s.step) == cint(p.get("step"))), None)
	if not row:
		raise registry.Cancel(_("the step is gone"))
	if p.get("keep") not in [ln.strip() for ln in (row.subject_test or "").splitlines()]:
		raise registry.Cancel(_("the step's subject lines changed since"))
	row.subject_test = p["keep"]
	seq.save(ignore_permissions=True)
	seq.add_comment("Comment", _("{0} (AI): subject-line test on step {1} ended; kept: {2}").format(
		agent.agent_name, row.step, frappe.utils.escape_html(p["keep"])))
	return ("Excom AI Sequence", seq.name)


# ── cost tuning ──────────────────────────────────────────────────────────────


def _last_run(eval_set: str) -> tuple:
	run = frappe.db.get_value("AI Eval Set", eval_set, "last_run")
	score, cost = frappe.db.get_value("AI Eval Run", run, ["score", "base_cost"]) if run else (0, 0)
	return run, score, cost


def cost_check(candidates: list[dict]) -> list[dict]:
	"""Score each {model, effort} on the Sales reply-intents set (it costs one model call per case
	each, so a person starts it). Returns them cheapest first, the passing ones marked; the first
	passing one is the recommendation."""
	from excom.excom_ai.builder.brief import ai_ready
	from excom.excom_ai.evals import SET
	from excom.excom_ai.monitor import _trial

	if not ai_ready() or not frappe.db.exists("AI Eval Set", SET):
		frappe.throw(_("No model provider or no evaluation set: nothing to compare."))
	from ai_interface.services import evals

	threshold = flt(frappe.db.get_value("AI Eval Set", SET, "pass_threshold"))
	out = []
	for c in candidates[:6]:
		model, effort = c.get("model") or None, c.get("effort") or None
		with _trial(model, effort):
			ok = evals.gate(SET, notes=_("cost check: model {0}, effort {1}").format(model or "-", effort or "-"))
		run, score, cost = _last_run(SET)
		out.append({"model": model, "effort": effort, "score": flt(score), "cost": flt(cost), "passes": bool(ok), "run": run})
	out.sort(key=lambda r: (not r["passes"], r["cost"]))
	if out and out[0]["passes"]:
		out[0]["recommended"] = True
	frappe.get_doc("AI Eval Set", SET).add_comment("Comment", _("Cost check (threshold {0}%): {1}").format(threshold, "; ".join(
		f"{r['model'] or 'default'}/{r['effort'] or 'default'} {round(r['score'])}% {r['cost']:.4f}" for r in out)))
	return out
