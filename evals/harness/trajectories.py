"""Turn a raw run trace (trace_writer's JSON) into a validated Trajectory (schema.py, v1).

    python -m evals.harness.trajectories --latest     # convert and check the newest run
    python -m evals.harness.trajectories --all        # backfill: convert every run that can be converted

trace_writer keeps writing its event log exactly as before (step records + critique, retry,
finding, give_up, final events). This module is the ONE place that reads that log and produces
the locked shape, so the agent's tracing never has to know about the eval schema.

Converted files go to data/traces/trajectories/<run_id>.json. Runs that can't be converted
(still running, crashed mid-write, or missing fields from before Day 6) are listed with the reason
and left untouched. They predate the schema; Day 9's eval runs regenerate everything anyway.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import ValidationError

from agentforge_core.tracing import trace_writer
from evals.harness.schema import PREVIEW_CHARS, Trajectory

NOT_RECORDED = "(not recorded)"


def trajectories_dir() -> Path:
    return trace_writer.run_json_path("x").parent.parent / "trajectories"


def _preview(obs: dict[str, Any]) -> str:
    for key in ("output_preview", "preview"):
        if isinstance(obs.get(key), str):
            return obs[key][:PREVIEW_CHARS]
    raw = obs.get("raw_output", obs.get("output"))
    if raw is None:
        return ""
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    return text[:PREVIEW_CHARS]


def _step_record(entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Accept both shapes trace_writer has used: {"step": {...}, "observation": {...}} or flat."""
    if "step" in entry and "observation" in entry:
        return entry["step"], entry["observation"]
    return entry, entry


def _seconds(start: Optional[str], end: Optional[str]) -> Optional[float]:
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return (b - a).total_seconds()
    except (TypeError, ValueError):
        return None


def to_trajectory(run: Union[str, dict[str, Any]], task_id: Optional[str] = None) -> Trajectory:
    """Build and validate a Trajectory from a run id or an already-loaded run dict."""
    data = trace_writer.read_run(run) if isinstance(run, str) else run
    status = data.get("status")
    if status not in ("completed", "failed"):
        raise ValueError(f"run {data.get('run_id')} has status {status!r}: only finished runs convert")

    events = data.get("events") or []
    by_kind = lambda k: [e for e in events if e.get("kind") == k]   # noqa: E731
    critiques = {(e["step_id"], e["attempt"]): e for e in by_kind("critique")}
    retries = {(e["step_id"], e["attempt"]): e for e in by_kind("retry")}
    final = next(iter(by_kind("final")), {})

    plan = [{k: p[k] for k in ("step_id", "tool_name", "tool_args", "expected_outcome") if k in p}
            for p in data.get("plan") or []]
    expected_by_step = {p["step_id"]: p.get("expected_outcome") for p in plan}
    final_plan = {p["step_id"]: p for p in final.get("final_plan") or []}

    steps: dict[int, list[dict[str, Any]]] = {}
    for entry in data.get("steps") or []:
        step, obs = _step_record(entry)
        sid = step.get("step_id", entry.get("step_id"))
        n = entry.get("attempt", step.get("attempt", 1))
        tool = step.get("tool_name") or obs.get("tool_name")
        fp = final_plan.get(sid, {})
        expected = (step.get("expected_outcome")
                    or (fp.get("expected_outcome") if fp.get("tool_name") == tool else None)
                    or expected_by_step.get(sid) or NOT_RECORDED)
        crit = critiques.get((sid, n))
        retry = retries.get((sid, n))
        steps.setdefault(sid, []).append({
            "attempt": n,
            "tool_call": {"tool_name": tool, "tool_args": step.get("tool_args", {})},
            "expected_outcome": expected,
            "observation": {
                "status": obs.get("status"),
                "latency_ms": float(obs.get("latency_ms", 0.0)),
                "output_preview": _preview(obs),
                "output_bytes": obs.get("output_bytes"),
                "error_kind": obs.get("error_kind"),
                "error_type": obs.get("error_type"),
                "empty_reason": obs.get("empty_reason"),
            },
            "critique": None if crit is None else {
                "verdict": crit["verdict"], "reasoning": crit.get("reasoning") or NOT_RECORDED,
                "confidence": float(crit.get("confidence", 0.0)), "source": crit.get("source", "llm")},
            "retry_reason": None if n == 1 else (retry or {}).get("why_different") or NOT_RECORDED,
        })

    if not plan:   # Day 5 free runs started without a plan: rebuild it from the first attempts
        plan = [{"step_id": sid, "tool_name": a[0]["tool_call"]["tool_name"],
                 "tool_args": a[0]["tool_call"]["tool_args"], "expected_outcome": a[0]["expected_outcome"]}
                for sid, a in sorted(steps.items())]

    findings = []
    for i, e in enumerate(by_kind("finding"), 1):
        findings.append({
            "finding_id": str(e.get("finding_id") or f"{data['run_id']}-f{i}"),
            "type": e.get("type"), "location": str(e.get("location") or NOT_RECORDED),
            "filing_id": e.get("filing_id"), "description": e.get("description") or NOT_RECORDED,
            "severity": str(e.get("severity") or "unspecified"), "step_id": e.get("step_id"),
            "suppressed": bool(e.get("suppressed", False)),
        })

    step_list = [{"step_id": sid, "attempts": sorted(a, key=lambda x: x["attempt"])}
                 for sid, a in sorted(steps.items())]
    step_ms = sum(a["observation"]["latency_ms"] for s in step_list for a in s["attempts"])
    wall = _seconds(data.get("started_at"), data.get("finished_at"))
    give_up = next(iter(by_kind("give_up")), {})

    return Trajectory.model_validate({
        "run_id": data["run_id"],
        "task_id": task_id,
        "task": data.get("task") or NOT_RECORDED,
        "filings": data.get("filing_ids") or data.get("filings") or [],
        "plan": plan,
        "steps": step_list,
        "findings": findings,
        "final_status": status,
        "give_up_reason": give_up.get("reason") or (NOT_RECORDED if status == "failed" else None),
        "started_at": str(data.get("started_at") or NOT_RECORDED),
        "finished_at": str(data.get("finished_at") or NOT_RECORDED),
        "total_latency_ms": max(step_ms, (wall or 0) * 1000),
        "total_retries": sum(len(s["attempts"]) - 1 for s in step_list),
    })


def save(t: Trajectory) -> Path:
    out = trajectories_dir() / f"{t.run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(t.model_dump_json(indent=2), encoding="utf-8")
    return out


def _runs() -> list[Path]:
    return sorted(trace_writer.run_json_path("x").parent.glob("*.json"), key=lambda p: p.stat().st_mtime)


def _why(e: Exception) -> str:
    if isinstance(e, ValidationError):
        first = e.errors()[0]
        return f"{'.'.join(map(str, first['loc']))}: {first['msg']} (+{e.error_count() - 1} more)"
    return f"{type(e).__name__}: {e}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--latest", action="store_true", help="convert the newest run")
    g.add_argument("--all", action="store_true", help="backfill every run that converts")
    g.add_argument("--run", help="convert one run id")
    a = ap.parse_args()

    runs = _runs()
    if not runs:
        sys.exit("No runs found. Run an agent task first.")
    chosen = runs if a.all else [runs[-1]] if a.latest else [trace_writer.run_json_path(a.run)]

    ok = 0
    for path in chosen:
        run_id = path.stem
        version = json.loads(path.read_text(encoding="utf-8")).get("schema_version", "?")
        try:
            t = to_trajectory(run_id)
            save(t)
            ok += 1
            print(f"  ok    {run_id}  [{version}]  steps={len(t.steps)}  retries={t.total_retries}  "
                  f"findings={len(t.findings)}  {t.final_status}")
        except Exception as e:  # noqa: BLE001  (one bad run must not hide the rest)
            print(f"  skip  {run_id}  [{version}]  {_why(e)}")
    print(f"{ok} of {len(chosen)} converted -> {trajectories_dir()}")


if __name__ == "__main__":
    main()