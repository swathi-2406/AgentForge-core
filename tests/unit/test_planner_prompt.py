"""Tests for the planner prompt (Day 4 task 2). No LLM is called."""

import pytest

from agentforge_core import planner
from agentforge_core.plan import planner_tool_names
from agentforge_core.planner import PlannerError, build_messages, build_tool_menu
from agentforge_core.tools import REGISTRY

TVA = "tva_facility_lease"


def system_and_user(task="Check for dangling cross-references.", ids=(TVA,)):
    msgs = build_messages(task, list(ids))
    assert [m["role"] for m in msgs] == ["system", "user"]
    return msgs[0]["content"], msgs[1]["content"]


# ---------- tool menu ----------

def test_menu_lists_exactly_the_plannable_tools():
    menu = build_tool_menu()
    listed = [line[4:] for line in menu.splitlines() if line.startswith("### ")]
    assert listed == planner_tool_names()
    assert "flag_inconsistency" not in menu


def test_menu_shows_every_input_field():
    """If a tool gains or renames an arg, the menu follows automatically."""
    menu = build_tool_menu()
    for name in planner_tool_names():
        for field in REGISTRY[name].Input.model_fields:
            assert f'"{field}"' in menu, f"{name}.{field} missing from menu"


def test_menu_drops_pydantic_titles():
    assert '"title":"' not in build_tool_menu().replace('"title":{', "")


# ---------- user message ----------

def test_user_message_has_task_and_filings():
    _, user = system_and_user("Check defined terms.", ["ford_arr_2026b"])
    assert "Check defined terms." in user
    assert "- ford_arr_2026b" in user


def test_amendment_pair_is_explained():
    _, user = system_and_user(ids=["redwire_credit_amend1", "redwire_credit_original"])
    assert "redwire_credit_amend1 (amendment of redwire_credit_original)" in user
    assert "redwire_credit_original (original agreement)" in user


def test_no_document_text_is_read(monkeypatch):
    """Building the prompt must never run a tool, so no contract text gets in."""
    def boom(*a, **k):
        raise AssertionError("prompt building ran a tool")
    for name in REGISTRY:
        monkeypatch.setattr(REGISTRY[name], "run", boom)
    system_and_user()


# ---------- bad input fails early, before any LLM call ----------

@pytest.mark.parametrize("task, ids, msg", [
    ("", [TVA], "empty"),
    ("   ", [TVA], "empty"),
    ("Check xrefs.", [], "At least one"),
    ("Check xrefs.", ["redwire_credit_amend2"], "Unknown filing"),
])
def test_bad_input_raises_planner_error(task, ids, msg):
    with pytest.raises(PlannerError, match=msg):
        build_messages(task, ids)


# ---------- stability ----------

def test_same_input_same_prompt():
    assert build_messages("Check xrefs.", [TVA]) == build_messages("Check xrefs.", [TVA])


def test_prompt_version_is_set():
    assert planner.PROMPT_VERSION.startswith("planner-v")