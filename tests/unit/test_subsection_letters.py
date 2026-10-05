"""Day 3 task 3: inline (w)(x)(y)(z) in Redwire original 6.01 were read as subsections."""

from agentforge_core.tools.extract_section_map import find_subsections

# Trimmed from redwire_credit_original Section 6.01, keeping every trap.
REDWIRE_601 = """Section 6.01. Financial Statements.

(a) Deliver ... opinion of (i) any accounting firm, (ii) one of the "Big Four" or (iii) another firm, (other than as a result of (w) an upcoming maturity date, (x) changes in GAAP, (y) a prospective default, or (z) Unrestricted Subsidiaries).

(b) Deliver ... under this clause (b), customary management discussion;

(c) Deliver ... under this clause (c), customary management discussion.

(d) Prior to a Qualified IPO, deliver ... Projections; and

(e) Deliver ... referred to in Sections 6.01(a), 6.01(b) and 6.01(c).

Notwithstanding the foregoing, ... (other than as a result of (w)

an upcoming maturity date, (x) changes in GAAP, (y) a prospective default, or (z) Unrestricted Subsidiaries).

The Borrowers hereby acknowledge that (a) the Administrative Agent will post materials and (b) certain Lenders are Public Lenders. The Lead Borrower agrees that (w) all such Borrower Materials shall be marked "PUBLIC"; (x) by marking Borrower Materials "PUBLIC" ... (as set forth in Section 10.08); (y) all Borrower Materials marked "PUBLIC" are permitted; and (z) the Administrative Agent shall treat ...
"""


def test_redwire_601_is_a_to_e_with_no_skips():
    assert find_subsections(REDWIRE_601, "Financial Statements.") == (["a", "b", "c", "d", "e"], [])


def test_real_single_skip_at_line_start_is_still_reported():
    assert find_subsections("(a) one\n(b) two\n(d) four") == (["a", "b", "d"], ["c"])


def test_inline_subsections_after_sentences_still_count():
    text = "13.2 Procedure. (a) one. (b) two. (c) three; (d) four. (e) five."
    assert find_subsections(text) == (["a", "b", "c", "d", "e"], [])


def test_inline_marker_that_skips_is_ignored():
    assert find_subsections("Each party agrees; (b) as provided in Section 10.07 ...") == ([], [])


def test_page_break_pushing_inline_letter_to_line_start_is_ignored():
    assert find_subsections("(a) one\n(b) two\n(z) Unrestricted Subsidiaries).")[0] == ["a", "b"]


def test_roman_lines_under_a_are_not_letters():
    assert find_subsections("(a) shall:\n(i) first\n(ii) second\n(v) fifth\n(b) next") == (["a", "b"], [])


def test_h_then_i_is_a_real_letter_but_h_then_roman_i_ii_is_not():
    assert find_subsections("\n".join(f"({c}) x" for c in "abcdefghij"))[0] == list("abcdefghij")
    text = "\n".join(f"({c}) x" for c in "abcdefg") + "\n(h) shall:\n(i) one\n(ii) two"
    assert find_subsections(text)[0] == list("abcdefgh")


def test_double_letters_continue_after_z():
    text = "\n".join(f"({c}) x" for c in "abcdefghijklmnopqrstuvwxyz") + "\n(aa) x\n(bb) x"
    assert find_subsections(text)[0][-3:] == ["z", "aa", "bb"]


def test_definitions_section_has_no_subsections():
    assert find_subsections("(a) one\n(b) two\n(a) next term\n(c) more", "Defined Terms") == ([], [])