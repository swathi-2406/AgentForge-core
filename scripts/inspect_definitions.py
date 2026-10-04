"""Eyeball extract_definitions.

    python scripts/inspect_definitions.py tva_facility_lease                 # issues only
    python scripts/inspect_definitions.py tva_facility_lease "Basic Lease Rent"   # one term
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tools import call_tool  # noqa: E402

fid = sys.argv[1]
if len(sys.argv) > 2:
    out = call_tool("extract_definitions", {"filing_id": fid, "term": sys.argv[2]})
    for d in out.definitions:
        print(f"{d.term} {d.aliases or ''}  [{d.source}, scope={d.scope}, in {d.defined_in}, used {d.usage_count}x]")
        print(f"  points_to: {d.points_to}" if d.points_to else "", d.text[:800], sep="\n")
    for w in out.warnings:
        print("WARNING:", w)
    sys.exit()

out = call_tool("extract_definitions", {"filing_id": fid, "only_issues": True})
print(f"{fid}: {out.total_terms} terms  " + "  ".join(f"{k}={v}" for k, v in sorted(out.counts.items())))
print(f"\nNear-misses ({len(out.near_misses)}):")
for m in out.near_misses:
    print(f"  [{m.confidence:<6}] {m.kind:<16} '{m.phrase}' -> '{m.likely_term}'  {m.count}x in {', '.join(m.found_in)}")
    print(f"      ...{m.snippet[:130]}...")
twice = [d for d in out.duplicates if d.note == "defined_twice"]
print(f"\nDefined twice, worth a look ({len(twice)}):", ", ".join(f"{d.term} {d.defined_in}" for d in twice))
print(f"Restated in a glossary, normal ({len(out.duplicates) - len(twice)})")
print(f"\nNever used ({len(out.unused_terms)}):", ", ".join(out.unused_terms[:30]), "..." if len(out.unused_terms) > 30 else "")
print(f"\nGlued uses:", ", ".join(f"{d.term} ({d.glued_uses})" for d in out.definitions))