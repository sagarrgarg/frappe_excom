"""What packs plug into the engine: task kinds, planners and executors.

    @task_kind("profile_company", priority=4, effort="write")
    def profile_company(task, agent) -> str: ...      # returns a one-line outcome

    @planner("sales")
    def plan(agent) -> list[dict]: ...                 # task specs for tasks.add()

    @executor("note")
    def add_note(proposal, agent) -> tuple[str, str] | None: ...   # what it created

    @responder("sales")
    def respond(task, agent) -> str: ...               # a buyer wrote (an `inbound` task)

Modules that register are listed in MODULES and imported once, on first use.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe

MODULES = [
	"excom.excom_ai.engine.builtin",
	"excom.excom_ai.engine.inbound",
]

# Priorities (HLD-007 §10.1): 0 inbound replies and events · 1 approved actions and due sends ·
# 2 checks · 3 writing · 4 research · 5 discovery · 6 knowledge refresh and coaching.
PRIORITY = {"inbound": 0, "send": 1, "check": 2, "write": 3, "research": 4, "discover": 5, "refresh": 6}


@dataclass
class TaskKind:
	kind: str
	handler: callable
	priority: int = 5
	on_hold: bool = False  # may still run while the agent is On Hold (bounces, opt-outs, out-of-office)
	outreach: bool = False  # starts contact with someone: never on quiet dates, never on hold
	effort: str = "write"  # classify | write | reason: which effort setting its AI calls use


TASK_KINDS: dict[str, TaskKind] = {}
PLANNERS: dict[str, list] = {}
EXECUTORS: dict[str, callable] = {}
RESPONDERS: dict[str, callable] = {}
_loaded = False


def task_kind(kind: str, priority: int = 5, on_hold: bool = False, outreach: bool = False, effort: str = "write"):
	def wrap(fn):
		TASK_KINDS[kind] = TaskKind(kind, fn, priority, on_hold, outreach, effort)
		return fn

	return wrap


def planner(*packs: str):
	def wrap(fn):
		for pack in packs:
			if fn not in PLANNERS.setdefault(pack, []):
				PLANNERS[pack].append(fn)
		return fn

	return wrap


def executor(action: str):
	def wrap(fn):
		EXECUTORS[action] = fn
		return fn

	return wrap


def responder(pack: str):
	def wrap(fn):
		RESPONDERS[pack] = fn
		return fn

	return wrap


def load():
	global _loaded
	if _loaded:
		return
	for module in MODULES:
		frappe.get_module(module)
	_loaded = True


def kind(name: str) -> TaskKind:
	load()
	if name not in TASK_KINDS:
		frappe.throw(f"Unknown Excom AI task kind: {name}")
	return TASK_KINDS[name]
