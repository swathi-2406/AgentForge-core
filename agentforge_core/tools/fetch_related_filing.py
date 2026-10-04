# """Tool for fetching a related filing referenced by the source document."""
# """fetch_related_filing: given an amendment, make sure its original agreement is on disk and readable.

# How it finds the original:
#     1. The amendment's `amends:` link in manifest.yaml (the normal case, e.g. redwire_credit_amend1).
#     2. Or an original_url you pass in -- only https://www.sec.gov/Archives/edgar/data/... is accepted.
#        The original is added to the manifest and linked, so the next call is a cache hit.

# It never fetches a non-EDGAR URL, and only touches the network when the file isn't already on disk.
# """

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, field_validator

from agentforge_core import edgar
from agentforge_core.tools.base import Tool, ToolError, ToolInput, ToolOutput, register_tool
from agentforge_core.tools.read_document import load_document


class FetchRelatedFilingInput(ToolInput):
    filing_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="The amendment's id in manifest.yaml")
    original_url: Optional[str] = Field(None, description="EDGAR URL of the original, if the manifest has no link yet")
    original_id: Optional[str] = Field(None, pattern=r"^[a-z0-9][a-z0-9_]*$",
                                       description="id to store a new original under (default: <filing_id>_original)")

    @field_validator("original_url")
    @classmethod
    def edgar_only(cls, url: Optional[str]) -> Optional[str]:
        if url is not None:
            try:
                edgar.parse_edgar_url(url)
            except edgar.EdgarError as e:
                raise ValueError(str(e)) from None
        return url


class FetchRelatedFilingOutput(ToolOutput):
    amendment_id: str
    original_id: str
    status: Literal["cached", "fetched"]
    url: str
    accession: str
    local_path: str
    section_count: int
    warnings: list[str]


@register_tool
class FetchRelatedFiling(Tool):
    name = "fetch_related_filing"
    description = ("For an amendment, load the original agreement it amends (from the manifest link, or an EDGAR "
                   "URL you give). Returns the original's filing_id so other tools can read it. EDGAR URLs only.")
    Input = FetchRelatedFilingInput
    Output = FetchRelatedFilingOutput

    def run(self, args: FetchRelatedFilingInput) -> FetchRelatedFilingOutput:
        try:
            return self._run(args)
        except edgar.EdgarError as e:
            raise ToolError(str(e)) from None

    def _run(self, args: FetchRelatedFilingInput) -> FetchRelatedFilingOutput:
        entries = edgar.load_manifest()
        by_id = {e["id"]: e for e in entries}
        amendment = by_id.get(args.filing_id)
        if not amendment:
            raise ToolError(f"Unknown filing_id '{args.filing_id}'. Known: {', '.join(sorted(by_id))}")

        linked = amendment.get("amends")
        if linked and args.original_url and by_id[linked]["url"] != args.original_url:
            raise ToolError(f"{args.filing_id} is already linked to {linked} ({by_id[linked]['url']}); "
                            "fix the manifest by hand if that link is wrong")
        if not linked:
            if not args.original_url:
                raise ToolError(f"{args.filing_id} has no 'amends' link in manifest.yaml. "
                                "Pass original_url (an EDGAR document URL) to fetch its original.")
            new_id = args.original_id or f"{args.filing_id}_original"
            existing = next((e for e in entries if e["url"] == args.original_url), None)
            if existing:
                new_id = existing["id"]
            else:
                if new_id in by_id:
                    raise ToolError(f"id '{new_id}' already exists for a different URL; pass another original_id")
                entries.append({"id": new_id, "url": args.original_url, "tier": amendment.get("tier", "hard"),
                                "role": "original"})
            amendment["amends"], amendment["role"] = new_id, amendment.get("role") or "amendment"
            linked = new_id
            by_id = {e["id"]: e for e in entries}

        original = by_id[linked]
        edgar.validate(entries)  # every URL in the manifest must be EDGAR before anything is fetched
        on_disk = edgar.target_path(original).exists()
        status = edgar.fetch_entry(original, None if on_disk else edgar.get_user_agent())
        edgar.save_manifest(entries)

        doc = load_document(linked)
        return FetchRelatedFilingOutput(
            amendment_id=args.filing_id, original_id=linked, status="cached" if status == "skipped" else "fetched",
            url=original["url"], accession=original["accession"], local_path=original["local_path"],
            section_count=len(doc.sections), warnings=list(doc.warnings))