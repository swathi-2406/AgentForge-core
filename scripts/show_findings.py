"""Show recorded findings and check nobody edited or deleted any.

    python scripts/show_findings.py              # all findings
    python scripts/show_findings.py RUN_ID       # one run
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core.tools.flag_inconsistency import findings_path, read_findings, verify_findings  # noqa: E402

findings = read_findings(run_id=sys.argv[1] if len(sys.argv) > 1 else None)
ok, bad_line = verify_findings()
print(f"{findings_path()}: {len(findings)} findings  |  integrity: "
      + ("OK" if ok else f"BROKEN at line {bad_line} (a finding was edited or removed)"))
for f in findings:
    loc = f.location + ("" if f.location_verified else " (?)")
    print(f"  [{f.severity:<6}] {f.type:<20} {f.filing_id} {loc:<12} {f.description[:90]}")