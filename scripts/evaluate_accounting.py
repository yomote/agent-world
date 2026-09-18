"""Run one frozen accounting case without reading evaluator expectations."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps"))

from accounting_agent.controller import AccountingAgentController  # noqa: E402
from accounting_agent.provider import (  # noqa: E402
    CodexExecProvider,
    FixedWorkflowProvider,
    GlobalCallBudget,
)
from accounting_agent.store import RunStore  # noqa: E402
from world.accounting_simulator import AccountingSimulator  # noqa: E402

FIXTURES = {
    "known": ROOT / "apps/world/fixtures/accounting_known.json",
    "m8": ROOT / "apps/world/fixtures/accounting_holdout_m8_missing.json",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["agent", "baseline"], required=True)
    parser.add_argument("--fixture", choices=FIXTURES, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--model-budget", type=int, default=0)
    args = parser.parse_args()
    if args.mode == "agent" and not 1 <= args.model_budget <= 11:
        raise SystemExit("agent requires --model-budget 1..11 from the shared remaining budget")
    simulator = AccountingSimulator(FIXTURES[args.fixture])
    store = RunStore(args.database)
    provider = (
        CodexExecProvider(GlobalCallBudget(args.model_budget))
        if args.mode == "agent"
        else FixedWorkflowProvider()
    )
    controller = AccountingAgentController(simulator, store, provider)
    run_id = controller.start(args.mode)
    view = controller.view(run_id)
    for _step in range(11):
        if view["status"] != "running" or view["pending_question"]:
            break
        view = controller.advance(run_id, f"eval-{uuid4()}", view["step_version"])
    summary = {
        "run_id": run_id,
        "fixture_id": view["fixture_id"],
        "mode": view["mode"],
        "status": view["status"],
        "model_attempts": view["model_attempts"],
        "tool_calls": view["tool_calls"],
        "pending_question": view["pending_question"] is not None,
        "artifact_id": view["artifact"]["artifact_id"] if view["artifact"] else None,
        "artifact": view["artifact"]["payload"] if view["artifact"] else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
