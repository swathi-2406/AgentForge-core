"""Tests for extract_section_map: the subsection-letter logic, the tool, and your real filings."""

import pytest
import yaml
from pydantic import ValidationError

from agentforge_core.tools import call_tool
from agentforge_core.tools.extract_section_map import find_subsections
from agentforge_core.tools.read_document import get_root

# ---------- subsection letters ----------

@pytest.mark.parametrize("text, subs, skipped", [
    ("Intro.\n(a) one\n(b) two\n(c) three", ["a", "b", "c"], []),
    ("Section 2.02 Borrowings. (a) Each...\n(b) Next", ["a", "b"], []),          # (a) right after heading
    ("(a) one\n(i) roman\n(ii) roman\n(b) two", ["a", "b"], []),                 # roman list inside (a)
    ("(a)\n(b)\n(c)\n(d)\n(e)\n(f)\n(g)\n(h)\n(i) real letter\n(j)", list("abcdefghij"), []),
    ("(a)\n(b)\n(c)\n(d)\n(e)\n(f)\n(g)\n(h) last\n(i) roman\n(ii) roman", list("abcdefgh"), []),  # (i) then (ii) = roman
    # ("(h) last\n(i) roman\n(ii) roman", list("h"), list("abcdefg")),             # (i) then (ii) = roman
    ("(a) one\n(b) two\n(d) four", ["a", "b", "d"], ["c"]),                      # a real gap
    ("(a) one\n(A) nested\n(B) nested\n(b) two", ["a", "b"], []),                # capitals ignored
    ("the Borrower under clause (b) above", [], []),                             # cross-reference, not a clause
    ("No clauses here.", [], []),
])
def test_find_subsections(text, subs, skipped):
    assert find_subsections(text) == (subs, skipped)


def test_double_letters():
    text = "\n".join(f"({c})" for c in "abcdefghijklmnopqrstuvwxyz") + "\n(aa) more\n(bb) more"
    subs, skipped = find_subsections(text)
    assert subs[-3:] == ["z", "aa", "bb"] and skipped == []

# ---------- the tool ----------

def make_repo(tmp_path, monkeypatch, html: str):
    raw = tmp_path / "data" / "contracts" / "raw"
    raw.mkdir(parents=True)
    (raw / "doc.htm").write_text(html, encoding="utf-8")
    (tmp_path / "data" / "contracts" / "manifest.yaml").write_text(
        yaml.safe_dump([{"id": "doc", "local_path": "data/contracts/raw/doc.htm"}]))
    monkeypatch.setenv("AGENTFORGE_ROOT", str(tmp_path))


def test_tool_by_name(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch,
              "<p>ARTICLE I</p><p>Section 1.1 Terms. Words.</p><p>(a) one</p><p>(b) two</p><p>(d) four</p>"
              "<p>Section 1.2 Other. No clauses.</p>")
    out = call_tool("extract_section_map", {"filing_id": "doc"})
    assert [e.number for e in out.entries] == ["Article I", "1.1", "1.2"]
    assert out.get("1.1").subsections == ["a", "b", "d"]
    assert out.get("1.2").subsections == []
    assert any("1.1 skips (c)" in w for w in out.warnings)


@pytest.mark.parametrize("args", [{}, {"filing_id": "../x"}, {"filing_id": "doc", "extra": 1}])
def test_bad_args(args):
    with pytest.raises(ValidationError):
        call_tool("extract_section_map", args)

# ---------- your real filings ----------

def downloaded(fid):
    m = get_root() / "data" / "contracts" / "manifest.yaml"
    if not m.exists():
        return False
    e = next((e for e in yaml.safe_load(m.read_text()) or [] if e["id"] == fid), None)
    return bool(e and e.get("local_path") and (get_root() / e["local_path"]).exists())


@pytest.mark.skipif(not downloaded("tva_facility_lease"), reason="TVA lease not downloaded")
def test_tva_map():
    out = call_tool("extract_section_map", {"filing_id": "tva_facility_lease"})
    assert out.get("13.2").subsections == ["a", "b", "c", "d", "e"]
    assert out.get("23.10") and out.get("14")


@pytest.mark.skipif(not downloaded("ford_arr_2026b"), reason="Ford not downloaded")
def test_ford_map():
    out = call_tool("extract_section_map", {"filing_id": "ford_arr_2026b"})
    assert out.get("Schedule A") and out.get("Schedule B")


@pytest.mark.skipif(not downloaded("redwire_credit_original"), reason="Redwire not downloaded")
def test_redwire_map():
    out = call_tool("extract_section_map", {"filing_id": "redwire_credit_original"})
    assert out.get("2.02").subsections[:2] == ["a", "b"]