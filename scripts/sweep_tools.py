"""Day 3 hand sweep: run every tool on every filing once, save the output, show a grid.

    python scripts/sweep_tools.py                         # full sweep
    python scripts/sweep_tools.py --filing ford_arr_2026b # one row
    python scripts/sweep_tools.py --tool extract_dates    # one column

Each result is saved to data/sweep/<filing_id>/<tool>.json for you to read.
The script also checks that your known findings show up in the output.
A miss means "go look", not "the tool is broken": the strings below are search hints.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentforge_core.tools import REGISTRY, call_tool  # noqa: E402

MANIFEST = ROOT / "data" / "contracts" / "manifest.yaml"
OUT_DIR = ROOT / "data" / "sweep"

TOOLS = ["read_document", "extract_definitions", "extract_cross_references", "extract_section_map",
         "extract_dates", "fetch_related_filing", "flag_inconsistency"]
SHORT = {"read_document": "read", "extract_definitions": "defs", "extract_cross_references": "xrefs",
         "extract_section_map": "secmap", "extract_dates": "dates", "fetch_related_filing": "fetch",
         "flag_inconsistency": "flag"}

# Known findings from Days 1-2: (filing, tool) -> text that should appear in that tool's output.
# Edit freely as you confirm things in task 4.
EXPECT = {
    ("tva_facility_lease", "extract_cross_references"): ["13.2(d)"],
    ("tva_facility_lease", "extract_definitions"): ["Supplement Lease Rent", "Indenture Trustee", "Discount Value"],
    ("ford_arr_2026b", "extract_definitions"): ["Review Receivable"],
    ("ford_arr_2026b", "extract_section_map"): ["Schedule A", "Schedule B"],
    ("redwire_credit_original", "extract_cross_references"): ["1.1"],
    ("redwire_credit_original", "extract_definitions"): ["Basket", "Securitization Repurchasing Obligations"],
    ("redwire_credit_amend1", "extract_dates"): ["March 30, 2021", "March 31, 2021"],
    ("redwire_credit_amend1", "fetch_related_filing"): ["redwire_credit_original"],
}


def load_filings() -> list[dict]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or []
    return data.get("filings", []) if isinstance(data, dict) else data


def applies(tool: str, filing: dict) -> bool:
    """fetch_related_filing only makes sense for an amendment."""
    return tool != "fetch_related_filing" or bool(filing.get("amends"))


def args_for(tool: str, filing_id: str) -> dict:
    if tool == "flag_inconsistency":
        return {"filing_id": filing_id, "location": "preamble", "type": "other", "severity": "low",
                "description": "Day 3 sweep test finding, safe to ignore."}
    return {"filing_id": filing_id}


def to_json(out) -> dict:
    return out.model_dump(mode="json") if hasattr(out, "model_dump") else out


def summarize(d: dict) -> str:
    """One line: list/dict fields as counts, long text as length, short values as-is."""
    parts = []
    for k, v in d.items():
        if isinstance(v, (list, dict)):
            parts.append(f"{k}={len(v)}")
        elif isinstance(v, str):
            parts.append(f"{k}={len(v):,}ch" if len(v) > 30 else f"{k}={v!r}")
        elif v is not None:
            parts.append(f"{k}={v}")
    line = "  ".join(parts)
    return line if len(line) <= 90 else line[:87] + "..."


def run_cell(filing_id: str, tool: str) -> tuple[str, str]:
    """Returns (mark, detail). Mark: ok, MISS (known finding not seen), ERR."""
    start = time.perf_counter()
    try:
        data = to_json(call_tool(tool, args_for(tool, filing_id)))
    except Exception as e:  # noqa: BLE001  -- a sweep reports, it doesn't stop
        return "ERR", f"{type(e).__name__}: {str(e).splitlines()[0][:80]}"
    ms = (time.perf_counter() - start) * 1000

    path = OUT_DIR / filing_id / f"{tool}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")

    missing = [s for s in EXPECT.get((filing_id, tool), []) if s.lower() not in text.lower()]
    detail = f"{ms:6.0f} ms  {summarize(data)}"
    if missing:
        return "MISS", detail + f"\n{'':32}not found: {missing}"
    return "ok", detail


def main() -> int:
    # Windows consoles default to cp1252, which can't print ✓ or contract text like "§".
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--filing", help="only this filing id")
    p.add_argument("--tool", help="only this tool")
    a = p.parse_args()

    missing_tools = set(TOOLS) - set(REGISTRY)
    if missing_tools:
        print(f"Not registered: {sorted(missing_tools)}")
        return 1

    filings = [f for f in load_filings() if not a.filing or f["id"] == a.filing]
    tools = [t for t in TOOLS if not a.tool or t == a.tool]
    if not filings or not tools:
        print("Nothing to run. Check the --filing / --tool name.")
        return 1

    grid: dict[tuple[str, str], str] = {}
    for f in filings:
        print(f"\n{f['id']}  ({f.get('tier', '?')})")
        for t in tools:
            if not applies(t, f):
                grid[f["id"], t] = "-"
                continue
            mark, detail = run_cell(f["id"], t)
            grid[f["id"], t] = mark
            print(f"  {mark:<4} {t:<26}{detail}")

    symbol = {"ok": "✓", "MISS": "?", "ERR": "✗", "-": "–"}
    print("\n" + " " * 26 + "".join(f"{SHORT[t]:>8}" for t in tools))
    for f in filings:
        print(f"{f['id']:<26}" + "".join(f"{symbol[grid[f['id'], t]]:>8}" for t in tools))
    print("\n✓ ran, known findings seen   ? ran, a known finding not seen   ✗ error   – not applicable")
    print(f"Outputs: {OUT_DIR.relative_to(ROOT)}/<filing>/<tool>.json")
    return 1 if "ERR" in grid.values() else 0


if __name__ == "__main__":
    raise SystemExit(main())