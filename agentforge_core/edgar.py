# """Everything that talks to SEC EDGAR, shared by scripts/fetch_edgar_filing.py and fetch_related_filing.

# Rules enforced here (SEC fair-access policy + playbook):
#     - Only https://www.sec.gov/Archives/edgar/data/... document URLs are accepted.
#     - Every request sends EDGAR_USER_AGENT (name + email) from .env.
#     - Requests are spaced at least 0.2s apart (max 5/sec, under SEC's 10/sec limit), with backoff on 429/503.
# """

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from dotenv import load_dotenv

from agentforge_core.paths import get_root

ALLOWED_HOSTS = {"www.sec.gov", "sec.gov"}
EDGAR_PATH = re.compile(
    r"^/Archives/edgar/data/(?P<cik>\d+)/(?P<acc>\d{18})/(?P<file>[^/]+\.(?:htm|html|txt))$", re.IGNORECASE)
TIERS = {"easy", "medium", "hard"}
ROLES = {"original", "amendment"}
MIN_INTERVAL_S = 0.2
FIELD_ORDER = ["id", "accession", "url", "tier", "role", "amends",
               "cik", "local_path", "sha256", "bytes", "fetched_at"]

_last_request = 0.0


class EdgarError(Exception):
    """Bad URL, missing User-Agent, or SEC refused the request."""


def raw_dir(root: Path | None = None) -> Path:
    return (root or get_root()) / "data" / "contracts" / "raw"


def manifest_path(root: Path | None = None) -> Path:
    return (root or get_root()) / "data" / "contracts" / "manifest.yaml"


def parse_edgar_url(url: str) -> dict:
    """Validate an EDGAR archive URL and pull out CIK, accession number and filename."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in ALLOWED_HOSTS:
        raise EdgarError(f"Not an https sec.gov URL: {url}")
    match = EDGAR_PATH.match(parsed.path)
    if not match:
        raise EdgarError(f"Not an EDGAR archive document path: {parsed.path}")
    acc = match["acc"]
    return {"cik": match["cik"], "accession": f"{acc[:10]}-{acc[10:12]}-{acc[12:]}", "filename": match["file"]}


def get_user_agent(root: Path | None = None) -> str:
    load_dotenv((root or get_root()) / ".env")
    ua = os.getenv("EDGAR_USER_AGENT", "").strip().strip('"')
    if "@" not in ua or " " not in ua:
        raise EdgarError('Set EDGAR_USER_AGENT in .env, e.g. "Your Name you@email.com"')
    return ua


def download(url: str, user_agent: str, retries: int = 3) -> bytes:
    """GET with spacing between calls and backoff on SEC throttling responses."""
    global _last_request
    parse_edgar_url(url)  # never fetch anything that isn't an EDGAR document
    for attempt in range(retries + 1):
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
        if resp.status_code in (429, 503) and attempt < retries:
            time.sleep(2 ** (attempt + 1))
            continue
        if resp.status_code == 403:
            raise EdgarError("SEC returned 403. Check EDGAR_USER_AGENT has a real name and email.")
        resp.raise_for_status()
        if not resp.content or b"Request Rate Threshold Exceeded" in resp.content:
            raise EdgarError(f"Empty or throttled response from {url}")
        return resp.content
    raise EdgarError(f"Gave up after {retries} retries: {url}")


def load_manifest(root: Path | None = None) -> list[dict]:
    path = manifest_path(root)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise EdgarError("manifest.yaml must be a YAML list of filing entries")
    return data


def save_manifest(entries: list[dict], root: Path | None = None) -> None:
    ordered = [{k: e[k] for k in FIELD_ORDER if e.get(k) is not None}
               | {k: v for k, v in e.items() if k not in FIELD_ORDER} for e in entries]
    path = manifest_path(root)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


def validate(entries: list[dict]) -> None:
    ids = [e.get("id") for e in entries]
    if None in ids or len(ids) != len(set(ids)):
        raise EdgarError("Every manifest entry needs a unique id")
    for e in entries:
        if e.get("tier") not in TIERS:
            raise EdgarError(f"{e['id']}: tier must be one of {sorted(TIERS)}")
        if e.get("role") and e["role"] not in ROLES:
            raise EdgarError(f"{e['id']}: role must be one of {sorted(ROLES)}")
        if e.get("amends") and e["amends"] not in ids:
            raise EdgarError(f"{e['id']}: amends unknown id '{e['amends']}'")
        info = parse_edgar_url(e["url"])
        if e.get("accession") and e["accession"] != info["accession"]:
            raise EdgarError(f"{e['id']}: accession {e['accession']} doesn't match its URL")


def target_path(entry: dict, root: Path | None = None) -> Path:
    """Where a filing lives on disk: data/contracts/raw/<accession>_<filename>."""
    info = parse_edgar_url(entry["url"])
    return raw_dir(root) / f"{info['accession']}_{info['filename']}"


def fetch_entry(entry: dict, user_agent: str | None, force: bool = False, root: Path | None = None) -> str:
    """Download one entry if needed and fill in its metadata. Returns 'fetched' or 'skipped'.
    user_agent may be None when the file is already on disk."""
    root = root or get_root()
    info = parse_edgar_url(entry["url"])
    target = target_path(entry, root)
    entry.update(accession=info["accession"], cik=info["cik"], local_path=target.relative_to(root).as_posix())
    if target.exists() and not force:
        status, content = "skipped", target.read_bytes()
    else:
        if not user_agent:
            raise EdgarError("A download is needed but no User-Agent was given")
        content = download(entry["url"], user_agent)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(content)
        tmp.replace(target)
        entry["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status = "fetched"
    entry["sha256"] = hashlib.sha256(content).hexdigest()
    entry["bytes"] = len(content)
    return status