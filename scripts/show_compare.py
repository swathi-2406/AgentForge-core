"""Run both comparison tools on the Redwire pair and print a short summary. No LLM.

    python -m scripts.show_compare
"""

from agentforge_core.tools import call_tool

PAIR = {"original_id": "redwire_credit_original", "amendment_id": "redwire_credit_amend1"}

naive = call_tool("diff_filings", PAIR)
print(f"NAIVE  diff_filings: {naive.total_differences} differences "
      f"({naive.changed} changed, {naive.added} added, {naive.identical} identical)")
for h in naive.hunks[:2]:
    print(f"   amendment: {h.amendment_sentence[:90]}")
    print(f"   original:  {h.closest_original[:90] or '(none)'}\n")

out = call_tool("compare_amended_clauses", PAIR)
print(f"TARGETED  compare_amended_clauses: {out.total_directives} directives -> "
      f"{out.changed} changed, {out.unchanged} unchanged, {out.added} added, "
      f"{out.missing_in_original} missing")
for c in out.clauses:
    print(f"   {c.ref:34.34} {c.status:10} {c.located_by:15} "
          f"changes={len(c.substantive_changes):<3} cosmetic={c.cosmetic_differences}")
for f in out.internal_flags:
    print(f"   FLAG {f.ref}: {f.detail}  ({'in original too' if f.in_original else 'introduced by amendment'})")
for w in out.warnings:
    print(f"   warning: {w}")