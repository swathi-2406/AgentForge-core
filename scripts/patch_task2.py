"""Day 8 task 2: teach the eval task format about Tier 3. Run once from the repo root:

    python scripts/patch_day8_task2.py

Changes (each is skipped if it's already there, so running twice is safe):
  evals/harness/task_format.py    + naive_failure field, + tier 3 rules
  evals/tasks/_template.yaml      + naive_failure and filing: lines
  tests/unit/test_task_format.py  + 9 tests for the new rules
  tests/unit/test_eval_tasks.py   + 2 tests that check real tier 3 files against manifest and ground truth

Already supported since Day 7, so nothing to add:
  type: [a, b]          a must_flag accepts either type   (RWA-4)
  filing: <id>          required on every item once a task has two filings
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMAT = ROOT / "evals" / "harness" / "task_format.py"
TEMPLATE = ROOT / "evals" / "tasks" / "_template.yaml"
FORMAT_TESTS = ROOT / "tests" / "unit" / "test_task_format.py"
TASK_TESTS = ROOT / "tests" / "unit" / "test_eval_tasks.py"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")


# ---------- 1. task_format.py ----------

FIELD = (
    '    naive_failure: Optional[str] = None          '
    '# tier 3 only: what a plain text diff would wrongly report\n'
)

VALIDATOR = '''
    @model_validator(mode="after")
    def _tier3_rules(self) -> "EvalTask":
        """Tier 3 compares an amendment with its original, so it needs both filings and a
        written-down naive failure mode. That's what makes the tier exercise self-correction."""
        if self.tier == 3:
            if len(self.filings) != 2:
                raise ValueError("a tier 3 task needs two filings: [amendment, original]")
            if not self.naive_failure or len(self.naive_failure.strip()) < 20:
                raise ValueError("a tier 3 task needs naive_failure: one sentence on what a plain diff gets wrong")
        elif self.naive_failure is not None:
            raise ValueError("naive_failure is only for tier 3 tasks")
        return self
'''


def patch_format() -> str:
    s = read(FORMAT)
    if "naive_failure" in s:
        return "task_format.py      already patched"
    anchor = '    notes: str = ""\n'
    if s.count(anchor) != 1:
        raise SystemExit("task_format.py: expected exactly one 'notes: str = \"\"' line inside EvalTask")
    s = s.replace(anchor, anchor + FIELD)
    end = "\n\n\ndef load_task("
    if end not in s:
        raise SystemExit("task_format.py: couldn't find 'def load_task(' after the EvalTask class")
    s = s.replace(end, VALIDATOR.rstrip("\n") + end, 1)
    write(FORMAT, s)
    return "task_format.py      + naive_failure, + _tier3_rules"


# ---------- 2. _template.yaml ----------

def patch_template() -> str:
    lines = read(TEMPLATE).split("\n")
    if any("naive_failure" in l for l in lines):
        return "_template.yaml      already patched"

    def after(pred, new_lines):
        i = max(i for i, l in enumerate(lines) if pred(l))
        lines[i + 1:i + 1] = new_lines

    # must_flag item: after the location line (and its "or a list" comment, if present)
    loc = next(i for i, l in enumerate(lines) if l.lstrip().startswith('location: "<15.1>"'))
    j = loc + 1 if loc + 1 < len(lines) and lines[loc + 1].lstrip().startswith("#") else loc
    lines.insert(j + 1, "      filing: <filing_id>           # tier 3 only: which of the two filings this is in")
    # trap item: after its reason line
    after(lambda l: l.lstrip().startswith('reason: "<why'),
          ["      filing: <filing_id>           # tier 3 only"])
    # naive_failure: just before notes
    n = next(i for i, l in enumerate(lines) if l.startswith("notes:"))
    lines[n:n] = ["naive_failure: >-                  # tier 3 only, required there; delete for tier 1/2",
                  "  <What a plain text diff would wrongly report, in one sentence.>"]
    write(TEMPLATE, "\n".join(lines))
    return "_template.yaml      + naive_failure, + filing: lines"


# ---------- 3. test_task_format.py ----------

FORMAT_TEST_CODE = '''


# ---------- Day 8: tier 3 ----------

def t3(**over):
    task = {"id": "t3_demo_contradiction", "tier": 3, "filings": ["demo_amend1", "demo_original"],
            "task": "Check whether this amendment contradicts the original agreement.",
            "naive_failure": "A plain diff reports every intended ratio change as a contradiction.",
            "expected": {"must_flag": [{"type": ["term_mismatch", "dangling_reference"],
                                        "location": "1.01", "filing": "demo_amend1"}]},
            "ground_truth_ref": ["DEMO-4"], "optimal_steps": 5}
    task.update(over)
    return task


def test_tier3_task_loads():
    t = EvalTask.model_validate(t3())
    assert t.expected.must_flag[0].accepted_types() == {"term_mismatch", "dangling_reference"}


def test_tier3_clean_task_with_trap_loads():
    EvalTask.model_validate(t3(expected={"must_flag": [], "must_not_flag": [
        {"location": "7.14", "filing": "demo_amend1", "reason": "Deliberate covenant relief."}]},
        ground_truth_ref=[]))


@pytest.mark.parametrize("bad, why", [
    ({"naive_failure": None}, "no naive_failure"),
    ({"naive_failure": "diff fails"}, "naive_failure too short"),
    ({"filings": ["demo_amend1"]}, "only one filing"),
    ({"expected": {"must_flag": [{"type": "other", "location": "1.01"}]}}, "must_flag without filing"),
    ({"expected": {"must_flag": [], "must_not_flag": [{"location": "7.14", "reason": "intended"}]},
      "ground_truth_ref": []}, "trap without filing"),
    ({"expected": {"must_flag": [{"type": "other", "location": "1.01", "filing": "elsewhere"}]}},
     "filing not in the task"),
])
def test_bad_tier3_tasks_rejected(bad, why):
    with pytest.raises(ValidationError):
        EvalTask.model_validate(t3(**bad))


def test_naive_failure_only_for_tier3():
    with pytest.raises(ValidationError, match="only for tier 3"):
        EvalTask.model_validate(good(naive_failure="A plain diff reports every change as a problem."))
'''


def patch_format_tests() -> str:
    s = read(FORMAT_TESTS)
    if "def test_tier3_task_loads" in s:
        return "test_task_format.py already patched"
    write(FORMAT_TESTS, s.rstrip("\n") + FORMAT_TEST_CODE)
    return "test_task_format.py + 9 tier 3 tests"


# ---------- 4. test_eval_tasks.py ----------

TASK_TEST_CODE = '''


# ---------- Day 8: tier 3 files ----------

T3 = [t for t in TASKS if t.tier == 3]


def test_tier3_filings_are_an_amendment_and_its_original():
    entries = {e["id"]: e for e in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))}
    problems = []
    for t in T3:
        amend = [f for f in t.filings if entries.get(f, {}).get("role") == "amendment"]
        if len(amend) != 1 or entries[amend[0]].get("amends") not in t.filings:
            problems.append(f"{t.id}: {t.filings} is not [amendment, the original it amends]")
    assert not problems, "\\n".join(problems)


def test_tier3_refs_are_verified_tier3_rows():
    problems = []
    for t in T3:
        for ref in t.ground_truth_ref:
            row = GT.get(ref)
            if row is None or row["status"] != "verified" or row["tier"] != "3":
                problems.append(f"{t.id}: {ref} must be a verified tier 3 row, got {row}")
    assert not problems, "\\n".join(problems)
'''


def patch_task_tests() -> str:
    s = read(TASK_TESTS)
    if "def test_tier3_filings_are_an_amendment_and_its_original" in s:
        return "test_eval_tasks.py  already patched"
    for name in ("TASKS", "MANIFEST", "GT", "import yaml"):
        if name not in s:
            raise SystemExit(f"test_eval_tasks.py: expected to find {name!r}; paste the top of the file to Claude")
    write(TASK_TESTS, s.rstrip("\n") + TASK_TEST_CODE)
    return "test_eval_tasks.py  + 2 tier 3 file checks"


if __name__ == "__main__":
    for step in (patch_format, patch_template, patch_format_tests, patch_task_tests):
        print("  " + step())
    print("Done. Now run: pytest tests/unit -q -m \"not slow\"")