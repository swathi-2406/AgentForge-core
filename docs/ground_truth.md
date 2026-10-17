# Ground truth: known findings per filing

The answer key for Sprint 3 eval tasks. Every row was spotted on Day 1 or by a tool on Day 2,
and is re-read by hand in the filing text before its status becomes `verified`.

Check any row with: `python scripts/inspect_filing.py <filing_id> "<section>"`

Status: `verified` = re-read in the filing · `to-check` = not yet re-read · `open` = needs a decision

## tva_facility_lease (easy)

| ID | Location | Type | Finding | Tier | Status |
|---|---|---|---|---|---|
| TVA-1 | §15.1 | wrong_reference | Cites "the election described in Section 13.2(d)" for the Early Buy Out Notice, twice. 13.2(d) is a Membership Interests payment; 13.2(e) is the Early Buy Out election and points back to 15.1. Should cite 13.2(e). | 2 | verified |
| TVA-2 | §4.1 | list_numbering | Roman list jumps from (iv) to (vi); no (v) in the section. | 1 | verified |
| TVA-3 | §13.2 | defined_term | "Supplement Lease Rent" (1 use); the defined term is "Supplemental Lease Rent" (§3.3, 29 uses). | 2 | verified |
| TVA-4 | §3.3 | defined_term | "Indenture Trustee" (2 uses); the defined term is "Lease Indenture Trustee". | 2 | verified |
| TVA-6 | Appendix A ("Excepted Payments") | list_numbering | Top-level list (a)–(f) ends with "(vii)" instead of "(g)"; (a) has its own (i)(ii) sub-list, so (vii) continues neither. | 1 | verified |

## ford_arr_2026b (medium)

| ID | Location | Type | Finding | Tier | Status |
|---|---|---|---|---|---|
| FORD-1 | §3.8 | wrong_reference | Says the Tests are in Schedule A; Schedule B is "Representations and Warranties and Tests", and §3.4 cites Schedule B correctly. | 2 | verified |
| FORD-2 | §3.3 | defined_term | "Review Receivable" (2 uses) where "Review Lease" (29 uses) is meant. "Review Lease" is defined in Appendix 1, not in this filing. | 2 | verified |
| FORD-3 | §1.2 → Schedule A | defined_term | "Contract" has the meaning stated in Schedule A, but Schedule A never defines it (its only use is inside "List of Approved Contract Forms"). | 2 | verified |

## redwire_credit_original (hard, original)

| ID | Location | Type | Finding | Tier | Status |
|---|---|---|---|---|---|
| RWO-1 | §1.01 | dangling_reference | Cites "Section 1.1" for pro forma calculation; no such section. Intended target: §1.11 Pro Forma Calculations. | 1 | verified |
| RWO-2 | §7.02 | defined_term | Defined as "Joint Venture Investments Basket" and "Unrestricted Subsidiary Investments Basket" (plural), but each definition refers to the other in the singular ("...Investment Basket"). | 2 | verified |
| RWO-3 | §1.01 | defined_term | "Securitization Repurchasing Obligations" (1 use); the defined term is "Securitization Repurchase Obligation". | 2 | verified |
| RWO-4 | §11.02, §11.11 | term_mismatch | Article XI calls its own instrument "this Guarantee" (5× in 11.02, 1× in 11.11); "Guarantee" is defined as any Person's generic obligation, "Guaranty" as this instrument. 11.08 and 11.11 use "this Guaranty" correctly. | 2 | verified |
| RWO-5 | §2.01(a)(ii) | wrong_reference | Calls itself "Section 2.01(b)" twice; the clause is 2.01(a)(ii). §2.01(b) exists (Revolving Credit Borrowings), so an existence check won't catch it. | 2 | verified |

## redwire_credit_amend1 (hard, amendment)

| ID | Location | Type | Finding | Tier | Status |
|---|---|---|---|---|---|
| RWA-1 | Article II (new §7.11 text) | date | Lead-in says the first Test Period ends March 30, 2021; the amended table uses March 31 (2021-2024). The original used March 30 in both places, so the amendment fixed the table but not the lead-in. | 3 | verified |
| RWA-2 | §4.01 | defined_term | Uses "Amended Credit Agreement" once; it is never defined. | 2 | verified |
| RWA-3 | §1.03 | list_numbering | Roman list jumps from (iv) to (vi); no (v) in the section. | 1 | verified |
| RWA-4 | §1.01 (amendment §(a)(ii)) | defined_term | Restates "Lenders"; the original's term is "Lender". Read literally, "First Amendment Term Lender" cites clause (b) of a "Lender" definition that has no clause (b). Scorer accepts defined_term or dangling_reference here. | 3 | verified |
| RWA-5 | §2.02(a), clause (II) | defined_term | New sentence says "Eurocurrency Loans"; the original defines only "Eurocurrency Rate Loan" and "Eurocurrency Rate". | 3 | verified |
| RWA-6 | §6.15(a) | list_numbering | Restated text wraps an inner (i)(ii)(iii) in a new outer (i)/(ii), so "(ii)" means two different things. Any reference to clause (ii) of 6.15(a) is ambiguous. | 3 | verified |

**RWA-1, settled on Day 3.** The amendment introduced this contradiction. It wasn't in the original:

| | Lead-in | Table March dates |
|---|---|---|
| Original §7.11 | March 30, 2021 | March 30 (every year) |
| Amended §7.11 | March 30, 2021 | March 31 (every year) |

## Hard-tier comparison targets

Clause-by-clause calls from `scripts/compare_clause.py --all` (Day 8).

### redwire_credit_amend1 vs redwire_credit_original

| Amended clause | Call | Notes |
|---|---|---|
| §1.01 new definitions (DSS, First Amendment …) | intended | New terms for the new loans. |
| §1.01 "Lenders" | contradiction | RWA-4 |
| §2.01(a)(i) | intended | Adds First Amendment Term Loans. The no-reborrow rule narrows to (a)(i), but (a)(ii) has its own. |
| §2.02(a) | contradiction | RWA-5. The one-day carve-out (y) is intended. |
| §2.06(b) | intended | Adds (iii) for First Amendment Term Commitments. |
| §2.07(a) | intended | Adds (B) amortization and (C) maturity for First Amendment Term Loans. |
| §6.15(a) | contradiction | RWA-6 |
| §7.11 | contradiction | RWA-1 (settled Day 3) |

### martin_credit_amend1 vs martin_credit_original

| Amended clause | Call | Notes |
|---|---|---|
| §1.01 "Loan Documents" | intended | Only adds "the First Amendment". |
| §1.01 "First Amendment" (new) | intended | New term. |
| §7.14(a) | intended | Interest coverage 2.00 → 1.75 for Q1–Q3 2025, then back to 2.00. |
| §7.14(c) | intended | First lien leverage 1.50 → 1.25 for Q1–Q3 2025, then back to 1.50. |
| §7.14(b), not amended | control | Untouched. See Not findings. |

Martin has no contradictions. Its tasks are clean negatives that test for false positives.

## Not findings

Things that look like findings but aren't. Eval scoring must not count them.

| Filing | What | Why it's not a finding |
|---|---|---|
| redwire_credit_original | "Admaicntistrative Agent" | Conversion damage: the missing word "act" was shuffled into it. |
| redwire_credit_original | §6.01 inline (w)(x)(y)(z) | Inline alternatives, not subsections (fixed Day 3). |
| redwire_credit_original | Buyer, Parent, Company "defined twice" | Both definitions point to the same preamble text. |
| redwire_credit_original | Missing exhibits and schedules | Not filed on EDGAR, which is normal for credit agreements. |
| tva_facility_lease | "Discount Value" | Only in the definitions index in the table of contents; the body uses the defined "Discounted Value". read_document skips TOC entries, so the agent never sees it. |
| ford_arr_2026b | Blank "Article V" heading | Parser cosmetic, no tool depends on it. |
| redwire_credit_amend1 | §7.11 leverage ratios raised (5.00 → 6.00 in 2021, 3.75 → 5.00 in 2022, 3.00 → 4.00 in 2023) | Deliberate amendment, not a contradiction. A naive diff will flag these: this is the Day 6 false positive. |
| any | Glued words ("issatisfied", "DomesticSubsidiary", "anddeliver"…) | Conversion damage, still visible after parsing. "Componentshall" itself was split by the Day 3 fix. |
| redwire_credit_original | §6.11 closing list: "; and" after (iv) | Punctuation slip, not an inconsistency type we check. |
| redwire_credit_original | "Guarantee" outside Article XI, incl. "Collateral and Guarantee Requirement" | Correct: the generic defined term, or part of a separate defined term. |
| redwire_credit_original | §6.11(a)(i)(B) "Material Subsidiary" vs "Material Domestic Subsidiary" in (A), (C) | Both defined; Material Subsidiary includes foreign ones, and (B) covers pledges that reach them. Deliberate. |
| redwire_credit_amend1 | §2.02(a) one-day notice carve-out (y) | Stated openly by the amendment: intended. |
| redwire_credit_amend1 | §2.02(a) "three (3) Business Day" (singular) | Inherited from the original; grammar, not a contradiction. |
| redwire_credit_amend1 | §2.02(a) mention in amendment §3.02 | A condition that cites the clause, not a change to it. |
| redwire_credit_amend1 | §2.01(a)(i) no-reborrow narrowed to (a)(i) | (a)(ii) keeps its own no-reborrow sentence. |
| martin_credit_amend1 | §7.14(a), §7.14(c) ratio changes | Deliberate covenant relief. A naive diff will flag these. |
| martin_credit_original | §7.14(b) "until" vs (a)/(c) "through and including" | Different wording for the same quarter boundary. Not a contradiction: keep it as a must-not-flag trap. |
