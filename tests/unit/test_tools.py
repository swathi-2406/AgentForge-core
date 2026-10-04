"""Day 2 definition of done, for all 7 tools at once:

  - every tool is registered and callable purely by name + a dict of args (no tool imports here)
  - every tool's schema is ready for the Day 4 planner prompt
  - every tool rejects bad args, survives a malformed filing, and runs on every real filing
"""

import json

import pytest
import yaml
from pydantic import ValidationError

from agentforge_core.tools import REGISTRY, call_tool, list_tool_specs
from agentforge_core.tools.base import ToolError
from agentforge_core.tools.read_document import get_root

TOOLS = {"read_document", "extract_definitions", "extract_cross_references", "extract_section_map",
         "extract_dates", "fetch_related_filing", "flag_inconsistency"}


def minimal_args(tool: str, filing_id: str) -> dict:
    if tool == "flag_inconsistency":
        return {"filing_id": filing_id, "location": "preamble", "type": "other", "severity": "low",
                "description": "Day 2 contract test finding, safe to ignore."}
    return {"filing_id": filing_id}

# ---------- registry and schemas ----------

def test_exactly_the_seven_playbook_tools():
    assert set(REGISTRY) == TOOLS


@pytest.mark.parametrize("spec", list_tool_specs(), ids=lambda s: s["name"])
def test_spec_ready_for_planner(spec):
    assert len(spec["description"]) >= 40
    assert "filing_id" in spec["input_schema"]["properties"]
    assert "filing_id" in spec["input_schema"].get("required", [])
    assert spec["input_schema"].get("additionalProperties") is False      # typos fail loudly
    assert len(json.dumps(spec)) < 20_000                                  # fits in a prompt

# ---------- bad args, for every tool ----------

BAD_ARGS = {
    "missing_filing_id": lambda tool: {k: v for k, v in minimal_args(tool, "x").items() if k != "filing_id"},
    "path_trick": lambda tool: minimal_args(tool, "../../etc/passwd"),
    "extra_field": lambda tool: {**minimal_args(tool, "x"), "not_a_field": 1},
}


@pytest.mark.parametrize("tool", sorted(TOOLS))
@pytest.mark.parametrize("case", sorted(BAD_ARGS))
def test_bad_args_rejected(tool, case):
    with pytest.raises(ValidationError):
        call_tool(tool, BAD_ARGS[case](tool))

# ---------- malformed filing, for every tool ----------

@pytest.fixture
def malformed_repo(tmp_path, monkeypatch):
    """A 'filing' with no headings, no definitions, no dates: tools must answer, not crash."""
    raw = tmp_path / "data" / "contracts" / "raw"
    raw.mkdir(parents=True)
    (raw / "blob.htm").write_text("<html><body><p>Just one paragraph with nothing in it.</p></body></html>")
    (tmp_path / "data" / "contracts" / "manifest.yaml").write_text(yaml.safe_dump([
        {"id": "blob", "url": "https://www.sec.gov/Archives/edgar/data/1/000000000000000001/blob.htm",
         "tier": "easy", "local_path": "data/contracts/raw/blob.htm"}]))
    monkeypatch.setenv("AGENTFORGE_ROOT", str(tmp_path))


@pytest.mark.parametrize("tool", sorted(TOOLS - {"fetch_related_filing"}))
def test_malformed_filing_does_not_crash(malformed_repo, tool):
    out = call_tool(tool, minimal_args(tool, "blob"))
    assert out is not None


def test_fetch_related_on_non_amendment_explains(malformed_repo):
    with pytest.raises(ToolError, match="no 'amends' link"):
        call_tool("fetch_related_filing", {"filing_id": "blob"})

# ---------- every tool on every real filing ----------

def real_filings() -> list[dict]:
    m = get_root() / "data" / "contracts" / "manifest.yaml"
    if not m.exists():
        return []
    return [e for e in yaml.safe_load(m.read_text()) or []
            if e.get("local_path") and (get_root() / e["local_path"]).exists()]


REAL = real_filings()


@pytest.mark.skipif(not REAL, reason="no filings downloaded")
@pytest.mark.parametrize("tool", sorted(TOOLS))
@pytest.mark.parametrize("entry", REAL or [None], ids=lambda e: e["id"] if e else "none")
def test_every_tool_on_every_real_filing(tool, entry):
    if tool == "fetch_related_filing" and not entry.get("amends"):
        with pytest.raises(ToolError):
            call_tool(tool, {"filing_id": entry["id"]})
        return
    out = call_tool(tool, minimal_args(tool, entry["id"]))   # flag_inconsistency writes to a temp folder (conftest)
    assert out is not None