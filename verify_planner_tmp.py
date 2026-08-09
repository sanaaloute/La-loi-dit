"""Check the planner decomposes dynamically, not from hardcoded lists."""
import asyncio, sys
from backend.core.context import build_context
from backend.planner.agent import PlannerAgent
from backend.workflows.graph import initial_state

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUERIES = [
    "Quels sont les droits d'un locataire au Burkina Faso ?",  # NOT in hardcoded expansions
    "Comment créer une SARL au Burkina Faso ?",                # NOT in hardcoded expansions
]

async def main():
    ctx = await build_context()
    agent = PlannerAgent()
    for q in QUERIES:
        result = await agent.run(initial_state(q), ctx)
        plan = result.get("plan")
        print(f"Q: {q}")
        if plan is None:
            print("  (fallback - no plan)")
            continue
        for sq in plan.sub_questions:
            print(f"  - {sq}")
        print(f"  tasks: {len(plan.tasks)}, domains: {plan.legal_domains}")
        print()

asyncio.run(main())
