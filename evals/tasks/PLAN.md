# Day 7 eval task plan

One line per task. Every `real` row cites a verified row in `docs/ground_truth.md`.
Every `clean` row must be **proved** clean (tool output + your read) before its YAML is written.

Status: `planned` → `proved` (clean/scoped/trap only) → `written`

Filings: TVA = tva_facility_lease · FORD = ford_arr_2026b · RWO = redwire_credit_original · RWA = redwire_credit_amend1

## Tier 1 · single pass (target 12)

| # | id | Filing | Kind | Rows | Scope | Expect | Status |
|---|---|---|---|---|---|---|---|
| 1 | t1_tva_numbering_whole | TVA | real | TVA-2 | whole_document | flag 4.1 · [number_mismatch, other] | planned |
| 2 | t1_rwo_dangling_whole | RWO | real | RWO-1 | whole_document | flag 1.01 · dangling_reference · trap: mentions Exhibit/Schedule (not filed) | planned |
| 3 | t1_rwa_numbering_whole | RWA | real | RWA-3 | whole_document | flag 1.03 · [number_mismatch, other] | planned |
| 4 | t1_tva_dangling_whole | TVA | clean | – | whole_document | nothing | planned |
| 5 | t1_ford_dangling_whole | FORD | clean | – | whole_document | nothing | planned |
| 6 | t1_rwa_dangling_whole | RWA | clean | – | whole_document | nothing | planned |
| 7 | t1_ford_numbering_whole | FORD | clean | – | whole_document | nothing | planned |
| 8 | t1_rwo_dangling_2_05 | RWO | scoped | – | 2.05 | nothing · traps: 1.01 (outside scope), mentions Exhibit/Schedule | planned |
| 9 | t1_tva_dangling_3_4 | TVA | scoped | – | 3.4 | nothing · trap: references to other documents | planned |
| 10 | t1_ford_dangling_sched_b | FORD | scoped | – | Schedule B | nothing · trap: references to other documents | planned |
| 11 | t1_rwa_numbering_art2 | RWA | scoped | – | Article II | nothing · trap: 1.03 (outside scope) | planned |
| 12 | t1_rwo_numbering_art6 | RWO | trap | – | Article VI | nothing · trap: 6.01 inline (w)(x)(y)(z) | planned |

## Tier 2 · multi-step reading (target 12)

| # | id | Filing | Kind | Rows | Scope | Expect | Status |
|---|---|---|---|---|---|---|---|
| 13 | t2_tva_reference_s15 | TVA | real | TVA-1 | Section 15 | flag 15.1 · wrong_reference | planned |
| 14 | t2_tva_terms_whole | TVA | real | TVA-3, TVA-4 | whole_document | flag 13.2 + 3.3 · term_mismatch | planned |
| 15 | t2_tva_terms_3_3 | TVA | real | TVA-4 | 3.3 | flag 3.3 · term_mismatch | planned |
| 16 | t2_ford_reference_s3 | FORD | real | FORD-1 | Section 3 | flag 3.8 · wrong_reference | planned |
| 17 | t2_ford_terms_3_3 | FORD | real | FORD-2 | 3.3 | flag 3.3 · term_mismatch | planned |
| 18 | t2_ford_terms_whole | FORD | real | FORD-2, FORD-3 | whole_document | flag 3.3 term_mismatch + 1.2 undefined_term | planned |
| 19 | t2_rwo_terms_art7 | RWO | real | RWO-2 | Article VII | flag 7.02 · term_mismatch | planned |
| 20 | t2_rwo_terms_whole | RWO | real | RWO-2, RWO-3, RWO-4 | whole_document | flag 7.02 + 1.01 + [11.02, 11.11] · term_mismatch · all Redwire traps apply | planned |
| 21 | t2_rwa_terms_whole | RWA | real | RWA-2 | whole_document | flag 4.01 · undefined_term | planned |
| 22 | t2_rwo_trap_defs | RWO | real + trap | RWO-3 | 1.01 | flag 1.01 · term_mismatch · trap: 1.01 duplicate_definition (Buyer/Parent/Company) | planned |
| 23 | t2_rwo_trap_9_01 | RWO | trap | – | 9.01 | nothing · trap: mentions "Admaicntistrative" | planned |
| 24 | t2_rwo_trap_6_11 | RWO | trap | – | 6.11 | nothing · traps: "Collateral and Guarantee Requirement", glued words, "Material Subsidiary" in (B) | planned |
| 25 | t2_rwo_terms_art11 | RWO | real | RWO-4 | Article XI | flag [11.02, 11.11] · term_mismatch | planned |

## Rules I'm holding myself to

- Wider scope = harder task. Mix section, article and whole-document scopes in Tier 2.
- If a scope contains another real finding **of the kind the task asks about**, add it to `must_flag`.
- If a "clean" check finds something real, it becomes a new ground-truth row, not a clean task.
- Traps must be in text the agent can actually see (not the TOC-only "Discount Value").

Tier 3 (8–12 tasks) is planned on Day 8.
