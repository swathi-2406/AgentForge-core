"""Day 7 task 0: the eval task format rejects anything a script couldn't score."""

import pytest
import yaml
from pydantic import ValidationError

from evals.harness.task_format import EvalTask, load_all, load_task, normalize_location


def good(**over):
    task = {"id": "t1_demo_dangling", "tier": 1, "filings": ["demo"],
            "task": "Check for references to sections that don't exist.",
            "expected": {"must_flag": [{"type": "dangling_reference", "location": "1.01"}]},
            "ground_truth_ref": ["DEMO-1"], "optimal_steps": 3}
    task.update(over)
    return task


def write(root, folder, data, name=None):
    path = root / folder / f"{name or data['id']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return path


def test_good_task_loads():
    assert EvalTask.model_validate(good()).expected.max_extra_findings == 0


def test_clean_task_loads():
    EvalTask.model_validate(good(expected={"must_flag": []}, ground_truth_ref=[]))


def test_list_of_types_allowed():
    t = EvalTask.model_validate(good(expected={"must_flag": [
        {"type": ["term_mismatch", "undefined_term"], "location": "3.3"}]}))
    assert t.expected.must_flag[0].accepted_types() == {"term_mismatch", "undefined_term"}


@pytest.mark.parametrize("bad, why", [
    ({"expected": {"must_flag": [{"type": "defined_term", "location": "1"}]}}, "unknown type"),
    ({"expected": {"must_flagg": []}}, "typo in key"),
    ({"expected": {"must_flag": []}}, "clean task citing a row"),
    ({"ground_truth_ref": []}, "finding with no row"),
    ({"id": "t2_demo"}, "id prefix vs tier"),
    ({"filings": ["a", "b"]}, "two filings, no filing:"),
    ({"expected": {"must_flag": [{"type": "other", "location": "1", "filing": "x"}]}}, "filing not in task"),
    ({"task": "check"}, "task too short"),
])
def test_bad_tasks_rejected(bad, why):
    with pytest.raises(ValidationError):
        EvalTask.model_validate(good(**bad))


def test_file_name_and_folder_must_match(tmp_path):
    with pytest.raises(ValueError, match="file name"):
        load_task(write(tmp_path, "tier1_cross_reference", good(), name="other"))
    with pytest.raises(ValueError, match="belongs in"):
        load_task(write(tmp_path, "tier2_defined_terms", good()))


def test_load_all_skips_template(tmp_path):
    write(tmp_path, "tier1_cross_reference", good())
    (tmp_path / "_template.yaml").write_text("id: <placeholder>")
    assert [t.id for t in load_all(tmp_path)] == ["t1_demo_dangling"]


def test_real_template_is_skipped():
    load_all()   # the shipped _template.yaml has placeholders and must not break loading


@pytest.mark.parametrize("raw, norm", [("Section 15.1(a)", "15.1"), ("§ 7.11", "7.11"),
                                       ("Schedule A", "schedule a"), ("1.01.", "1.01")])
def test_normalize_location(raw, norm):
    assert normalize_location(raw) == norm


def test_empty_file_gives_clear_error(tmp_path):
    path = tmp_path / "tier1_cross_reference" / "example.yaml"
    path.parent.mkdir()
    path.write_text("")
    with pytest.raises(ValueError, match="empty or not a YAML mapping"):
        load_task(path)


def test_trap_can_narrow_by_type_and_words():
    t = EvalTask.model_validate(good(expected={
        "must_flag": [{"type": "term_mismatch", "location": "1.01"}],
        "must_not_flag": [
            {"location": "1.01", "type": "duplicate_definition", "reason": "same preamble text"},
            {"mentions": ["Exhibit"], "reason": "exhibits not filed on EDGAR"}]}))
    assert t.expected.must_not_flag[1].location is None


def test_trap_needs_location_or_mentions():
    with pytest.raises(ValidationError):
        EvalTask.model_validate(good(expected={"must_flag": [{"type": "other", "location": "1"}],
                                               "must_not_flag": [{"reason": "too vague"}]}))


def test_must_flag_accepts_any_of_several_locations():
    t = EvalTask.model_validate(good(expected={"must_flag": [
        {"type": "term_mismatch", "location": ["Section 11.02", "11.11"]}]}))
    assert t.expected.must_flag[0].accepted_locations() == {"11.02", "11.11"}


@pytest.mark.parametrize("loc", [[], [""], ""])
def test_empty_location_rejected(loc):
    with pytest.raises(ValidationError):
        EvalTask.model_validate(good(expected={"must_flag": [{"type": "other", "location": loc}]}))