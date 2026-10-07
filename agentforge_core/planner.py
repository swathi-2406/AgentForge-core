# """Planner: task + filing ids  ->  messages  ->  LLM  ->  validated Plan.

#     make_plan(task, filing_ids)
#       ├── build_messages()     system (rules + tool menu) · user (task + ids)
#       ├── structured_call()    LLM, validated against Plan, 1 retry on bad output
#       └── PlanResult           plan + CallRecord + prompt version

# The planner never sees document text, only filing ids. That keeps the prompt
# small and means nothing inside a contract can steer the plan (Sprint 4).

#     python -m agentforge_core.planner "<task>" <filing_id> ...         preview, free
#     python -m agentforge_core.planner --run "<task>" <filing_id> ...   real call, saves
# """

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from instructor.core import InstructorRetryException
from pydantic import BaseModel, ConfigDict

from agentforge_core.llm import structured_call
from agentforge_core.plan import MAX_STEPS, Plan, planner_tool_names
from agentforge_core.tools import REGISTRY

# Bump whenever SYSTEM_TEMPLATE or the menu format changes. Logged with every
# plan, so traces from different prompt versions can be told apart in Tune.
PROMPT_VERSION = "planner-v1"

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "contracts" / "manifest.yaml"
PLANS_DIR = ROOT / "data" / "plans"


class PlannerError(Exception):
    """The planner could not produce a valid plan (bad input, or the LLM failed twice)."""


SYSTEM_TEMPLATE = """\
You are the planning component of a contract-review agent. You do not read \
contracts yourself. You decide which tools to run, in what order, to check \
SEC EDGAR filings for internal inconsistencies: cross-references to sections \
that don't exist, defined terms used inconsistently, dates that disagree, and \
amendments that contradict the agreement they amend.

Rules:
1. Use only the tools listed below. Arguments must match the tool's input \
schema exactly. Leave out optional arguments unless the task needs them.
2. Use only the filing ids listed in the task. Never invent a filing id or a URL.
3. One tool call per step. Number steps 1, 2, 3 in order. Keep the plan as \
short as the task allows: usually 2 to 6 steps, never more than {max_steps}.
4. Each expected_outcome must be checkable against the tool's output without \
judgment. Name the filing and the concrete result, for example "a section map \
for tva_facility_lease with at least one section". Never write "works", \
"success" or "returns data".
5. Plan data gathering only. Do not report findings. A later stage compares \
the results and records any inconsistencies.

Tools:
{tool_menu}"""

USER_TEMPLATE = """\
Task: {task}

Filings in scope:
{filings}

Return the plan."""


# ---------------------------------------------------------------- tool menu

def _compact_schema(node: Any, in_properties: bool = False) -> Any:
    """Drop Pydantic's auto 'title' keys. They repeat field names and waste tokens.

    A field that is literally named 'title' is kept: inside a 'properties'
    mapping, keys are field names, not schema keywords.
    """
    if isinstance(node, dict):
        return {
            k: _compact_schema(v, in_properties=(k == "properties"))
            for k, v in node.items()
            if in_properties or k != "title"
        }
    if isinstance(node, list):
        return [_compact_schema(v) for v in node]
    return node


def build_tool_menu() -> str:
    """One block per plannable tool: name, description, input schema, output fields."""
    blocks = []
    for name in planner_tool_names():
        spec = REGISTRY[name].spec()
        schema = json.dumps(_compact_schema(spec["input_schema"]), separators=(",", ":"))
        returns = ", ".join(spec["output_schema"].get("properties", {})) or "(see schema)"
        blocks.append(
            f"### {name}\n{spec['description']}\ninput: {schema}\nreturns: {returns}"
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- filings

@lru_cache(maxsize=None)
def _manifest(path: Path = MANIFEST_PATH) -> dict[str, dict]:
    """Manifest entries by id. Handles a plain list or a top-level 'filings:' key."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = data.get("filings", []) if isinstance(data, dict) else data
    return {e["id"]: e for e in entries}


def describe_filing(filing_id: str) -> str:
    """'redwire_credit_amend1 (amendment of redwire_credit_original)'"""
    entry = _manifest()[filing_id]
    if entry.get("role") == "amendment" and entry.get("amends"):
        return f"- {filing_id} (amendment of {entry['amends']})"
    if entry.get("role") == "original":
        return f"- {filing_id} (original agreement)"
    return f"- {filing_id}"


# ---------------------------------------------------------------- messages

def build_messages(task: str, filing_ids: list[str]) -> list[dict[str, str]]:
    """The full planner prompt. Pure function: same inputs, same messages."""
    task = task.strip()
    if not task:
        raise PlannerError("Task text is empty.")
    if not filing_ids:
        raise PlannerError("At least one filing id is required.")
    unknown = [f for f in filing_ids if f not in _manifest()]
    if unknown:
        raise PlannerError(
            f"Unknown filing id(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(_manifest()))}"
        )

    system = SYSTEM_TEMPLATE.format(max_steps=MAX_STEPS, tool_menu=build_tool_menu())
    user = USER_TEMPLATE.format(
        task=task, filings="\n".join(describe_filing(f) for f in filing_ids)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- the call

class PlanResult(BaseModel):
    """A plan plus everything needed to trust and reproduce it later."""

    model_config = ConfigDict(extra="forbid")

    task: str
    filing_ids: list[str]
    prompt_version: str
    plan: Plan
    call: dict[str, Any]  # CallRecord.as_dict(): model, tokens, latency, timestamp


def _last_error(e: InstructorRetryException) -> str:
    """The validation error from the final attempt, in one readable string."""
    attempts = getattr(e, "failed_attempts", None) or []
    if attempts and getattr(attempts[-1], "exception", None) is not None:
        return str(attempts[-1].exception)
    return str(e)


def make_plan(task: str, filing_ids: list[str], *, client: Any = None) -> PlanResult:
    """Ask the LLM for a plan. Returns a valid PlanResult or raises PlannerError.

    Two levels of failure, handled differently:
      bad input (empty task, unknown filing)  -> PlannerError before any LLM call
      bad output twice (invalid plan)          -> PlannerError with the last reason
    Network and config errors pass through unchanged, so they aren't mistaken
    for planning failures.
    """
    messages = build_messages(task, filing_ids)
    try:
        plan, record = structured_call(
            messages,
            Plan,
            max_retries=1,
            validation_context={"filing_ids": set(filing_ids)},
            client=client,
        )
    except InstructorRetryException as e:
        raise PlannerError(
            f"No valid plan after {getattr(e, 'n_attempts', 2)} attempts. "
            f"Last problem: {_last_error(e)}"
        ) from e
    return PlanResult(
        task=task.strip(),
        filing_ids=list(filing_ids),
        prompt_version=PROMPT_VERSION,
        plan=plan,
        call=record.as_dict(),
    )


def save_plan(result: PlanResult, out_dir: Path = PLANS_DIR) -> Path:
    """data/plans/20261007T142233Z_tva_facility_lease.json"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9_]+", "_", result.filing_ids[0].lower())
    path = out_dir / f"{stamp}_{slug}.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- CLI

def _show_prompt(task: str, filing_ids: list[str]) -> None:
    msgs = build_messages(task, filing_ids)
    for m in msgs:
        print(f"===== {m['role'].upper()} =====\n{m['content']}\n")
    chars = sum(len(m["content"]) for m in msgs)
    print(f"===== {PROMPT_VERSION} · {len(planner_tool_names())} tools · "
          f"~{chars // 4} tokens =====")


def _show_plan(result: PlanResult, path: Path) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"Plan for: {result.task}", show_lines=True)
    table.add_column("#", justify="right")
    table.add_column("tool")
    table.add_column("args")
    table.add_column("expected outcome")
    for st in result.plan.steps:
        table.add_row(str(st.step_id), st.tool_name,
                      json.dumps(st.tool_args), st.expected_outcome)
    c = result.call
    con = Console()
    con.print(table)
    con.print(f"[dim]{c['served_model']} · {c['prompt_tokens']}+{c['completion_tokens']} tokens "
              f"· {c['latency_ms']} ms · {result.prompt_version}[/dim]")
    con.print(f"[green]Saved:[/green] {path}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m agentforge_core.planner")
    ap.add_argument("--run", action="store_true", help="call the LLM and save the plan")
    ap.add_argument("task")
    ap.add_argument("filing_ids", nargs="+")
    args = ap.parse_args(argv)

    if not args.run:
        _show_prompt(args.task, args.filing_ids)
        return
    try:
        result = make_plan(args.task, args.filing_ids)
    except PlannerError as e:
        print(f"PlannerError: {e}")
        raise SystemExit(1)
    _show_plan(result, save_plan(result))


if __name__ == "__main__":
    main()