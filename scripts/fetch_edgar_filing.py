# """Fetches filings from EDGAR for use as test or reference documents."""
# """Download SEC EDGAR exhibit filings into data/contracts/raw/ and record them in manifest.yaml.

# Commands:
#     python scripts/fetch_edgar_filing.py sync            # fetch every manifest entry not yet on disk
#     python scripts/fetch_edgar_filing.py sync --force    # re-download everything
#     python scripts/fetch_edgar_filing.py add URL --id ID --tier easy|medium|hard [--role original|amendment] [--amends ID]

# Rules this script enforces (SEC fair-access policy + playbook):
#     - Only https://www.sec.gov/Archives/edgar/data/... URLs are accepted.
#     - Every request sends EDGAR_USER_AGENT (name + email) from .env.
#     - Requests are spaced at least 0.2s apart (max 5/sec, under SEC's 10/sec limit).
# """
from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import typer
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "contracts" / "raw"
MANIFEST = ROOT / "data" / "contracts" / "manifest.yaml"

ALLOWED_HOSTS = {"www.sec.gov", "sec.gov"}
EDGAR_PATH = re.compile(
    r"^/Archives/edgar/data/(?P<cik>\d+)/(?P<acc>\d{18})/(?P<file>[^/]+\.(?:htm|html|txt))$",
    re.IGNORECASE,
)
TIERS = {"easy", "medium", "hard"}
ROLES = {"original", "amendment"}
MIN_INTERVAL_S = 0.2
FIELD_ORDER = ["id", "accession", "url", "tier", "role", "amends",
               "cik", "local_path", "sha256", "bytes", "fetched_at"]

app = typer.Typer(add_completion=False, help=__doc__)
_last_request = 0.0


def parse_edgar_url(url: str) -> dict:
    """Validate an EDGAR archive URL and pull out CIK, accession number and filename."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in ALLOWED_HOSTS:
        raise ValueError(f"Not an https sec.gov URL: {url}")
    match = EDGAR_PATH.match(parsed.path)
    if not match:
        raise ValueError(f"Not an EDGAR archive document path: {parsed.path}")
    acc = match["acc"]
    return {
        "cik": match["cik"],
        "accession": f"{acc[:10]}-{acc[10:12]}-{acc[12:]}",
        "filename": match["file"],
    }


def get_user_agent() -> str:
    load_dotenv(ROOT / ".env")
    ua = os.getenv("EDGAR_USER_AGENT", "").strip().strip('"')
    if "@" not in ua or " " not in ua:
        typer.secho('Set EDGAR_USER_AGENT in .env, e.g. "Your Name you@email.com"', fg="red")
        raise typer.Exit(1)
    return ua


def download(url: str, user_agent: str, retries: int = 3) -> bytes:
    """GET with spacing between calls and backoff on SEC throttling responses."""
    global _last_request
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
            raise RuntimeError("SEC returned 403. Check EDGAR_USER_AGENT has a real name and email.")
        resp.raise_for_status()
        if not resp.content or b"Request Rate Threshold Exceeded" in resp.content:
            raise RuntimeError(f"Empty or throttled response from {url}")
        return resp.content
    raise RuntimeError(f"Gave up after {retries} retries: {url}")


def load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    data = yaml.safe_load(MANIFEST.read_text()) or []
    if not isinstance(data, list):
        raise ValueError("manifest.yaml must be a YAML list of filing entries")
    return data


def save_manifest(entries: list[dict]) -> None:
    ordered = [
        {k: e[k] for k in FIELD_ORDER if e.get(k) is not None}
        | {k: v for k, v in e.items() if k not in FIELD_ORDER}
        for e in entries
    ]
    tmp = MANIFEST.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True))
    tmp.replace(MANIFEST)


def validate(entries: list[dict]) -> None:
    ids = [e.get("id") for e in entries]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError("Every manifest entry needs a unique id")
    for e in entries:
        if e.get("tier") not in TIERS:
            raise ValueError(f"{e['id']}: tier must be one of {sorted(TIERS)}")
        if e.get("role") and e["role"] not in ROLES:
            raise ValueError(f"{e['id']}: role must be one of {sorted(ROLES)}")
        if e.get("amends") and e["amends"] not in ids:
            raise ValueError(f"{e['id']}: amends unknown id '{e['amends']}'")
        info = parse_edgar_url(e["url"])
        if e.get("accession") and e["accession"] != info["accession"]:
            raise ValueError(f"{e['id']}: accession {e['accession']} doesn't match its URL")


def fetch_entry(entry: dict, user_agent: str, force: bool) -> str:
    """Download one entry if needed and fill in its metadata. Returns a status word."""
    info = parse_edgar_url(entry["url"])
    target = RAW_DIR / f"{info['accession']}_{info['filename']}"
    entry.update(accession=info["accession"], cik=info["cik"],
                 local_path=str(target.relative_to(ROOT)))

    if target.exists() and not force:
        status = "skipped"
        content = target.read_bytes()
    else:
        content = download(entry["url"], user_agent)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(content)
        tmp.replace(target)
        entry["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status = "fetched"

    entry["sha256"] = hashlib.sha256(content).hexdigest()
    entry["bytes"] = len(content)
    return status


@app.command()
def sync(force: bool = typer.Option(False, help="Re-download files already on disk")) -> None:
    """Fetch every filing listed in manifest.yaml."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    entries = load_manifest()
    if not entries:
        typer.secho("manifest.yaml is empty. Use the add command first.", fg="yellow")
        raise typer.Exit(1)
    validate(entries)
    ua = get_user_agent()
    for entry in entries:
        status = fetch_entry(entry, ua, force)
        save_manifest(entries)  # save after each file so a crash keeps progress
        typer.echo(f"  {status:8} {entry['id']:28} {entry['bytes']:>9,} bytes")
    typer.secho(f"Done: {len(entries)} filings in {RAW_DIR.relative_to(ROOT)}", fg="green")


@app.command()
def add(
    url: str,
    id: str = typer.Option(..., "--id", help="Short unique name, e.g. tva_facility_lease"),
    tier: str = typer.Option(..., help="easy, medium or hard"),
    role: Optional[str] = typer.Option(None, help="original or amendment (hard tier)"),
    amends: Optional[str] = typer.Option(None, help="id of the filing this one amends"),
) -> None:
    """Add one filing to the manifest and download it."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    entries = load_manifest()
    entries.append({"id": id, "url": url, "tier": tier, "role": role, "amends": amends})
    validate(entries)
    status = fetch_entry(entries[-1], get_user_agent(), force=False)
    save_manifest(entries)
    typer.secho(f"{status}: {id} -> {entries[-1]['local_path']}", fg="green")


if __name__ == "__main__":
    app()