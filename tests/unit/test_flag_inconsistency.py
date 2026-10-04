"""Tests for flag_inconsistency: append-only, hash-chained, never refuses a valid finding."""

import json
import re

import pytest
import yaml
from pydantic import ValidationError

from agentforge_core.tools import call_tool
from agentforge_core.tools import flag_inconsistency as fi
from agentforge_core.tools.base import ToolError
from agentforge_core.tools.read_document import get_root

DOC = "<p>Section 13.2 Elections. (a) one (d) four (e) five</p><p>Section 15.1 Buy Out. See Section 13.2(d).</p>"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    raw = tmp_path / "data" / "contracts" / "raw"
    raw.mkdir(parents=True)
    (raw / "lease.htm").write_text(DOC)
    (raw / "orig.htm").write_text(DOC)
    (tmp_path / "data" / "contracts" / "manifest.yaml").write_text(yaml.safe_dump([
        {"id": "lease", "local_path": "data/contracts/raw/lease.htm"},
        {"id": "orig", "local_path": "data/contracts/raw/orig.htm"}]))
    monkeypatch.setenv("AGENTFORGE_ROOT", str(tmp_path))
    monkeypatch.delenv("AGENTFORGE_TRACES_DIR", raising=False)
    return tmp_path


def flag(**kw):
    args = {"filing_id": "lease", "location": "15.1", "type": "wrong_reference", "severity": "medium",
            "description": "15.1 cites 13.2(d) for the buy-out election, which is in 13.2(e)."}
    args.update(kw)
    return call_tool("flag_inconsistency", args)


def test_records_and_appends(repo):
    a = flag(run_id="r1")
    b = flag(run_id="r1", location="13.2", type="term_mismatch", description="Supplement Lease Rent is not a term.")
    assert a.recorded and b.findings_in_run == 2
    lines = (repo / "data" / "traces" / "findings.jsonl").read_text().splitlines()
    assert [json.loads(l)["finding_id"] for l in lines] == [a.finding_id, b.finding_id]


def test_location_checked_but_never_blocks(repo):
    assert flag(location="Section 13.2(d)").location_verified is True
    unknown = flag(location="99.9")
    assert unknown.recorded is True and unknown.location_verified is False


def test_duplicate_in_same_run_is_recorded_and_marked(repo):
    first = flag(run_id="r1")
    again = flag(run_id="r1")
    assert again.recorded and again.duplicate_of == first.finding_id
    assert flag(run_id="r2").duplicate_of is None          # other runs don't count


def test_amendment_finding_with_related_filing(repo):
    out = flag(type="amendment_conflict", related_filing_id="orig", location="13.2")
    assert fi.read_findings()[-1].related_filing_id == "orig" and out.recorded


def test_read_findings_filters(repo):
    flag(run_id="r1"); flag(run_id="r2"); flag(run_id="r2")
    assert len(fi.read_findings(run_id="r2")) == 2 and len(fi.read_findings(filing_id="lease")) == 3


def test_hash_chain_detects_edits_and_deletions(repo):
    for i in range(3):
        flag(run_id="r1", description=f"Finding number {i} in this run.")
    assert fi.verify_findings() == (True, None)
    path = repo / "data" / "traces" / "findings.jsonl"
    lines = path.read_text().splitlines()

    path.write_text("\n".join([lines[0], lines[2]]) + "\n")                     # someone deletes finding 2
    assert fi.verify_findings() == (False, 2)

    edited = json.loads(lines[1]); edited["severity"] = "low"                      # someone downgrades one
    path.write_text("\n".join([lines[0], json.dumps(edited), lines[2]]) + "\n")
    assert fi.verify_findings() == (False, 2)


def test_there_is_no_way_to_delete_or_edit():
    public = [n for n in dir(fi) if not n.startswith("_")]
    assert not [n for n in public if re.search(r"delete|remove|update|edit|clear|suppress", n, re.I)]


@pytest.mark.parametrize("bad", [
    {"type": "looks_fine"},                       # not a known finding type
    {"severity": "huge"},
    {"description": "short"},                     # under 10 characters
    {"location": ""},
    {"filing_id": "../etc"},
    {"suppress": True},                           # unknown field
])
def test_bad_args(repo, bad):
    with pytest.raises(ValidationError):
        flag(**bad)


def test_unknown_filing(repo):
    with pytest.raises(ToolError, match="Unknown filing_id"):
        flag(filing_id="nope")

# ---------- your real filing (findings go to a temp folder, not your data/traces) ----------

def downloaded(fid):
    m = get_root() / "data" / "contracts" / "manifest.yaml"
    if not m.exists():
        return False
    e = next((e for e in yaml.safe_load(m.read_text()) or [] if e["id"] == fid), None)
    return bool(e and e.get("local_path") and (get_root() / e["local_path"]).exists())


@pytest.mark.skipif(not downloaded("tva_facility_lease"), reason="TVA lease not downloaded")
def test_real_tva_location(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTFORGE_TRACES_DIR", str(tmp_path))
    out = call_tool("flag_inconsistency", {
        "filing_id": "tva_facility_lease", "location": "13.2", "type": "term_mismatch", "severity": "low",
        "description": '"Supplement Lease Rent" is used; the defined term is "Supplemental Lease Rent".',
        "evidence": "Basic Lease Rent (Equity Portion) and Supplement Lease Rent due and payable"})
    assert out.location_verified and (tmp_path / "findings.jsonl").exists()