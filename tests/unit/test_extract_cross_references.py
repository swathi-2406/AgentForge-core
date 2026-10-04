"""Tests for extract_cross_references."""

import pytest
import yaml
from pydantic import ValidationError

from agentforge_core.tools import call_tool
from agentforge_core.tools.extract_cross_references import find_references
from agentforge_core.tools.read_document import get_root, parse_text

DOC = """ARTICLE I
DEFINITIONS
Section 1.1 Terms. See Section 13.2(d). Also Sections 2.1 and 2.2. Also Section 99.1 and Section 13.2(f).
Notice is given in Section 9.1 of the Participation Agreement. Sections 9.1 and 9.2 of the Participation Agreement apply.
Within Section 15 and 30 days. See Schedule A and Schedule B. Section 13.2(ii) here. Article I applies. Article 1 too.
Section 2.1 One. Under clauses (a) and (b) of Section 13.2, fine.
Section 2.2 Two. Text.
Section 13.2 Elections. The Lessee may:
(a) one
(b) two
(c) three, subject to clause (a) above and clause (g) above
(d) four
(e) five
Section 15 Buy Out. Text.
SCHEDULE A
Stuff."""


@pytest.fixture(scope="module")
def refs():
    return find_references(parse_text("x", "x", DOC))


def pick(refs, **kw):
    return [r for r in refs if all(getattr(r, k) == v for k, v in kw.items())]


def test_section_with_clause_ok(refs):
    r = pick(refs, target="13.2", found_in="1.1", clauses=["d"])
    assert r and r[0].status == "ok"


def test_missing_clause(refs):
    r = pick(refs, target="13.2", clauses=["f"])[0]
    assert r.status == "missing_clause" and r.missing_clauses == ["f"]


def test_missing_section(refs):
    assert pick(refs, target="99.1")[0].status == "missing_section"


def test_list_splits_into_items(refs):
    assert {r.target for r in pick(refs, found_in="1.1", kind="section", status="ok")} >= {"2.1", "2.2"}


def test_external_applies_to_whole_list(refs):
    ext = pick(refs, status="external")
    assert {r.target for r in ext} == {"9.1", "9.2"}
    assert all(r.external_doc == "Participation Agreement" for r in ext)


def test_number_followed_by_days_is_not_a_section(refs):
    assert pick(refs, target="15")[0].status == "ok"
    assert not pick(refs, target="30")


def test_attachments(refs):
    assert pick(refs, target="Schedule A")[0].status == "ok"
    assert pick(refs, target="Schedule B")[0].status == "missing_attachment"


def test_roman_clause_is_unverified(refs):
    assert pick(refs, target="13.2", clauses=["ii"])[0].status == "unverified"


def test_articles_roman_and_arabic(refs):
    arts = pick(refs, kind="article")
    assert [r.target for r in arts] == ["Article I", "Article I"] and all(r.status == "ok" for r in arts)


def test_clause_prefix_attaches_to_section(refs):
    r = pick(refs, target="13.2", found_in="2.1")[0]
    assert r.clauses == ["a", "b"] and r.status == "ok"
    assert not pick(refs, kind="local_clause", found_in="2.1")  # not double-counted


def test_local_clauses(refs):
    local = pick(refs, kind="local_clause", found_in="13.2")
    assert [(r.clauses, r.status) for r in local] == [(["a"], "ok"), (["g"], "unverified")]


def test_headings_are_not_references(refs):
    assert not any(r.char_offset == DOC.index("Section 13.2 Elections") for r in refs)


@pytest.mark.parametrize("sentence, doc", [
    ("in the form of Exhibit F to the Participation Agreement.", "Participation Agreement"),
    ("as described in Exhibit 1 to the Equity Note Purchase Agreement.", "Equity Note Purchase Agreement"),
    ("as defined in Section 3(3) of ERISA that is subject to it.", "ERISA"),
    ("under Section 101 of Title 11 of the Bankruptcy Code.", "Title 11"),
    ("except as provided in Section 905 of such act; provided.", "such act"),
    ("all conditions in such Section 2.10(c) thereof are met.", "(another document: 'thereof')"),
    ("comply with 41 C.F.R. section 60-1.4 and rules.", "C.F.R."),
    ("the Bankruptcy Code, 11 U.S.C. §101 et seq.", "U.S.C."),
    ("a default under Section 4.2(e) or (f) of the Lease Indenture.", "Lease Indenture"),
    ("defined in Appendix 1 to the 2026-B Exchange Note Supplement, dated", "2026-B Exchange Note Supplement"),
    ("or in Appendix A to the Credit and Security Agreement.", "Credit and Security Agreement"),
    ("within the meaning of Code Section 163(i).", "Code"),
    ("for purposes of ERISA Section 3(42) or otherwise", "ERISA"),
    ("purposes of Treasury Regulation Section 5f.103-1(c), as agent", "Treasury Regulation"),
    ("within the meaning of section 1a(47) of the Commodity Exchange Act, if", "Commodity Exchange Act"),
    ("purposes of Section 1a(18)(A)(v)(II) of the Commodity Exchange Act.", "Commodity Exchange Act"),
    ("termination under Sections 4041 or 4041A of ERISA, or", "ERISA"),
    ("implementing Article 55 of Directive 2014/59/EU of the Parliament", "Directive 2014/59/EU"),
    ("within the meaning of 29 CFR § 2510.3- 101, as modified", "CFR"),
    ("in registered form under Section 5f.103-1(c) of the Treasury Regulations.", "(regulation number)"),
])
def test_other_document_references(sentence, doc):
    text = f"Section 1.1 Terms. Text.\nSection 2.1 Body. Note {sentence}"
    refs = find_references(parse_text("x", "x", text))
    assert refs and all(r.status == "external" for r in refs), [(r.target, r.status) for r in refs]
    assert refs[0].external_doc == doc


@pytest.mark.parametrize("sentence, target, clauses", [
    ("calculated under Section 3.2, (ii) a reduction in rent", "3.2", []),
    ("as set out in Section 3.2(a), and (iii) either a reduction", "3.2", ["a"]),
    ("pursuant to Section 3.2 and (c) following a loss", "3.2", []),
    ("under Sections 3.2(a), (b) and (c).", "3.2", ["a", "b", "c"]),
])
def test_outer_list_items_are_not_clauses(sentence, target, clauses):
    text = f"Section 3.2 Rent. Pay (a) rent, (b) fees and (c) costs.\nSection 3.4 Adjust. Text {sentence}"
    r = [r for r in find_references(parse_text("x", "x", text)) if r.found_in == "3.4"][0]
    assert (r.target, r.clauses, r.status) == (target, clauses, "ok")


def test_inline_list_counts_as_clauses():
    text = ("Section 7.1 Maintenance. The Lessee shall (a) maintain the Facility and (b) keep records.\n"
            "Section 17 Default. A breach of clause (a) of Section 7.1 or of Section 7.1(c).\n"
            "Section 23.2 Notices. Notice by either (a) hand or (b) courier, confirmed per clauses (a) and (b) above.")
    refs = find_references(parse_text("x", "x", text))
    got = {(r.found_in, r.target, tuple(r.clauses)): r.status for r in refs}
    assert got[("17", "7.1", ("a",))] == "ok"              # inline clause exists
    assert got[("17", "7.1", ("c",))] == "missing_clause"  # (c) appears nowhere in 7.1
    assert got[("23.2", "23.2", ("a", "b"))] == "ok"


def test_attachment_memory():
    text = ("Section 1.1 Usage. Terms are defined in Appendix 1 to the Exchange Note Supplement. "
            "Appendix 1 contains usage rules.\nSection 2.1 Other. Text.")
    refs = [r for r in find_references(parse_text("x", "x", text)) if r.target == "Appendix 1"]
    assert [r.status for r in refs] == ["external", "external"]
    assert refs[1].external_doc == "Exchange Note Supplement"


def test_scope_statement():
    text = ("Section 3.3 Reps. Text.\nSCHEDULE B\n(Section references are to the Exchange Note Purchase Agreement)\n"
            "Section 3.3(c) - Monthly Payments. Section 3.3(a) - Eligible.")
    refs = [r for r in find_references(parse_text("x", "x", text)) if r.found_in == "Schedule B" and r.kind == "section"]
    assert len(refs) == 2 and all(r.external_doc == "Exchange Note Purchase Agreement" for r in refs)


def test_amendment_checked_against_original():
    from agentforge_core.tools.extract_cross_references import resolve_against_original
    original = parse_text("orig", "orig", "Section 2.01 Loans. (a) Term loans.\nSection 7.11 Covenant. Text.")
    amend = parse_text("amend", "amend", (
        "Section 1.01 Loans. As provided in Section 2.01 of the Credit Agreement and Section 7.11 of the Credit Agreement.\n"
        "Section 2.01 Amendments. (a) Section 2.01(a) is amended. (b) Section 7.11 is amended. "
        "(c) Section 9.99 is amended. (d) Section 2.01(z) is amended. Also Section 4.4 of the Credit Agreement."))
    refs = find_references(amend)
    assert resolve_against_original(refs, original) == "Credit Agreement"
    got = {(r.found_in, r.target, tuple(r.clauses)): (r.status, r.resolved_in) for r in refs}
    assert got[("2.01", "7.11", ())] == ("external", "orig")            # missing here, exists in original
    assert got[("2.01", "9.99", ())] == ("missing_section", "orig")      # missing in both = real finding
    assert got[("2.01", "2.01", ("z",))] == ("missing_clause", "orig")
    assert got[("2.01", "4.4", ())] == ("missing_section", "orig")       # "of the Credit Agreement" but not there
    assert got[("2.01", "2.01", ("a",))][0] == "ok"                       # exists in the amendment itself


def test_toc_mentions_skipped():
    text = ("TABLE OF CONTENTS\nSection 1.1 Terms 1\nSection 2.1 Other 2\n"
            "Section 1.1 Terms. See Section 2.1.\nSection 2.1 Other. Text.")
    refs = find_references(parse_text("x", "x", text))
    assert [(r.found_in, r.target) for r in refs] == [("1.1", "2.1")]

# ---------- the tool ----------

def make_repo(tmp_path, monkeypatch, html):
    raw = tmp_path / "data" / "contracts" / "raw"
    raw.mkdir(parents=True)
    (raw / "doc.htm").write_text(html, encoding="utf-8")
    (tmp_path / "data" / "contracts" / "manifest.yaml").write_text(
        yaml.safe_dump([{"id": "doc", "local_path": "data/contracts/raw/doc.htm"}]))
    monkeypatch.setenv("AGENTFORGE_ROOT", str(tmp_path))


def test_tool_only_problems(tmp_path, monkeypatch):
    html = "".join(f"<p>{line}</p>" for line in DOC.split("\n"))
    make_repo(tmp_path, monkeypatch, html)
    full = call_tool("extract_cross_references", {"filing_id": "doc"})
    probs = call_tool("extract_cross_references", {"filing_id": "doc", "only_problems": True})
    assert full.total == len(full.references) and full.counts["ok"] >= 5
    assert {r.status for r in probs.references} <= {"missing_section", "missing_clause", "missing_attachment", "unverified"}
    assert len(probs.references) < len(full.references)


@pytest.mark.parametrize("args", [{}, {"filing_id": "doc", "limit": 0}, {"filing_id": "doc", "only_problem": True}])
def test_bad_args(args):
    with pytest.raises(ValidationError):
        call_tool("extract_cross_references", args)

# ---------- your real filings ----------

def downloaded(fid):
    m = get_root() / "data" / "contracts" / "manifest.yaml"
    if not m.exists():
        return False
    e = next((e for e in yaml.safe_load(m.read_text()) or [] if e["id"] == fid), None)
    return bool(e and e.get("local_path") and (get_root() / e["local_path"]).exists())


@pytest.mark.skipif(not downloaded("tva_facility_lease"), reason="TVA lease not downloaded")
def test_tva_15_1_points_at_13_2_d():
    """Day 1 finding: 15.1 cites the buy-out election in 13.2(d), but it's in 13.2(e).
    The reference RESOLVES (d exists), so the status is ok -- spotting it is the agent's job."""
    out = call_tool("extract_cross_references", {"filing_id": "tva_facility_lease", "limit": 5000})
    hits = [r for r in out.references if r.found_in == "15.1" and r.target == "13.2" and "d" in r.clauses]
    assert len(hits) >= 2 and all(r.status == "ok" for r in hits)