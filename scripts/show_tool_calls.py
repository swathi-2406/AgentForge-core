"""Summarize data/traces/tool_calls.jsonl.

    python scripts/show_tool_calls.py            # per-tool summary + last 10 calls
    python scripts/show_tool_calls.py RUN_ID     # one run
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tracing.tool_calls import read_tool_calls, tool_calls_path  # noqa: E402

rows = read_tool_calls(run_id=sys.argv[1] if len(sys.argv) > 1 else None)
print(f"{tool_calls_path()}: {len(rows)} calls\n")
by_tool = defaultdict(list)
for r in rows:
    by_tool[r["tool"]].append(r)
print(f"  {'tool':<26}{'calls':>6}{'errors':>8}{'avg ms':>10}{'max ms':>10}")
for tool, rs in sorted(by_tool.items()):
    ms = [r["latency_ms"] for r in rs]
    errs = sum(r["status"] == "error" for r in rs)
    print(f"  {tool:<26}{len(rs):>6}{errs:>8}{sum(ms) / len(ms):>10.1f}{max(ms):>10.1f}")
print("\nLast 10:")
for r in rows[-10:]:
    detail = f"{r['output']['bytes']:,} bytes" if r["status"] == "ok" else f"{r['error_type']}: {r['error'][:60]}"
    print(f"  {r['started_at'][11:23]}  {r['tool']:<26} {r['status']:<6} {r['latency_ms']:>8.1f} ms  {detail}")