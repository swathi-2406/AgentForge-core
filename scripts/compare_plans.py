# """Day 4 task 4: compare saved plans and check the Definition of Done.

#     python -m scripts.compare_plans

# Reads every data/plans/*.json, re-validates it against today's tool registry,
# groups runs of the same task, and shows whether repeated runs agree.

#     Done = at least 2 tasks with 3+ runs each, and every plan still valid.
# """

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from agentforge_core.planner import PLANS_DIR, PlanResult

RUNS_NEEDED = 3
TASKS_NEEDED = 2


def load_all(plans_dir: Path = PLANS_DIR):
    """(path, PlanResult or None, error) for every saved plan, oldest first."""
    out = []
    for path in sorted(plans_dir.glob("*.json")):
        try:
            out.append((path, PlanResult.model_validate_json(path.read_text(encoding="utf-8")), None))
        except (ValidationError, json.JSONDecodeError) as e:
            out.append((path, None, str(e).splitlines()[0]))
    return out


def sequence(r: PlanResult) -> str:
    return " → ".join(s.tool_name for s in r.plan.steps)


def args_key(r: PlanResult) -> str:
    return json.dumps([(s.tool_name, s.tool_args) for s in r.plan.steps], sort_keys=True)


def main() -> None:
    con = Console()
    loaded = load_all()
    if not loaded:
        con.print(f"[yellow]No plans in {PLANS_DIR}. Run the planner with --run first.[/yellow]")
        raise SystemExit(1)

    invalid = [(p, err) for p, r, err in loaded if r is None]
    groups: dict[tuple, list[tuple[Path, PlanResult]]] = defaultdict(list)
    for p, r, _ in loaded:
        if r is not None:
            groups[(r.task, tuple(r.filing_ids))].append((p, r))

    for (task, ids), runs in groups.items():
        t = Table(title=f"{task}  [{', '.join(ids)}]", title_justify="left", show_lines=False)
        t.add_column("run", justify="right")
        t.add_column("tools")
        t.add_column("args same as run 1?")
        t.add_column("tokens", justify="right")
        t.add_column("ms", justify="right")
        first = args_key(runs[0][1])
        for i, (_, r) in enumerate(runs, 1):
            same = "—" if i == 1 else ("[green]yes[/green]" if args_key(r) == first else "[yellow]differs[/yellow]")
            tok = (r.call.get("prompt_tokens") or 0) + (r.call.get("completion_tokens") or 0)
            t.add_row(str(i), sequence(r), same, str(tok), str(r.call.get("latency_ms", "")))
        con.print(t)
        distinct = len({args_key(r) for _, r in runs})
        note = "all runs identical" if distinct == 1 else f"{distinct} different plans across {len(runs)} runs"
        con.print(f"  [dim]{note} · {runs[0][1].prompt_version}[/dim]\n")

    for p, err in invalid:
        con.print(f"[red]INVALID[/red] {p.name}: {err}")

    # ---- Definition of Done
    full = [k for k, runs in groups.items() if len(runs) >= RUNS_NEEDED]
    total_tokens = sum((r.call.get("prompt_tokens") or 0) + (r.call.get("completion_tokens") or 0)
                       for runs in groups.values() for _, r in runs)
    con.print(f"[bold]{sum(len(v) for v in groups.values())} valid plans[/bold], "
              f"{len(invalid)} invalid, {total_tokens:,} tokens total")
    if not invalid and len(full) >= TASKS_NEEDED:
        con.print("[green bold]Day 4 Definition of Done: met[/green bold]")
    else:
        need = []
        if invalid:
            need.append(f"{len(invalid)} invalid plan(s) to look at")
        for (task, ids), runs in groups.items():
            if len(runs) < RUNS_NEEDED:
                need.append(f"{RUNS_NEEDED - len(runs)} more run(s) of '{task[:40]}…'")
        if len(groups) < TASKS_NEEDED:
            need.append(f"{TASKS_NEEDED - len(groups)} more task(s)")
        con.print("[yellow]Not done yet:[/yellow] " + "; ".join(need))


if __name__ == "__main__":
    main()