"""Tests for extract_definitions."""

import time

import pytest
import yaml
from pydantic import ValidationError

from agentforge_core.tools import call_tool
from agentforge_core.tools.extract_definitions import (
    count_usage, find_definitions, find_duplicates, find_near_misses,
)
from agentforge_core.tools.read_document import get_root, parse_text

DOC = """Section 1.1 Definitions.
"Supplemental Lease Rent" shall mean all amounts other than Basic Lease Rent.
"Tax" or "Taxes" shall mean any tax.
"Lease Indenture Trustee" means the trustee under the Lease Indenture.
"Review Lease" has the meaning stated in Section 3.2.
"Exchange Note" has the meaning set forth in Section 1.01 of the Credit Agreement.
"Prudent Industry Practice" means good practice.
"Component" means a part.
"Orphan Term" means something never used again.
"Lender" means a bank.
Section 3.2 Review. The Issuer shall identify each Review Lease (the "Review Pool"), and the Servicer (together, the "Parties") agree.
The Review Lease file and Review Leases list. Each Review Lease is tested. Review Lease again. Review Lease once more.
A Review Receivable is not a lease. The Lease Indenture Trustee shall act.
Section 3.3 Rent. The Lessee shall pay Supplemental Lease Rent and Supplement Lease Rent and Taxes.
Notice to the Indenture Trustee is required. Each Lender and any Lender may act. The Lender's rights apply.
Repairs follow withPrudent Industry Practice, and no Componentshall be temporary. Two Components exist.
Section 4.1 Other. Such that "run-rate" means the full benefit.
"Component" means a different part."""


@pytest.fixture(scope="module")
def parsed():
    doc = parse_text("x", "x", DOC)
    defs = find_definitions(doc)
    count_usage(doc, defs)
    return doc, defs


def by_term(defs, term):
    return [d for d in defs if d.term == term]


# ---------- finding definitions ----------

def test_definitions_section_terms(parsed):
    _, defs = parsed
    d = by_term(defs, "Supplemental Lease Rent")[0]
    assert d.source == "definitions_section" and d.defined_in == "1.1" and d.scope == "local"
    assert d.text.startswith('"Supplemental Lease Rent" shall mean')


def test_alias(parsed):
    assert by_term(parsed[1], "Tax")[0].aliases == ["Taxes"]


def test_points_elsewhere_local_and_external(parsed):
    review = by_term(parsed[1], "Review Lease")[0]
    assert (review.source, review.points_to, review.scope) == ("points_elsewhere", "Section 3.2", "local")
    note = by_term(parsed[1], "Exchange Note")[0]
    assert note.scope == "external" and "Credit Agreement" in note.points_to


def test_inline_definitions(parsed):
    pool = by_term(parsed[1], "Review Pool")[0]
    assert pool.source == "inline" and pool.defined_in == "3.2"
    assert by_term(parsed[1], "Parties")                      # "(together, the "Parties")"
    assert by_term(parsed[1], "run-rate")[0].source == "inline"


def test_usage_counts(parsed):
    _, defs = parsed
    assert by_term(defs, "Review Lease")[0].usage_count == 6            # incl. plural "Review Leases"
    assert by_term(defs, "Tax")[0].usage_count == 1                     # via alias "Taxes"
    assert by_term(defs, "Lender")[0].usage_count == 3                  # incl. possessive
    assert by_term(defs, "Orphan Term")[0].usage_count == 0


def test_glued_words(parsed):
    _, defs = parsed
    assert by_term(defs, "Prudent Industry Practice")[0].usage_count == 1   # "withPrudent ..."
    comp = by_term(defs, "Component")[0]
    assert comp.glued_uses == 1 and comp.usage_count == 1                   # "Componentshall" vs "Components"


def test_duplicates(parsed):
    dups = find_duplicates(parsed[1], parsed[0])
    assert [(d.term, d.defined_in, d.note) for d in dups] == [("Component", ["1.1", "4.1"], "defined_twice")]

# ---------- near-misses ----------

@pytest.fixture(scope="module")
def misses(parsed):
    doc, defs = parsed
    return {(m.phrase, m.likely_term): m for m in find_near_misses(doc, defs)}


def test_spelling_variant(misses):
    m = misses[("Supplement Lease Rent", "Supplemental Lease Rent")]
    assert (m.kind, m.confidence, m.found_in) == ("spelling_variant", "high", ["3.3"])


def test_truncated(misses):
    m = misses[("Indenture Trustee", "Lease Indenture Trustee")]
    assert m.kind == "truncated" and m.count == 1   # the full term in 3.2 isn't counted


def test_word_swap(misses):
    assert misses[("Review Receivable", "Review Lease")].kind == "word_swap"


def test_one_entry_per_phrase(parsed):
    doc, defs = parsed
    phrases = [m.phrase for m in find_near_misses(doc, defs)]
    assert len(phrases) == len(set(phrases))


def test_no_false_alarms(misses):
    phrases = {p for p, _ in misses}
    assert not phrases & {"Each Lender", "Lease Indenture Trustee", "Review Leases", "Supplemental Lease Rent"}

NOISE = """Section 1.1 Definitions.
"Equity Note Purchase Agreement" means the Note Purchase Agreement, dated today.
"Equity Note Purchaser" means a buyer.
"Equity Pledge Agreement" means the pledge.
"Event of Loss" means a loss.
"U.S. Government Obligations" means bonds.
"Government" means the state.
"Effective Date" means today.
"Final Shutdown Date" means later.
Section 2.1 Body. The Equity Note Purchase Agreement and the Equity Pledge Agreement apply.
The Equity Note Purchaser buys. Each Event of Loss. Occurrence of Events of Loss.
U.S. Government Obligations are bonds. Governmental approvals and Government consent.
The Final Shutdown Date and the Effective Date and Effective Date and Effective Date and Effective Date.
Effective Date again. The Depository Trust Company holds it. Attention: Senior Manager.
SECTION 3. EVENTS OF LOSS AND NET LEASE PROVISIONS
Section 3.1 Events of Loss. THE FACILITY LESSEE'S RIGHTS ARE LIMITED."""


def test_noise_is_suppressed():
    doc = parse_text("n", "n", NOISE)
    defs = find_definitions(doc)
    count_usage(doc, defs)
    misses = [(m.phrase, m.likely_term) for m in find_near_misses(doc, defs)]
    assert misses == [], misses
    assert next(d for d in defs if d.term == "Government").glued_uses == 0   # "Governmental" is a real word


ROUND2 = """This Agreement is made by Cosmos Acquisition, LLC (the "Buyer") and others.
Section 1.01 Defined Terms.
"Joint Venture Investments Basket" means a basket.
"Event of Default" has the meaning set forth in Section 8.01.
"Review Fee" has the meaning stated in Section 4.3.
"Mortgaged Properties" means land.
"Trust Indenture Act" shall mean the 1939 act; provided that if amended, "Trust Indenture Act" means the act as amended.
"Review Lease" means a lease under review.
"Review Materials" means files.
"Exchange Act" means the 1934 act.
"Solicited Discounted Prepayment Offer" means an offer.
"Borrower Solicitation of Discounted Prepayment Offers" means a solicitation.
"EEA Financial Institution" means a bank.
"Buyer" has the meaning specified in the introductory paragraph hereto.
Section 4.3 Fees. The fee (the "Review Fee") is due. Each Review Lease is checked. The Review Lease, the Review Lease,
the Review Lease, the Review Lease and Review Materials. A Review Receivable fails.
Section 7.02 Investments. The Joint Venture Investment Basket applies. Each Mortgaged Property is pledged.
Section 8.01 Defaults. Each of the following is an "Event of Default". Any Events of Default.
Section 9.01 Other. The Resource Conservation and Recovery Act and the Exchange Act and the Exchange Act and the Exchange Act
and the Exchange Act and the Exchange Act. The Lender's Commitment. The Financial Institutions Reform Act."""


@pytest.fixture(scope="module")
def round2():
    doc = parse_text("r", "r", ROUND2)
    defs = find_definitions(doc)
    count_usage(doc, defs)
    return doc, defs, {m.phrase: m for m in find_near_misses(doc, defs)}


def test_real_redwire_pattern_found(round2):
    _, defs, misses = round2
    m = misses["Joint Venture Investment Basket"]
    assert (m.kind, m.likely_term) == ("spelling_variant", "Joint Venture Investments Basket")
    assert by_term(defs, "Joint Venture Investments Basket")[0].usage_count == 0


def test_pointer_is_not_a_duplicate(round2):
    doc, defs, _ = round2
    assert {d.term for d in find_duplicates(defs, doc)} == set()   # Event of Default, Review Fee, Trust Indenture Act


def test_head_noun_plural_and_singular_use(round2):
    _, defs, misses = round2
    assert "Events of Default" not in misses
    assert by_term(defs, "Mortgaged Properties")[0].usage_count == 1


def test_word_swap_picks_most_used_term(round2):
    assert round2[2]["Review Receivable"].likely_term == "Review Lease"


def test_fragments_and_possessives_ignored(round2):
    phrases = set(round2[2])
    assert not phrases & {"Recovery Act", "Lender's Commitment", "Financial Institution", "Discounted Prepayment Offer"}


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


def test_tool_full(repo):
    out = call_tool("extract_definitions", {"filing_id": "doc"})
    assert out.counts["scope_external"] == 1
    assert "Orphan Term" in out.unused_terms and "Exchange Note" not in out.unused_terms
    assert all(len(d.text) <= 200 for d in out.definitions)


def test_tool_term_lookup(repo):
    out = call_tool("extract_definitions", {"filing_id": "doc", "term": "taxes"})
    assert [d.term for d in out.definitions] == ["Tax"]


def test_tool_only_issues(repo):
    out = call_tool("extract_definitions", {"filing_id": "doc", "only_issues": True})
    assert {d.term for d in out.definitions} == {"Component"} and out.near_misses   # both definitions of it


@pytest.mark.parametrize("args", [{}, {"filing_id": "doc", "term": ""}, {"filing_id": "doc", "limit": 0}])
def test_bad_args(args):
    with pytest.raises(ValidationError):
        call_tool("extract_definitions", args)


def test_big_document_is_fast():
    """Redwire is ~870k characters; the tool must stay quick."""
    body = "\n".join(f'"Term Number {i} Alpha" means item {i}.' for i in range(400))
    filler = " ".join(f"The Term Number {i % 400} Alpha applies under the Lease Indenture." for i in range(12000))
    doc = parse_text("big", "big", "Section 1.01 Defined Terms.\n" + body + "\nSection 2.01 Body. " + filler)
    t = time.time()
    defs = find_definitions(doc)
    count_usage(doc, defs)
    find_near_misses(doc, defs)
    assert len(defs) == 400 and time.time() - t < 5

# ---------- your real filings ----------

def downloaded(fid):
    m = get_root() / "data" / "contracts" / "manifest.yaml"
    if not m.exists():
        return False
    e = next((e for e in yaml.safe_load(m.read_text()) or [] if e["id"] == fid), None)
    return bool(e and e.get("local_path") and (get_root() / e["local_path"]).exists())


@pytest.mark.skipif(not downloaded("tva_facility_lease"), reason="TVA lease not downloaded")
def test_tva_supplement_lease_rent():
    """Day 1 finding: 13.2 says 'Supplement Lease Rent' instead of 'Supplemental Lease Rent'."""
    out = call_tool("extract_definitions", {"filing_id": "tva_facility_lease", "only_issues": True})
    hits = [m for m in out.near_misses if m.phrase == "Supplement Lease Rent"]
    assert hits and hits[0].likely_term == "Supplemental Lease Rent" and "13.2" in hits[0].found_in