"""Shared test setup: no test ever writes traces into your real data/traces folder."""

import pytest


@pytest.fixture(autouse=True)
def _traces_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTFORGE_TRACES_DIR", str(tmp_path / "traces"))