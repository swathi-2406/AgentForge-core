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

## redwire_credit_amend1 (hard, amendment)

| ID | Location | Type | Finding | Tier | Status |
|---|---|---|---|---|---|
| RWA-1 | Article II (new §7.11 text) | date | Lead-in says the first Test Period ends March 30, 2021; the amended table uses March 31 (2021-2024). The original used March 30 in both places, so the amendment fixed the table but not the lead-in. | 3 | verified |
| RWA-2 | §4.01 | defined_term | Uses "Amended Credit Agreement" once; it is never defined. | 2 | verified |
| RWA-3 | §1.03 | list_numbering | Roman list jumps from (iv) to (vi); no (v) in the section. | 1 | verified |

**RWA-1, settled on Day 3.** The amendment introduced this contradiction. It wasn't in the original:

| | Lead-in | Table March dates |
|---|---|---|
| Original §7.11 | March 30, 2021 | March 30 (every year) |
| Amended §7.11 | March 30, 2021 | March 31 (every year) |

## Hard-tier comparison targets

The clauses the amendment changes. Tier 3 tasks compare these against the original, and the
correct answer for some will be "no contradiction". That's a valid, useful task too.

| Amended clause | Exists in original | Contradiction? |
|---|---|---|
| §1.01 (definitions) | yes (Day 2 cross-ref check) | to-check |
| §2.01(a)(i) | yes | to-check |
| §2.02(a) | yes | to-check |
| §2.06(b) | yes | to-check |
| §2.07(a) | yes | to-check |
| §6.15(a) | yes | to-check |
| §7.11 | yes | yes: RWA-1. The ratio changes are intended (see Not findings). |

## Not findings

Things that look like findings but aren't. Eval scoring must not count them.

| Filing | What | Why it's not a finding |
|---|---|---|
| redwire_credit_original | "Admaicntistrative Agent" | Conversion damage: the missing word "act" was shuffled into it. |
| any | "Componentshall" and other glued words | Conversion damage from EDGAR HTML. |
| redwire_credit_original | §6.01 inline (w)(x)(y)(z) | Inline alternatives, not subsections (fixed Day 3). |
| redwire_credit_original | Buyer, Parent, Company "defined twice" | Both definitions point to the same preamble text. |
| redwire_credit_original | Missing exhibits and schedules | Not filed on EDGAR, which is normal for credit agreements. |
| tva_facility_lease | "Discount Value" | Only in the definitions index in the table of contents; the body uses the defined "Discounted Value". read_document skips TOC entries, so the agent never sees it. |
| ford_arr_2026b | Blank "Article V" heading | Parser cosmetic, no tool depends on it. |
| redwire_credit_amend1 | §7.11 leverage ratios raised (5.00 → 6.00 in 2021, 3.75 → 5.00 in 2022, 3.00 → 4.00 in 2023) | Deliberate amendment, not a contradiction. A naive diff will flag these: this is the Day 6 false positive. |