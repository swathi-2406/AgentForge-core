"""Day 8: the hard tier is real and sized as the playbook says (8-12 tasks, a minority overall)."""
from evals.harness.task_format import load_all


def test_tier3_has_8_to_12_tasks_and_stays_a_minority():
    tasks = load_all()
    t3 = [t for t in tasks if t.tier == 3]
    assert 8 <= len(t3) <= 12, f"tier 3 has {len(t3)} tasks"
    assert len(t3) < len(tasks) / 2, "the hard tier should be a minority of the set"
    assert any(t.expected.must_flag for t in t3), "at least one tier 3 task needs a real contradiction"