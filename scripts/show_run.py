"""Show one run's steps from the trace database.

    python -m scripts.show_run              # the latest run
    python -m scripts.show_run <run_id>     # a specific run
"""

import sys

from agentforge_core.tracing import trace_writer as tw
from agentforge_core.tracing.tool_calls import read_tool_calls


def main() -> None:
    run_id = sys.argv[1] if len(sys.argv) > 1 else tw.latest_run_id()
    if not run_id:
        print("No runs yet. Start one with trace_writer.start_run(...).")
        raise SystemExit(1)
    run = tw.read_run(run_id)
    print(f"run {run_id}  [{run['status']}]  {run['task'][:70]}")
    print(f"filings: {', '.join(run['filing_ids'])}   schema: {run['schema_version']}\n")
    print(f"{'step':>4} {'try':>3}  {'status':<7} {'ms':>8} {'bytes':>9}  tool / detail")
    for r in tw.step_rows(run_id):
        detail = r["empty_reason"] or (f"{r['error_kind']}: {r['error_type']}" if r["error_kind"] else "")
        size = f"{r['output_bytes']:,}" if r["output_bytes"] is not None else "-"
        print(f"{r['step_id']:>4} {r['attempt']:>3}  {r['status']:<7} {r['latency_ms']:>8.1f} {size:>9}  "
              f"{r['tool_name']}  {detail}")
    print(f"\nlinked tool calls in tool_calls.jsonl: {len(read_tool_calls(run_id))}")
    print(f"json: {tw.run_json_path(run_id)}")


if __name__ == "__main__":
    main()