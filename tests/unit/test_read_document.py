"""Tests for read_document: a synthetic EDGAR-style fixture, plus your real filings when downloaded."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentforge_core.tools import call_tool
from agentforge_core.tools.base import ToolError
from agentforge_core.tools.read_document import get_root, load_document

FIXTURES = Path(__file__).parent / "fixtures"


def make_repo(tmp_path, monkeypatch, files: dict[str, str]):
    """Fake repo root with its own manifest, so tests never touch your real data."""
    raw = tmp_path / "data" / "contracts" / "raw"
    raw.mkdir(parents=True)
    entries = []
    for fid, content in files.items():
        (raw / f"{fid}.htm").write_text(content, encoding="utf-8")
        entries.append({"id": fid, "local_path": f"data/contracts/raw/{fid}.htm"})
    (tmp_path / "data" / "contracts" / "manifest.yaml").write_text(yaml.safe_dump(entries))
    monkeypatch.setenv("AGENTFORGE_ROOT", str(tmp_path))


@pytest.fixture
def lease(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"lease": (FIXTURES / "edgar_like.htm").read_text()})
    return "lease"


# ---------- happy path (synthetic) ----------

def test_sections_found_in_order(lease):
    out = call_tool("read_document", {"filing_id": lease})
    assert [s.number for s in out.sections] == [
        "preamble", "Article I", "1.1", "Article XIII", "13.2", "13.3", "14", "15.1", "Schedule A", "Schedule B"]


def test_toc_entries_dropped(lease):
    out = call_tool("read_document", {"filing_id": lease})
    assert out.toc_entries_skipped == 7
    assert "TABLE OF CONTENTS" in out.sections[0].text  # TOC lands in the preamble


def test_headings_and_articles(lease):
    secs = {s.number: s for s in call_tool("read_document", {"filing_id": lease}).sections}
    assert secs["Article I"].heading == "DEFINITIONS"
    assert secs["1.1"].heading == "Defined Terms"
    assert secs["13.2"].heading == "Lessee Elections"
    assert secs["13.2"].article == "Article XIII"


def test_sentence_starting_with_section_is_not_a_heading(lease):
    s132 = call_tool("read_document", {"filing_id": lease, "section": "13.2"}).sections[0]
    assert "Section 13.2(d) of this Lease shall not apply" in s132.text
    assert "(e) To buy out" in s132.text                # padded span became a space
    assert "RESERVED" not in s132.text                   # Section 14 split off


def test_heading_found_mid_line(lease):
    s133 = call_tool("read_document", {"filing_id": lease, "section": "13.3"}).sections[0]
    assert s133.heading == "Holdover"
    assert s133.text.startswith("Section 13.3 Holdover")


def test_toc_entry_with_no_body_warns(lease):
    out = call_tool("read_document", {"filing_id": lease})
    assert "16.1" not in [s.number for s in out.sections]
    assert any("16.1" in w for w in out.warnings)


def test_sentence_is_not_a_heading():
    from agentforge_core.tools.read_document import parse_text
    text = ("Section 1.01 Subject to the terms and conditions set forth herein, each Lender agrees to lend.\n"
            "Section 2.02 Borrowings, Conversions and Continuations of Loans. (a) Each Borrowing...")
    secs = {s.number: s.heading for s in parse_text("x", "x", text).sections}
    assert secs == {"1.01": "", "2.02": "Borrowings, Conversions and Continuations of Loans"}


def test_cover_label_stays_in_preamble(lease):
    out = call_tool("read_document", {"filing_id": lease})
    assert "EXHIBIT 10.24" in out.sections[0].text
    assert not any(s.number.startswith("Exhibit 10") for s in out.sections)


def test_reserved_section(lease):
    s14 = call_tool("read_document", {"filing_id": lease, "section": "14"}).sections[0]
    assert s14.heading == "[RESERVED]"


def test_text_is_cleaned(lease):
    doc = load_document(lease)
    assert '"Supplemental Lease Rent"' in doc.text      # curly quotes -> straight
    assert "\xa0" not in doc.text                      # no non-breaking spaces
    assert "hidden XBRL junk" not in doc.text          # display:none removed
    assert "- 1 -" not in doc.text                     # page numbers removed


def test_section_prefix_filter(lease):
    out = call_tool("read_document", {"filing_id": lease, "section": "13"})
    assert [s.number for s in out.sections] == ["13.2", "13.3"]


def test_missing_section_warns(lease):
    out = call_tool("read_document", {"filing_id": lease, "section": "99.9"})
    assert out.sections == [] and "No section '99.9'" in out.warnings[-1]


# ---------- malformed filings ----------

def test_no_headings_returns_preamble_with_warning(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"blob": "<html><body><p>Just one paragraph.</p></body></html>"})
    out = call_tool("read_document", {"filing_id": "blob"})
    assert [s.number for s in out.sections] == ["preamble"]
    assert any("No section headings" in w for w in out.warnings)


def test_empty_file_does_not_crash(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"empty": ""})
    out = call_tool("read_document", {"filing_id": "empty"})
    assert out.sections == [] and out.warnings


# ---------- bad args ----------

@pytest.mark.parametrize("args", [
    {},                                         # missing filing_id
    {"filing_id": "../../etc/passwd"},          # path tricks blocked by the pattern
    {"filing_id": "lease", "sectoin": "1.1"},   # typo'd field
    {"filing_id": "lease", "section": ""},      # empty section
])
def test_bad_args_rejected(lease, args):
    with pytest.raises(ValidationError):
        call_tool("read_document", args)


def test_unknown_filing_id(lease):
    with pytest.raises(ToolError, match="Unknown filing_id"):
        call_tool("read_document", {"filing_id": "not_in_manifest"})


# ---------- your real filings (skipped until downloaded) ----------

def real_ids():
    manifest = get_root() / "data" / "contracts" / "manifest.yaml"
    if not manifest.exists():
        return []
    return [e["id"] for e in yaml.safe_load(manifest.read_text()) or []
            if e.get("local_path") and (get_root() / e["local_path"]).exists()]


@pytest.mark.parametrize("filing_id", real_ids() or [pytest.param("none", marks=pytest.mark.skip("no filings downloaded"))])
def test_real_filing_parses(filing_id):
    out = call_tool("read_document", {"filing_id": filing_id})
    assert out.char_count > 2000, out.warnings
    assert out.total_sections >= 5, out.warnings
    numbers = [s.number for s in load_document(filing_id).sections]
    assert len(numbers) == len(set(numbers)), "duplicate section numbers"


@pytest.mark.skipif("tva_facility_lease" not in real_ids(), reason="TVA lease not downloaded")
def test_tva_13_2_has_clause_e():
    s = call_tool("read_document", {"filing_id": "tva_facility_lease", "section": "13.2"}).sections
    assert s and "(e)" in s[0].text