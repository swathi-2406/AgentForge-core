"""Print the raw HTML around a word, to debug parsing.

    python scripts/show_raw_html.py tva_facility_lease Prudent
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tools.read_document import resolve_filing_path  # noqa: E402

fid, word = sys.argv[1], sys.argv[2]
raw = resolve_filing_path(fid).read_bytes().decode("utf-8", errors="replace")
i = raw.find(word)
if i < 0:
    print(f"'{word}' not found in the raw file")
else:
    print(repr(raw[max(0, i - 250):i + 60]))