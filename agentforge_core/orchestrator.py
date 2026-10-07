# """Top-level orchestrator coordinating planner, executor, and critic loops."""
# """Orchestrator: run a whole plan, step by step, inside one traced run.

# Day 5 version (no critic, no retry yet):

#     start_run ──► for each step: execute_and_record ──► finish_run

# Day 6 adds the critique → retry branch inside the loop. The shape stays the same.

#     python -m agentforge_core.orchestrator data/plans/<file>.json     run a saved plan
#     python -m agentforge_core.orchestrator --latest tva_facility_lease run the newest plan for a filing

# No LLM calls here: plans come from the planner (Day 4), which saves them to data/plans/.
# """

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentforge_core.executor import Observation, execute_and_record
from agentforge_core.plan import PlannedStep
from agentforge_core.planner import PLANS_DIR, PlanResult
from agentforge_core.tracing import trace_writer


class RunResult(BaseModel):
    """What one run produced. The full detail lives in the trace."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str  # completed | failed
    observations: list[Observation]

    def count(self, status: str) -> int:
        return sum(o.status == status for o in self.observations)


def run_plan(task: str, filing_ids: list[str], steps: list[PlannedStep]) -> RunResult:
    """Execute every step in order and trace the whole run.

    Without a critic there's nothing to retry with, so a failing step doesn't stop the run.
    Later steps still run (each tool works on the filing on its own), and the run is
    marked 'failed' if any step errored. 'empty' steps don't fail the run: deciding
    whether an empty result is acceptable is the critic's job on Day 6.
    """
    run_id = trace_writer.start_run(task, filing_ids, plan=steps)
    observations = [execute_and_record(run_id, step) for step in steps]
    status = "failed" if any(o.status == "error" for o in observations) else "completed"
    trace_writer.finish_run(run_id, status)
    return RunResult(run_id=run_id, status=status, observations=observations)


def load_plan(path: Path) -> PlanResult:
    return PlanResult.model_validate_json(path.read_text(encoding="utf-8"))


def run_saved_plan(path: Path) -> RunResult:
    saved = load_plan(path)
    return run_plan(saved.task, saved.filing_ids, saved.plan.steps)


def latest_plan_for(filing_id: str, plans_dir: Path = PLANS_DIR) -> Path:
    """Newest saved plan whose first filing is filing_id (file names start with a UTC timestamp)."""
    matches = sorted(plans_dir.glob(f"*_{filing_id}.json"))
    if not matches:
        raise SystemExit(f"No saved plan for '{filing_id}' in {plans_dir}. "
                         f"Make one with: python -m agentforge_core.planner --run \"<task>\" {filing_id}")
    return matches[-1]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m agentforge_core.orchestrator")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("plan_path", nargs="?", type=Path, help="a saved plan JSON")
    group.add_argument("--latest", metavar="FILING_ID", help="run the newest saved plan for this filing")
    args = ap.parse_args(argv)

    path = args.plan_path or latest_plan_for(args.latest)
    result = run_saved_plan(path)
    print(f"plan: {path.name}")
    print(f"run:  {result.run_id}  [{result.status}]  "
          f"ok={result.count('ok')} empty={result.count('empty')} error={result.count('error')}")
    print("details: python -m scripts.show_run")


if __name__ == "__main__":
    main()