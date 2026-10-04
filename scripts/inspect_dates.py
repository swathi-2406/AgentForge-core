"""Eyeball extract_dates.

    python scripts/inspect_dates.py redwire_credit_original            # summary + issues
    python scripts/inspect_dates.py redwire_credit_original 2.02       # every date/period in 2.02
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tools import call_tool  # noqa: E402

fid = sys.argv[1]
args = {"filing_id": fid, "limit": 5000}
if len(sys.argv) > 2:
    args["section"] = sys.argv[2]
out = call_tool("extract_dates", args)
print(f"{fid}: {out.total} mentions  " + "  ".join(f"{k}={v}" for k, v in sorted(out.counts.items())))

if len(sys.argv) > 2:
    for m in out.mentions:
        print(f"  {m.kind:<8} {m.value:<16} {m.text!r}")
        print(f"      ...{m.snippet[:150]}...")
    sys.exit()

print("\nMost frequent dates:")
for s in sorted(out.dates, key=lambda s: -s.count)[:10]:
    print(f"  {s.value:<12} {s.count:>3}x  in {', '.join(s.sections[:6])}{' ...' if len(s.sections) > 6 else ''}")
issues = [m for m in out.mentions if m.issue]
print(f"\nIssues ({len(issues)}):")
for m in issues:
    print(f"  {m.issue:<16} in {m.section:<10} {m.text!r}")
    print(f"      ...{m.snippet[:150]}...")