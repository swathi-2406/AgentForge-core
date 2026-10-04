"""Eyeball extract_cross_references.

    python scripts/inspect_cross_refs.py tva_facility_lease            # counts + every problem
    python scripts/inspect_cross_refs.py tva_facility_lease 15.1       # every reference made from 15.1
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tools import call_tool  # noqa: E402

fid = sys.argv[1]
only_from = sys.argv[2] if len(sys.argv) > 2 else None
out = call_tool("extract_cross_references", {"filing_id": fid, "limit": 5000})
print(f"{fid}: {out.total} references  " + "  ".join(f"{k}={v}" for k, v in sorted(out.counts.items())))

if only_from:
    rows = [r for r in out.references if r.found_in == only_from]
else:
    rows = [r for r in out.references if r.status not in ("ok", "external")]
    print("Problems only (ok and external hidden):")

for r in rows:
    clauses = "(" + ")(".join(r.clauses) + ")" if r.clauses else ""
    print(f"  {r.status:<18} in {r.found_in:<10} -> {r.target}{clauses}")
    print(f"      ...{r.snippet[:140]}...")