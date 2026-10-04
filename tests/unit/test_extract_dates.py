"""Tests for extract_dates."""

import pytest
import yaml
from pydantic import ValidationError

from agentforge_core.tools import call_tool
from agentforge_core.tools.extract_dates import find_mentions, words_to_number
from agentforge_core.tools.read_document import get_root, parse_text

DOC = """This Agreement is dated as of October 28, 2020.
Section 1.01 Defined Terms.
"Closing Date" means October 28, 2020.
"Maturity Date" means the 15th day of March, 2027.
Section 2.02 Borrowings. Notice not later than three (3) Business Days prior, or one (1) Business Day for
Base Rate Loans, within sixty (90) days, eighteen months after February 30, 2021, then on 12/31/2020 and in
December 2020 and one hundred twenty (120) days. Payment is due on the next Business Day.
Section 2.02.1 Sub. Within ten days.
Section 3.01 Taxes. Within thirty (30) calendar days and 2 years."""


@pytest.fixture(scope="module")
def mentions():
    return find_mentions(parse_text("x", "x", DOC))


def pick(ms, **kw):
    return [m for m in ms if all(getattr(m, k) == v for k, v in kw.items())]


@pytest.mark.parametrize("text, value", [
    ("October 28, 2020", "2020-10-28"),
    ("15th day of March, 2027", "2027-03-15"),
    ("12/31/2020", "2020-12-31"),
    ("December 2020", "2020-12"),
])
def test_date_formats(mentions, text, value):
    assert pick(mentions, kind="date", text=text)[0].value == value


def test_labels(mentions):
    assert pick(mentions, value="2027-03-15")[0].label == "Maturity Date"
    closing = pick(mentions, value="2020-10-28")
    assert [m.label for m in closing] == ["dated as of", "Closing Date"]


@pytest.mark.parametrize("text, value", [
    ("three (3) Business Days", "3 business_day"),
    ("one (1) Business Day", "1 business_day"),
    ("eighteen months", "18 month"),
    ("one hundred twenty (120) days", "120 day"),
    ("thirty (30) calendar days", "30 day"),
    ("2 years", "2 year"),
    ("ten days", "10 day"),
])
def test_duration_formats(mentions, text, value):
    assert pick(mentions, kind="duration", text=text)[0].value == value


def test_no_amount_is_not_a_duration(mentions):
    assert not [m for m in mentions if "next Business Day" in m.text]


def test_issues(mentions):
    assert pick(mentions, text="sixty (90) days")[0].issue == "number_mismatch"
    assert pick(mentions, text="February 30, 2021")[0].issue == "invalid_date"
    assert len([m for m in mentions if m.issue]) == 2


@pytest.mark.parametrize("words, n", [(["eighteen"], 18), (["one", "hundred", "twenty"], 120),
                                      (["forty", "five"], 45), (["prior"], None)])
def test_words_to_number(words, n):
    assert words_to_number(words) == n

# ---------- the tool ----------

def make_repo(tmp_path, monkeypatch, html):
    raw = tmp_path / "data" / "contracts" / "raw"
    raw.mkdir(parents=True)
    (raw / "doc.htm").write_text(html, encoding="utf-8")
    (tmp_path / "data" / "contracts" / "manifest.yaml").write_text(
        yaml.safe_dump([{"id": "doc", "local_path": "data/contracts/raw/doc.htm"}]))
    monkeypatch.setenv("AGENTFORGE_ROOT", str(tmp_path))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, "".join(f"<p>{line}</p>" for line in DOC.split("\n")))


def test_tool_section_filter(repo):
    out = call_tool("extract_dates", {"filing_id": "doc", "section": "2.02", "kind": "duration"})
    assert {m.section for m in out.mentions} == {"2.02", "2.02.1"}
    assert out.counts["date"] == 6 and out.counts["number_mismatch"] == 1


def test_tool_summary_and_issues(repo):
    out = call_tool("extract_dates", {"filing_id": "doc", "only_issues": True})
    assert {m.issue for m in out.mentions} == {"invalid_date", "number_mismatch"}
    oct28 = next(s for s in out.dates if s.value == "2020-10-28")
    assert oct28.count == 2 and oct28.sections == ["1.01", "preamble"]


@pytest.mark.parametrize("args", [{}, {"filing_id": "doc", "kind": "year"}, {"filing_id": "doc", "section": ""}])
def test_bad_args(args):
    with pytest.raises(ValidationError):
        call_tool("extract_dates", args)

# ---------- your real filings ----------

def downloaded(fid):
    m = get_root() / "data" / "contracts" / "manifest.yaml"
    if not m.exists():
        return False
    e = next((e for e in yaml.safe_load(m.read_text()) or [] if e["id"] == fid), None)
    return bool(e and e.get("local_path") and (get_root() / e["local_path"]).exists())


@pytest.mark.skipif(not downloaded("redwire_credit_original"), reason="Redwire not downloaded")
def test_redwire_dated_october_28_2020():
    out = call_tool("extract_dates", {"filing_id": "redwire_credit_original", "kind": "date"})
    assert any(s.value == "2020-10-28" for s in out.dates)