"""Tests for diff_filings (naive) and compare_amended_clauses (targeted).

Fixtures are short copies of the real Redwire wording, so they run anywhere.
The last two tests use your real downloaded filings and skip if they're missing.
"""

from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from agentforge_core.tools import call_tool
from agentforge_core.tools import compare_filings as cf


@dataclass
class FakeSection:
    number: str
    text: str


@dataclass
class FakeDoc:
    filing_id: str
    text: str
    secs: dict = field(default_factory=dict)

    def section(self, number):
        s = self.secs.get(number)
        return FakeSection(number, s) if s else None


ORIG_SECS = {
    "1.01": ("Section 1.01 Defined Terms.\n"
             "“Lender” has the meaning set forth in the introductory paragraph to this Agreement.\n"
             "“Term Loan” means any Initial Term Loan, Extended Term Loan, Delayed Draw Term Loan, "
             "Incremental Term Loan, Refinancing Term Loan or Replacement Term Loan, as the context may require.\n"
             "“Total Outstandings” means the aggregate Outstanding Amount of all Loans.\n"),
    "2.02": ("Section 2.02 Borrowings.\n(a) Each Borrowing shall be made upon notice. Each such notice must be "
             "received by the Administrative Agent not later than 2:00 p.m., (I) three Business Days prior to the "
             "requested date of any Borrowing of Eurocurrency Rate Loans, and (II) three Business Days prior to the "
             "requested date of any Borrowing of Base Rate Loans; provided that the notice referred to in clause (I) "
             "above may be delivered no later than three Business Day prior to the Closing Date in the case of "
             "initial Credit Extensions. Each Borrowing shall be in a minimum principal amount of $1,000,000.\n"
             "(b) Following receipt of a Committed Loan Notice, the Administrative Agent shall notify each Lender.\n"),
    "2.01": ("Section 2.01 The Loans.\n(a) Term Borrowings. (i) Term Loan Borrowings. Each Term Lender severally "
             "agrees to make to the Lead Borrower on the Closing Date one or more Term Borrowings. Amounts borrowed "
             "under this Section 2.01(a)(i) and repaid may not be re-borrowed.\n(ii) Delayed Draw Term Borrowings. "
             "Each Term Lender agrees to make Delayed Draw Term Loans.\n(b) Revolving Borrowings. Each Revolving "
             "Credit Lender agrees to make Revolving Credit Loans.\n"),
    "2.06": ("Section 2.06 Termination of Commitments.\n(a) Optional. The Borrower may terminate the Commitments.\n"
             "(b) Mandatory. (i) The Initial Term Commitments of each Term Lender shall be automatically and "
             "permanently reduced to $0 upon the funding of the Initial Term Loans. The Revolving Credit "
             "Commitments shall automatically and permanently terminate on the Maturity Date.\n"
             "(c) Application. Any reduction shall be applied ratably.\n"),
    "7.11": ("Section 7.11 Financial Covenant. Permit the Consolidated Total Net Leverage Ratio as of the last day "
             "of any Test Period (commencing with the Test Period ending on March 30, 2021) to be greater than:\n"
             "March 30, 2021 5.00:1.00\nJune 30, 2021 5.00:1.00\nMarch 30, 2022 4.50:1.00\n"),
}

AMEND_TEXT = """FIRST AMENDMENT TO CREDIT AGREEMENT
Section 1.01 . Each First Amendment Term Lender agrees to make First Amendment Term Loans.
ARTICLE II.
AMENDMENTS
(a) Amendments of Section 1.01.
Section 1.01 is hereby revised by:
(i) Inserting the following definitions in the appropriate alphabetical order therein:
“First Amendment” means the First Amendment to Credit Agreement dated as of February 17, 2021.
“First Amendment Term Loans” has the meaning assigned to such term in the First Amendment.
(ii) Amending and restating the defined terms set forth below to read in their entirety as follows:
“Lenders” (a) has the meaning set forth in the introductory paragraph to this Agreement and (b) includes
the First Amendment Term Lenders, each of which is referred to herein as a “Lender”.
“Term Loan” means any Initial Term Loan, Extended Term Loan, Delayed Draw Term Loan, Incremental Term Loan
(including the Term Loans made on the First Amendment Effective Date by the First Amendment Term Lenders),
Refinancing Term Loan or Replacement Term Loan, as the context may require.
(b) Amendment of Section 2.01.
Section 2.01(a)(i) is amended and restated to read in its entirety as follows:
“(a) Term Borrowings .
(i) Term Loan Borrowings . Each Term Lender severally agrees to make to the Lead Borrower on the Closing Date
one or more Term Borrowings. Each First Amendment Term Lender agrees to make a Term Loan to the Lead Borrower on
the First Amendment Effective Date. Amounts borrowed under this Section 2.01(a)(i) and repaid may not be
re-borrowed.”
(c) Amendment of Section 2.02.
The second sentence of Section 2.02(a) is amended and restated to read in its entirety as follows:
“Each such notice must be received by the Administrative Agent not later than 2:00 p.m., (I) three (3) Business
Days prior to the requested date of any Borrowing of Eurocurrency Rate Loans, and (II) three (3) Business Days
prior to the requested date of any Borrowing of Base Rate Loans; provided that the notice referred to in clause (I)
above may be delivered no later than (x) three
(3) Business Day prior to the Closing Date in the case of initial Credit Extensions and (y) one
(1) Business Day prior to the First Amendment Effective Date in the case of the First Amendment Term Loans.”
(d) Amendment of Section 2.06.
Section 2.06(b) is amended and restated to read in its entirety as follows:
“ Mandatory . (i) The Initial Term Commitments of each Term Lender shall be automatically and permanently
reduced to $0 upon the funding of the Initial Term Loans. The Revolving Credit Commitments shall
automatically and permanently terminate on the Maturity Date.”
(g) Amendment of Section 7.11.
Section 7.11 is amended and restated to read in its entirety as follows:
“Permit the Consolidated Total Net Leverage Ratio as of the last day of any Test Period (commencing with the
Test Period ending on March 30, 2021) to be greater than:
March 31, 2021 6.50:1.00
June 30, 2021 6.00:1.00
March 31, 2022 5.50:1.00
ARTICLE III.
CONDITIONS PRECEDENT
Section 3.02. The Administrative Agent shall have received a notice as required by Section 2.02(a).
"""


@pytest.fixture
def docs(monkeypatch):
    orig = FakeDoc("redwire_credit_original", "\n".join(ORIG_SECS.values()), ORIG_SECS)
    amend = FakeDoc("redwire_credit_amend1", AMEND_TEXT)
    table = {orig.filing_id: orig, amend.filing_id: amend}
    monkeypatch.setattr(cf, "load_document", lambda fid, root=None: table[fid])
    return {"original_id": orig.filing_id, "amendment_id": amend.filing_id}


def by_ref(out):
    return {c.ref: c for c in out.clauses}


# ---------------------------------------------------------------- inputs

def test_same_filing_twice_rejected():
    with pytest.raises(ValidationError):
        cf.CompareAmendedClausesInput(original_id="a", amendment_id="a")


def test_bad_filing_id_rejected():
    with pytest.raises(ValidationError):
        cf.DiffFilingsInput(original_id="Bad Id", amendment_id="b")


# ---------------------------------------------------------------- naive

def test_naive_diff_reports_many_differences(docs):
    out = call_tool("diff_filings", docs)
    assert out.total_differences >= 8
    assert out.identical <= 2
    pair = next(h for h in out.hunks if "three (3) Business" in h.amendment_sentence)
    assert "three Business Days" in pair.closest_original      # rewording reported as a difference


# ---------------------------------------------------------------- targeted

def test_finds_every_directive_but_not_mentions(docs):
    out = call_tool("compare_amended_clauses", docs)
    assert out.total_directives == 5            # 1.01, 2.01(a)(i), 2.02(a), 2.06(b), 7.11 - not "Section 2.02(a)" in 3.02


def test_rewording_is_not_a_change(docs):
    c = by_ref(call_tool("compare_amended_clauses", docs))["2.06(b)"]
    assert c.located_by == "clause"
    assert c.status == "unchanged"


def test_restated_from_parent_level(docs):
    c = by_ref(call_tool("compare_amended_clauses", docs))["2.01(a)(i)"]
    assert c.located_by == "clause"
    assert len(c.substantive_changes) == 1
    assert c.substantive_changes[0].kind == "insert"
    assert "first amendment term lender" in c.substantive_changes[0].after


def test_second_sentence_finds_the_real_carve_out(docs):
    c = by_ref(call_tool("compare_amended_clauses", docs))["2.02(a)"]
    assert c.window_trimmed
    assert c.cosmetic_differences >= 3            # three vs three (3)
    added = " ".join(x.after for x in c.substantive_changes)
    assert "one business day prior to the first amendment effective date" in added
    assert "three" not in " ".join(x.before for x in c.substantive_changes)


def test_definitions_added_vs_restated(docs):
    refs = by_ref(call_tool("compare_amended_clauses", docs))
    assert refs["1.01 First Amendment"].status == "added"
    tl = refs["1.01 Term Loan"]
    assert tl.status == "changed"
    assert "first amendment effective date" in " ".join(x.after for x in tl.substantive_changes)


def test_restated_term_missing_from_original(docs):
    c = by_ref(call_tool("compare_amended_clauses", docs))["1.01 Lenders"]
    assert c.status == "missing_in_original"       # original §1.01 defines "Lender", not "Lenders"
    assert "doesn't have" in c.directive


def test_7_11_date_mismatch_introduced_by_amendment(docs):
    out = call_tool("compare_amended_clauses", docs)
    c = by_ref(out)["7.11"]
    assert c.status == "changed"
    assert any("6.50:1.00" in x.after for x in c.substantive_changes)
    flag = next(f for f in out.internal_flags if f.ref == "7.11")
    assert "March 30, 2021 vs March 31, 2021" == flag.detail
    assert flag.in_original is False


def test_missing_section_in_original(docs, monkeypatch):
    orig_secs = {k: v for k, v in ORIG_SECS.items() if k != "2.06"}
    orig = FakeDoc("redwire_credit_original", "x", orig_secs)
    amend = FakeDoc("redwire_credit_amend1", AMEND_TEXT)
    monkeypatch.setattr(cf, "load_document", lambda fid, root=None: {"redwire_credit_original": orig,
                                                                      "redwire_credit_amend1": amend}[fid])
    out = call_tool("compare_amended_clauses", docs)
    assert by_ref(out)["2.06(b)"].status == "missing_in_original"
    assert out.missing_in_original == 2          # 2.06(b) plus the restated Lenders


def test_no_directives_gives_warning(monkeypatch):
    plain = FakeDoc("p", "Nothing amended here.")
    monkeypatch.setattr(cf, "load_document", lambda fid, root=None: plain)
    out = call_tool("compare_amended_clauses", {"original_id": "o", "amendment_id": "p"})
    assert out.total_directives == 0 and out.warnings


# ---------------------------------------------------------------- your real filings

def _real():
    try:
        from agentforge_core.tools.read_document import load_document
        load_document("redwire_credit_original"), load_document("redwire_credit_amend1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Redwire filings not available: {e}")


REAL = {"original_id": "redwire_credit_original", "amendment_id": "redwire_credit_amend1"}


def test_real_redwire_targeted():
    _real()
    out = call_tool("compare_amended_clauses", REAL)
    bases = {c.ref.split()[0].split("(")[0] for c in out.clauses}
    assert {"1.01", "2.01", "2.02", "2.06", "2.07", "6.15", "7.11"} <= bases
    missing = {c.ref for c in out.clauses if c.status == "missing_in_original"}
    assert missing == {"1.01 Lenders"}            # original §1.01 defines "Lender" only
    assert any(f.ref == "7.11" and not f.in_original for f in out.internal_flags)


def test_real_redwire_naive_is_noisy():
    _real()
    out = call_tool("diff_filings", REAL)
    assert out.total_differences > 50