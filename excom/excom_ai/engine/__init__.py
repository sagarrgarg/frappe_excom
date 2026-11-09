"""The Excom AI engine (HLD-007 §10, §12): one engine for every agent type.

    orchestrator.tick()   every 2 minutes; does nothing at all while the master switch is off
      ├─ ownership.evaluate(agent)   who is in charge; holds an agent with nobody behind it
      ├─ planners (per pack)        add tasks for subjects that need work
      └─ dispatch                   start due tasks, taking turns between agents, within limits
    runner.run_task(task)  as the agent's own user: gates, the kind's handler, retries, holds
      └─ runner.ai(...)      every AI call, through ai_interface, charged to the agent's budget
    proposals.propose(...)  Off / Shadow / Review / Auto under the agent's policy and ceilings
      └─ proposals.execute()  the action's executor, as the agent's user, once

Packs (sales, accounts, …) register their task kinds, planners and executors in registry.py.
"""
