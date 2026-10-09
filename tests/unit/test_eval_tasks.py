"""Day 7 task 4: every eval task is consistent with the manifest, the ground truth and the plan.

These run on the real files in evals/tasks/, so a bad edit to any task fails CI.
"""

import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from evals.harness.task_format import TASKS_DIR, in_scope, load_all, normalize_location

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "contracts" / "manifest.yaml"
GROUND_TRUTH = ROOT / "docs" / "ground_truth.md"
PLAN = TASKS_DIR / "PLAN.md"

TASKS = load_all()                                   # a task that doesn't load fails right here
T12 = [t for t in TASKS if t.tier in (1, 2)]


def ground_truth_rows() -> dict[str, dict]:
    """{'TVA-1': {'location': '§15.1', 'tier': '2', 'status': 'verified'}, ...}"""
    rows = {}
    for line in GROUND_TRUTH.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and re.fullmatch(r"[A-Z]+-\d+", cells[0]):
            rows[cells[0]] = {"location": cells[1], "tier": cells[-2], "status": cells[-1]}
    return rows


GT = ground_truth_rows()


def places(text: str) -> set[str]:
    """'§1.2 → Schedule A' -> {'1.2', 'schedule a'}"""
    found = re.findall(r"\d+(?:\.\d+)+|(?:Appendix|Schedule|Exhibit|Article)\s+[\w.]+|\b\d+\b", text)
    return {normalize_location(f) for f in found}


def test_tier_sizes_meet_day7_target():
    per_tier = Counter(t.tier for t in T12)
    assert 10 <= per_tier[1] <= 15, per_tier
    assert 10 <= per_tier[2] <= 15, per_tier


@pytest.mark.skipif(not MANIFEST.exists(), reason="no manifest in this checkout")
def test_filings_are_in_manifest():
    known = {e["id"] for e in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))}
    bad = {t.id: f for t in TASKS for f in t.filings if f not in known}
    assert not bad, f"unknown filing ids: {bad}"


def test_refs_are_verified_rows_of_the_same_tier():
    problems = []
    for t in T12:
        for ref in t.ground_truth_ref:
            row = GT.get(ref)
            if row is None:
                problems.append(f"{t.id}: {ref} is not in ground_truth.md")
            elif row["status"] != "verified":
                problems.append(f"{t.id}: {ref} is '{row['status']}', not verified")
            elif row["tier"] != str(t.tier):
                problems.append(f"{t.id}: {ref} is a tier {row['tier']} row in a tier {t.tier} task")
    assert not problems, "\n".join(problems)


def test_every_verified_tier1_and_tier2_row_has_a_task():
    used = {ref for t in T12 for ref in t.ground_truth_ref}
    missing = [r for r, row in GT.items() if row["status"] == "verified" and row["tier"] in ("1", "2") and r not in used]
    assert not missing, f"verified rows with no task: {missing}"


def test_one_must_flag_per_ground_truth_row():
    bad = {t.id: (len(t.expected.must_flag), len(t.ground_truth_ref))
           for t in T12 if len(t.expected.must_flag) != len(t.ground_truth_ref)}
    assert not bad, f"must_flag count != ground_truth_ref count: {bad}"


def test_must_flag_locations_come_from_their_rows():
    problems = []
    for t in T12:
        allowed = set().union(*(places(GT[r]["location"]) for r in t.ground_truth_ref if r in GT)) if t.ground_truth_ref else set()
        for f in t.expected.must_flag:
            if not f.accepted_locations() & allowed:
                problems.append(f"{t.id}: {sorted(f.accepted_locations())} not in rows' locations {sorted(allowed)}")
    assert not problems, "\n".join(problems)


def test_must_flag_locations_are_inside_scope():
    problems = [f"{t.id}: {f.location} is outside scope '{t.scope}'"
                for t in T12 for f in t.expected.must_flag
                if not any(in_scope(loc, t.scope) for loc in (f.location if isinstance(f.location, list) else [f.location]))]
    assert not problems, "\n".join(problems)


@pytest.mark.skipif(not PLAN.exists(), reason="no PLAN.md")
def test_plan_and_task_files_agree():
    planned = set(re.findall(r"^\| \d+ \| (t[12]_\w+) \|", PLAN.read_text(encoding="utf-8"), re.M))
    written = {t.id for t in T12}
    assert planned == written, f"planned only: {sorted(planned - written)} · files only: {sorted(written - planned)}"


@pytest.mark.parametrize("loc, scope, ok", [
    ("7.02", "Article VII", True), ("11.11", "Article XI", True), ("3.8", "Section 3", True),
    ("13.2", "Section 3", False), ("Appendix A", "whole_document", True), ("Section 15.1(a)", "Section 15", True),
    ("Schedule B", "Schedule B", True), ("1.03", "Article II", False),
])
def test_in_scope(loc, scope, ok):
    assert in_scope(loc, scope) is ok