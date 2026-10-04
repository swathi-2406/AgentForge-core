"""Tests for fetch_related_filing and the shared EDGAR module. The network is always mocked."""

import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml
from pydantic import ValidationError

from agentforge_core import edgar
from agentforge_core.tools import call_tool
from agentforge_core.tools.base import ToolError
from agentforge_core.tools.read_document import get_root

ORIG_URL = "https://www.sec.gov/Archives/edgar/data/1819810/000181981022000025/exhibit1013redwire-credita.htm"
AMEND_URL = "https://www.sec.gov/Archives/edgar/data/1819810/000181981022000025/exhibit1014redwire-firstam.htm"
HTML = b"<p>ARTICLE I</p><p>Section 1.01 Defined Terms. Text.</p><p>Section 2.02 Borrowings. Three Business Days.</p>"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Fake repo: amendment on disk, original NOT on disk yet."""
    monkeypatch.setenv("AGENTFORGE_ROOT", str(tmp_path))
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test User test@example.com")
    raw = tmp_path / "data" / "contracts" / "raw"
    raw.mkdir(parents=True)
    (raw / "0001819810-22-000025_exhibit1014redwire-firstam.htm").write_bytes(HTML)   # the amendment
    entries = [
        {"id": "orig", "url": ORIG_URL, "tier": "hard", "role": "original"},
        {"id": "amend", "url": AMEND_URL, "tier": "hard", "role": "amendment", "amends": "orig",
         "local_path": "data/contracts/raw/0001819810-22-000025_exhibit1014redwire-firstam.htm"},
        {"id": "lonely", "url": AMEND_URL.replace("firstam", "other"), "tier": "hard"},
    ]
    (tmp_path / "data" / "contracts" / "manifest.yaml").write_text(yaml.safe_dump(entries))
    return tmp_path


@pytest.fixture
def fake_sec():
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers["User-Agent"]))
        return mock.Mock(status_code=200, content=HTML, raise_for_status=lambda: None)

    with mock.patch.object(edgar.requests, "get", fake_get), mock.patch.object(edgar, "MIN_INTERVAL_S", 0):
        yield calls


def manifest(root):
    return {e["id"]: e for e in yaml.safe_load((root / "data" / "contracts" / "manifest.yaml").read_text())}


def test_fetches_then_caches(repo, fake_sec):
    first = call_tool("fetch_related_filing", {"filing_id": "amend"})
    assert (first.original_id, first.status, first.section_count) == ("orig", "fetched", 3)
    assert fake_sec == [(ORIG_URL, "Test User test@example.com")]
    m = manifest(repo)["orig"]
    assert m["accession"] == "0001819810-22-000025" and m["sha256"] and (repo / m["local_path"]).exists()

    second = call_tool("fetch_related_filing", {"filing_id": "amend"})
    assert second.status == "cached" and len(fake_sec) == 1     # no second download


def test_link_a_new_original_by_url(repo, fake_sec):
    url = "https://www.sec.gov/Archives/edgar/data/1/000000000000000001/original.htm"
    out = call_tool("fetch_related_filing", {"filing_id": "lonely", "original_url": url})
    assert out.original_id == "lonely_original" and out.status == "fetched"
    m = manifest(repo)
    assert m["lonely"]["amends"] == "lonely_original" and m["lonely_original"]["role"] == "original"


@pytest.mark.parametrize("url", [
    "https://evil.example.com/Archives/edgar/data/1/000000000000000001/x.htm",
    "http://www.sec.gov/Archives/edgar/data/1/000000000000000001/x.htm",
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
    "file:///etc/passwd",
])
def test_non_edgar_url_rejected_before_any_network(repo, fake_sec, url):
    with pytest.raises(ValidationError):
        call_tool("fetch_related_filing", {"filing_id": "lonely", "original_url": url})
    assert fake_sec == []


def test_no_link_and_no_url(repo):
    with pytest.raises(ToolError, match="no 'amends' link"):
        call_tool("fetch_related_filing", {"filing_id": "lonely"})


def test_conflicting_url_for_existing_link(repo, fake_sec):
    other = "https://www.sec.gov/Archives/edgar/data/1/000000000000000001/other.htm"
    with pytest.raises(ToolError, match="already linked"):
        call_tool("fetch_related_filing", {"filing_id": "amend", "original_url": other})


def test_missing_user_agent_only_matters_when_downloading(repo, fake_sec, monkeypatch):
    monkeypatch.setenv("EDGAR_USER_AGENT", "")
    with pytest.raises(ToolError, match="EDGAR_USER_AGENT"):
        call_tool("fetch_related_filing", {"filing_id": "amend"})
    assert fake_sec == []


def test_tampered_manifest_url_never_fetched(repo, fake_sec):
    path = repo / "data" / "contracts" / "manifest.yaml"
    entries = yaml.safe_load(path.read_text())
    entries[0]["url"] = "https://evil.example.com/Archives/edgar/data/1/000000000000000001/x.htm"
    path.write_text(yaml.safe_dump(entries))
    with pytest.raises(ToolError, match="sec.gov"):
        call_tool("fetch_related_filing", {"filing_id": "amend"})
    assert fake_sec == []


@pytest.mark.parametrize("args", [{}, {"filing_id": "AMEND"}, {"filing_id": "amend", "extra": 1}])
def test_bad_args(args):
    with pytest.raises(ValidationError):
        call_tool("fetch_related_filing", args)

# ---------- the Day 1 script still works on top of the shared module ----------

def test_script_sync(repo, fake_sec):
    from typer.testing import CliRunner
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import fetch_edgar_filing as script
    first = CliRunner().invoke(script.app, ["sync"]).output
    assert "fetched  orig" in first and "skipped  amend" in first   # amendment was already on disk
    second = CliRunner().invoke(script.app, ["sync"]).output
    assert "fetched" not in second and "skipped  orig" in second

# ---------- your real filings (no network: the original is already on disk) ----------

def downloaded(fid):
    m = get_root() / "data" / "contracts" / "manifest.yaml"
    if not m.exists():
        return False
    e = next((e for e in yaml.safe_load(m.read_text()) or [] if e["id"] == fid), None)
    return bool(e and e.get("local_path") and (get_root() / e["local_path"]).exists())


@pytest.mark.skipif(not downloaded("redwire_credit_original"), reason="Redwire not downloaded")
def test_redwire_amendment_finds_original(fake_sec):
    out = call_tool("fetch_related_filing", {"filing_id": "redwire_credit_amend1"})
    assert (out.original_id, out.status) == ("redwire_credit_original", "cached")
    assert out.section_count > 100 and fake_sec == []