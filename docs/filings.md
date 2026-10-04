# Chosen filings

Four real SEC EDGAR exhibits power the eval tiers. All are listed in `data/contracts/manifest.yaml` and downloaded with `python scripts/fetch_edgar_filing.py sync`.

| id | tier | accession |
| --- | --- | --- |
| `tva_facility_lease` | easy | 0001376986-24-000029 |
| `ford_arr_2026b` | medium | 0001104659-26-086229 |
| `redwire_credit_original` | hard (original) | 0001819810-22-000025 |
| `redwire_credit_amend1` | hard (amendment) | 0001819810-22-000025 |

"Spotted so far" items are candidates found on a first read. They become eval ground truth only after they're confirmed against the full text on Day 3.

---

## Easy: TVA facility lease-purchase agreement

**Source:** [Exhibit 10.24 to TVA's 10-K for FY2024](https://www.sec.gov/Archives/edgar/data/1376986/000137698624000029/exhibit1024.htm)

This is a facility lease-purchase agreement dated as of October 2, 2024, between Johnsonville Aeroderivative Combustion Turbine Generation LLC (Owner Lessor) and the Tennessee Valley Authority (Facility Lessee). It covers the Johnsonville combustion turbine facility in Humphreys County, Tennessee. It fits the easy tier because it uses one clean "Section X.Y" numbering scheme across 23 sections and is dense with internal cross-references, so a single pass that extracts every reference and diffs it against the real section map can find dangling or misdirected references without any reasoning about meaning.

**Example checks**
- Find every "Section X.Y(z)" reference that points to a section or clause that doesn't exist, or to the wrong clause.
- Compare table-of-contents headings against the headings in the body.
- Find enumerated lists that skip a numeral.

**Spotted so far**
- §15.1 refers twice to the buy-out election in §13.2(d), but that election is in §13.2(e). Clause (d) covers transferring Membership Interests.
- §4.1(a) numbers its list (i)–(iv), then jumps to (vi).
- §13.2 says "Supplement Lease Rent" instead of the defined term "Supplemental Lease Rent".
- §3.3 says "Indenture Trustee" where the rest of the agreement says "Lease Indenture Trustee". Check whether Appendix A defines both.
- The table of contents lists Section 23.10 with no heading. The body calls it "Headings and Table of Contents".

---

## Medium: Ford Credit asset representations review agreement

**Source:** [Exhibit 10.10, Ford Credit Auto Lease Trust 2026-B](https://www.sec.gov/Archives/edgar/data/1519881/000110465926086229/tm2620301d11_ex10-10.htm)

This is an asset representations review agreement dated as of July 1, 2026, among Ford Credit Auto Lease Trust 2026-B (Issuer), Ford Motor Credit Company LLC (Servicer) and Clayton Fixed Income Services LLC (Asset Representations Reviewer). It sets out when and how the reviewer tests leases against Ford's representations and warranties. It fits the medium tier because checking its defined terms takes several steps: Section 1.2 defines terms by pointing into other sections and schedules, while most capitalized terms are defined in outside documents. So the agent has to build the term list, separate this agreement's own terms from imported ones, and then scan the whole text for mismatches.

**Example checks**
- Confirm every "has the meaning stated in Section X" in §1.2 points to a place that actually defines the term.
- Find places where a defined term is replaced by a different, undefined term.
- Confirm every Schedule A or Schedule B reference points to the right schedule.

**Spotted so far**
- §3.8(b) says the Tests are listed in Schedule A. They're in Schedule B, which is what §3.4(a) says.
- §3.3(b) uses "Review Receivable" twice instead of "Review Lease". It looks like leftover auto-loan template text.
- §1.2 says "Contract" is defined in Schedule A, but Schedule A never defines it.
- Schedule B cites the "Exchange Note Purchase Agreement", while §3.7 cites the "Exchange Note Sale Agreement". Check whether these are two separate documents before scoring this one.

**Scoping note:** Only score terms this agreement defines itself. Terms from Appendix 1 or Appendix A live in other documents and aren't findings.

---

## Hard (original): Redwire credit agreement

**Source:** [Exhibit 10.13 to Redwire's 10-K for FY2021](https://www.sec.gov/Archives/edgar/data/1819810/000181981022000025/exhibit1013redwire-credita.htm)

This is a credit agreement dated as of October 28, 2020, among Cosmos Finance, LLC (Parent), Cosmos Acquisition, LLC (Lead Borrower), Adams Street Credit Advisors LP (Administrative Agent and Collateral Agent) and the lenders. It provides the term loan, delayed draw and revolving facilities used to buy Roccor. It is the baseline for the hard tier: a 200+ page agreement in which the agent must find the exact clauses the First Amendment rewrites, then compare old and new text clause by clause.

---

## Hard (amendment): Redwire first amendment to credit agreement

**Source:** [Exhibit 10.14, same filing](https://www.sec.gov/Archives/edgar/data/1819810/000181981022000025/exhibit1014redwire-firstam.htm)

This is the First Amendment to the credit agreement above, dated as of February 17, 2021. It adds $32,000,000 of incremental "First Amendment Term Loans" to fund the acquisition of Deployable Space Systems, and it rewrites §§1.01, 2.01(a)(i), 2.02(a), 2.06(b), 2.07(a), 6.15(a) and 7.11 of the original. It fits the hard tier because every amended clause is restated in full. A naive text diff against the original will flag large amounts of repeated boilerplate as changes. The agent has to recover by first listing the clauses the amendment says it changes, then diffing only those.

**Example checks**
- Confirm every clause the amendment changes actually exists in the original.
- Find changes inside a restated clause that the amendment doesn't call out, for example whether the §2.02(a) notice period for base rate loans changed.
- Check the new §7.11 schedule for internal consistency.

**Spotted so far**
- §7.11 says testing starts with the period ending March 30, 2021, but its table starts at March 31, 2021. Check whether the original has the same slip.
- §4.01 uses "Amended Credit Agreement", which is never defined. The defined terms are "Existing Credit Agreement" and "Credit Agreement".
- §1.03(a) numbers its list (i)–(iv), then jumps to (vi).
- §5.08 says "this Agreement" where it means "this Amendment".

**Expected naive failure:** A direct full-text diff will report the restated §1.01 definitions and §2.07(a) repayment text as changed, almost entirely because of repeated wording.

**Answer key (not agent input):** Redwire later filed a copy of the credit agreement conformed through the First Amendment (Third Amendment exhibit, accession 0001819810-22-000005). Use it only to check hard-tier ground truth. Never pass it to the agent.