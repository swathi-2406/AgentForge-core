"""Day 2 definition of done, on your real filings: every tool, by name, on every filing.

    python scripts/day2_check.py

Findings and traces from this check go to a temporary folder, not data/traces.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["AGENTFORGE_TRACES_DIR"] = tempfile.mkdtemp(prefix="day2_check_")

from agentforge_core import edgar  # noqa: E402
from agentforge_core.tools import REGISTRY, call_tool, list_tool_specs  # noqa: E402

filings = [e for e in edgar.load_manifest() if e.get("local_path") and (edgar.get_root() / e["local_path"]).exists()]
tools = sorted(REGISTRY)
print(f"Registry: {len(tools)} tools  |  Filings on disk: {len(filings)}\n")

width = 15
print(f"  {'tool':<26}" + "".join(f"{e['id'][:width - 1]:>{width}}" for e in filings))
failures = 0
for tool in tools:
    cells = []
    for e in filings:
        args = {"filing_id": e["id"]}
        if tool == "flag_inconsistency":
            args.update(location="preamble", type="other", severity="low", description="Day 2 check, safe to ignore.")
        if tool == "fetch_related_filing" and not e.get("amends"):
            cells.append("n/a")
            continue
        start = time.perf_counter()
        try:
            call_tool(tool, args)
            cells.append(f"ok {(time.perf_counter() - start) * 1000:.0f}ms")
        except Exception as ex:  # noqa: BLE001
            failures += 1
            cells.append(f"FAIL {type(ex).__name__[:6]}")
    print(f"  {tool:<26}" + "".join(f"{c:>{width}}" for c in cells))

print("\nPlanner-ready schemas:")
for s in list_tool_specs():
    params = ", ".join(s["input_schema"]["properties"])
    print(f"  {s['name']:<26} ({params})")

print("\nDay 2 done." if failures == 0 else f"\n{failures} failures -- not done yet.")
sys.exit(1 if failures else 0)