"""Eyeball how read_document split a filing.

    python scripts/inspect_filing.py tva_facility_lease
    python scripts/inspect_filing.py tva_facility_lease 13.2     # print one section's text
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tools.read_document import load_document  # noqa: E402

doc = load_document(sys.argv[1])
if len(sys.argv) > 2:
    s = doc.section(sys.argv[2])
    print(s.text if s else f"No section {sys.argv[2]}")
    sys.exit()

print(f"{doc.filing_id}: {len(doc.text):,} chars, {len(doc.sections)} sections, "
      f"{doc.toc_entries_skipped} TOC entries skipped")
for w in doc.warnings:
    print(f"  WARNING: {w}")
for s in doc.sections:
    print(f"  {s.number:<14} {s.heading[:50]:<50} {len(s.text):>7,} chars")