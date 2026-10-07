# """Task planning logic that decomposes a request into executable steps."""
# """Planner: task + filing ids  ->  messages  ->  (task 3) LLM  ->  Plan.

#     build_messages(task, filing_ids)
#       ├── system:  role · rules · tool menu (built from REGISTRY, never hand-typed)
#       └── user:    task text · filing ids with their role (original / amendment)

# The planner never sees document text, only filing ids. That keeps the prompt
# small and means nothing inside a contract can steer the plan (Sprint 4).

# Preview the exact prompt without calling any LLM:
#     python -m agentforge_core.planner "Check for dangling cross-references" tva_facility_lease
# """

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from agentforge_core.plan import MAX_STEPS, planner_tool_names
from agentforge_core.tools import REGISTRY

# Bump whenever SYSTEM_TEMPLATE or the menu format changes. Logged with every
# plan, so traces from different prompt versions can be told apart in Tune.
PROMPT_VERSION = "planner-v1"

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "contracts" / "manifest.yaml"


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


# ---------------------------------------------------------------- preview

def _preview(argv: list[str]) -> None:
    if len(argv) < 2:
        print('usage: python -m agentforge_core.planner "<task>" <filing_id> [<filing_id> ...]')
        raise SystemExit(2)
    msgs = build_messages(argv[0], argv[1:])
    for m in msgs:
        print(f"===== {m['role'].upper()} =====\n{m['content']}\n")
    chars = sum(len(m["content"]) for m in msgs)
    print(f"===== {PROMPT_VERSION} · {len(planner_tool_names())} tools · "
          f"~{chars // 4} tokens =====")


if __name__ == "__main__":
    _preview(sys.argv[1:])