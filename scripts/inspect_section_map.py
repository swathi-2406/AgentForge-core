"""Eyeball extract_section_map:  python scripts/inspect_section_map.py tva_facility_lease"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tools import call_tool  # noqa: E402

out = call_tool("extract_section_map", {"filing_id": sys.argv[1]})
print(f"{out.filing_id}: {out.section_count} entries")
for w in out.warnings:
    print(f"  WARNING: {w}")
for e in out.entries:
    subs = ",".join(e.subsections) or "-"
    gap = f"   <- skips {','.join(e.skipped_subsections)}" if e.skipped_subsections else ""
    print(f"  {e.number:<14} {e.heading[:40]:<40} {subs}{gap}")