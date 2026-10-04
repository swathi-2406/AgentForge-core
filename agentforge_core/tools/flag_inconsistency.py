# """Tool for flagging inconsistencies found across document content."""
# """flag_inconsistency: the agent's only way to record a finding.

# Every call is appended to data/traces/findings.jsonl and there is NO function to edit or delete a
# finding. Each line also carries a hash of the previous line (a hash chain), so if anyone removes or
# edits a line afterwards, verify_findings() points at where the chain breaks. This is what Sprint 4's
# finding_integrity_guard builds on: suppressing a finding can't be silent.

# A finding is recorded even if its location can't be matched to a section (location_verified=False).
# Refusing to record would be a way to lose findings.
# """

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agentforge_core.paths import get_root
from agentforge_core.tools.base import Tool, ToolInput, ToolOutput, register_tool
from agentforge_core.tools.read_document import load_document, manifest_entry

FindingType = Literal[
    "dangling_reference",     # Section 1.1 doesn't exist
    "wrong_reference",        # reference resolves, but to the wrong clause/schedule (TVA 15.1 -> 13.2(d))
    "term_mismatch",          # "Supplement Lease Rent" vs defined "Supplemental Lease Rent"
    "undefined_term",
    "duplicate_definition",
    "date_inconsistency",     # "March 30, 2021" lead-in vs March 31 table
    "number_mismatch",        # "sixty (90) days"
    "amendment_conflict",     # amendment contradicts the original
    "other",
]
Severity = Literal["low", "medium", "high"]
GENESIS = "0" * 64


def findings_path() -> Path:
    """AGENTFORGE_TRACES_DIR overrides the folder (tests use this)."""
    folder = os.getenv("AGENTFORGE_TRACES_DIR")
    return (Path(folder) if folder else get_root() / "data" / "traces") / "findings.jsonl"


class Finding(BaseModel):
    finding_id: str
    recorded_at: str
    run_id: Optional[str]
    filing_id: str
    related_filing_id: Optional[str]
    location: str
    location_verified: bool
    type: FindingType
    severity: Severity
    description: str
    evidence: Optional[str]
    prev_hash: str
    hash: str

# ---------- append-only store ----------

def _line_hash(prev_hash: str, record: dict) -> str:
    body = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def _last_hash(path: Path) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return GENESIS
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 8192))
        last = [line for line in f.read().splitlines() if line.strip()][-1]
    return json.loads(last)["hash"]


def _append(record: dict) -> Finding:
    path = findings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record["prev_hash"] = _last_hash(path)
    record["hash"] = _line_hash(record["prev_hash"], {k: v for k, v in record.items() if k != "hash"})
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return Finding(**record)


def read_findings(run_id: Optional[str] = None, filing_id: Optional[str] = None) -> list[Finding]:
    path = findings_path()
    if not path.exists():
        return []
    out = [Finding(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [f for f in out if (run_id is None or f.run_id == run_id) and (filing_id is None or f.filing_id == filing_id)]


def verify_findings() -> tuple[bool, Optional[int]]:
    """(True, None) if untouched; (False, line_number) at the first edited, removed or reordered line."""
    path = findings_path()
    if not path.exists():
        return True, None
    prev = GENESIS
    for n, line in enumerate((l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()), start=1):
        rec = json.loads(line)
        claimed = rec.pop("hash", None)
        if rec.get("prev_hash") != prev or _line_hash(prev, rec) != claimed:
            return False, n
        prev = claimed
    return True, None

# ---------- location check ----------

def verify_location(filing_id: str, location: str) -> bool:
    """'13.2(d)', 'Section 13.2', 'Article II', 'Schedule B', 'Appendix A' -> does that section exist?"""
    base = re.sub(r"^(?:Section|§)\s*", "", location.strip(), flags=re.I).split("(")[0].strip().rstrip(".")
    numbers = {s.number.lower() for s in load_document(filing_id).sections}
    return base.lower() in numbers

# ---------- the tool ----------

class FlagInconsistencyInput(ToolInput):
    filing_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="Filing the finding is in")
    location: str = Field(min_length=1, max_length=120, description='Where: "13.2", "15.1", "Appendix A", "Article II"')
    type: FindingType
    severity: Severity
    description: str = Field(min_length=10, max_length=2000, description="What is wrong, in one or two sentences")
    evidence: Optional[str] = Field(None, max_length=1000, description="Short quote from the filing")
    related_filing_id: Optional[str] = Field(None, pattern=r"^[a-z0-9][a-z0-9_]*$",
                                             description="The other filing, for amendment conflicts")
    run_id: Optional[str] = Field(None, max_length=100, description="Groups findings from one agent run")


class FlagInconsistencyOutput(ToolOutput):
    finding_id: str
    recorded: bool
    location_verified: bool
    duplicate_of: Optional[str] = Field(None, description="Same finding already recorded in this run")
    findings_in_run: int


@register_tool
class FlagInconsistency(Tool):
    name = "flag_inconsistency"
    description = ("Record one finding (location, type, severity, description). Findings are append-only and can "
                   "never be edited or removed, so record every inconsistency you detect.")
    Input = FlagInconsistencyInput
    Output = FlagInconsistencyOutput

    def run(self, args: FlagInconsistencyInput) -> FlagInconsistencyOutput:
        manifest_entry(args.filing_id)                     # unknown filing -> ToolError
        if args.related_filing_id:
            manifest_entry(args.related_filing_id)
        earlier = read_findings(run_id=args.run_id) if args.run_id else []
        same = next((f for f in earlier if (f.filing_id, f.location, f.type, f.description) ==
                     (args.filing_id, args.location, args.type, args.description)), None)
        finding = _append({
            "finding_id": uuid.uuid4().hex[:12],
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": args.run_id, "filing_id": args.filing_id, "related_filing_id": args.related_filing_id,
            "location": args.location, "location_verified": verify_location(args.filing_id, args.location),
            "type": args.type, "severity": args.severity, "description": args.description,
            "evidence": args.evidence,
        })
        return FlagInconsistencyOutput(finding_id=finding.finding_id, recorded=True,
                                       location_verified=finding.location_verified,
                                       duplicate_of=same.finding_id if same else None,
                                       findings_in_run=len(earlier) + 1)