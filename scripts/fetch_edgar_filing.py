# """Download SEC EDGAR exhibit filings into data/contracts/raw/ and record them in manifest.yaml.

# Commands:
#     python scripts/fetch_edgar_filing.py sync            # fetch every manifest entry not yet on disk
#     python scripts/fetch_edgar_filing.py sync --force    # re-download everything
#     python scripts/fetch_edgar_filing.py add URL --id ID --tier easy|medium|hard [--role original|amendment] [--amends ID]

# The EDGAR rules (sec.gov URLs only, User-Agent, rate limit) live in agentforge_core/edgar.py,
# shared with the agent's fetch_related_filing tool.
# """
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentforge_core import edgar  # noqa: E402

app = typer.Typer(add_completion=False, help=__doc__)


def user_agent_or_exit() -> str:
    try:
        return edgar.get_user_agent()
    except edgar.EdgarError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)


@app.command()
def sync(force: bool = typer.Option(False, help="Re-download files already on disk")) -> None:
    """Fetch every filing listed in manifest.yaml."""
    entries = edgar.load_manifest()
    if not entries:
        typer.secho("manifest.yaml is empty. Use the add command first.", fg="yellow")
        raise typer.Exit(1)
    edgar.validate(entries)
    ua = user_agent_or_exit()
    for entry in entries:
        status = edgar.fetch_entry(entry, ua, force)
        edgar.save_manifest(entries)  # save after each file so a crash keeps progress
        typer.echo(f"  {status:8} {entry['id']:28} {entry['bytes']:>9,} bytes")
    typer.secho(f"Done: {len(entries)} filings in {edgar.raw_dir().relative_to(edgar.get_root())}", fg="green")


@app.command()
def add(
    url: str,
    id: str = typer.Option(..., "--id", help="Short unique name, e.g. tva_facility_lease"),
    tier: str = typer.Option(..., help="easy, medium or hard"),
    role: Optional[str] = typer.Option(None, help="original or amendment (hard tier)"),
    amends: Optional[str] = typer.Option(None, help="id of the filing this one amends"),
) -> None:
    """Add one filing to the manifest and download it."""
    entries = edgar.load_manifest()
    entries.append({"id": id, "url": url, "tier": tier, "role": role, "amends": amends})
    edgar.validate(entries)
    status = edgar.fetch_entry(entries[-1], user_agent_or_exit())
    edgar.save_manifest(entries)
    typer.secho(f"{status}: {id} -> {entries[-1]['local_path']}", fg="green")


if __name__ == "__main__":
    app()