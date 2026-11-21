"""Fixed-host academic source adapters with bounded HTTPS and strict metadata parsing."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import httpx

from .research_contracts import (
    _CATEGORY,
    IdentifierKind,
    PaperIdentifier,
    ResearchInputError,
    ResearchPaper,
    ResearchSource,
    ResearchSourceNotFoundError,
    ResearchSourceUnavailableError,
    _clean_text,
    arxiv_base_id,
    normalize_arxiv_id,
    normalize_doi,
)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
OPENALEX_API_URL = "https://api.openalex.org/works"
MAX_SOURCE_BYTES = 512_000
_MAX_ARXIV_RESULTS = 50
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"


async def _get_bounded(
    url: str,
    *,
    params: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None,
) -> bytes:
    """Fetch a fixed HTTPS endpoint with no redirects, proxy env, or unbounded body."""
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(6.0, connect=2.0),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/atom+xml, application/json",
                "User-Agent": "AI-2027-Research-Radar/1.0",
            },
        ) as client:
            async with client.stream("GET", url, params=dict(params)) as response:
                if response.status_code == 404:
                    raise ResearchSourceNotFoundError(
                        "The academic source has no record for this identifier."
                    )
                if response.status_code != 200:
                    raise ResearchSourceUnavailableError(
                        f"Academic source returned HTTP {response.status_code}."
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_SOURCE_BYTES:
                        raise ResearchSourceUnavailableError(
                            "Academic source response exceeded the size limit."
                        )
                    body.extend(chunk)
                return bytes(body)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ResearchSourceUnavailableError("Academic source could not be reached.") from exc
    except httpx.InvalidURL as exc:
        raise ResearchSourceUnavailableError(
            "Academic source returned an invalid response URL."
        ) from exc


def _text_at(element: ET.Element, path: str, *, required: bool = False) -> str:
    """Read one bounded Atom text field and normalize its whitespace."""
    found = element.find(path, {"atom": _ATOM_NS, "arxiv": _ARXIV_NS})
    value = _clean_text(found.text or "") if found is not None else ""
    if required and not value:
        raise ResearchSourceUnavailableError("arXiv record is missing a required metadata field.")
    return value


def _arxiv_id_from_entry(entry: ET.Element) -> str:
    """Validate the arXiv Atom entry ID and return its canonical versioned ID."""
    raw_id = _text_at(entry, "atom:id", required=True)
    try:
        parsed = urlsplit(raw_id)
    except ValueError as exc:
        raise ResearchSourceUnavailableError(
            "arXiv returned a malformed entry identifier."
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc.lower() != "arxiv.org"
        or parsed.query
        or parsed.fragment
    ):
        raise ResearchSourceUnavailableError("arXiv returned a non-canonical entry identifier.")
    match = re.fullmatch(r"/abs/([^/]+)", parsed.path)
    if match is None:
        raise ResearchSourceUnavailableError("arXiv returned an invalid entry path.")
    try:
        return normalize_arxiv_id(match.group(1))
    except ResearchInputError as exc:
        raise ResearchSourceUnavailableError("arXiv returned an invalid entry identifier.") from exc


def _parse_published(value: str) -> date:
    """Parse an upstream RFC3339-like date, refusing missing or malformed dates."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ResearchSourceUnavailableError(
            "Academic source returned an invalid publication date."
        ) from exc


def _arxiv_paper(entry: ET.Element) -> ResearchPaper:
    """Convert one validated Atom entry into digest-bound source metadata."""
    arxiv_id = _arxiv_id_from_entry(entry)
    title = _text_at(entry, "atom:title", required=True)
    abstract = _text_at(entry, "atom:summary")
    published = _parse_published(_text_at(entry, "atom:published", required=True))
    categories = tuple(
        dict.fromkeys(
            item.attrib.get("term", "").strip()
            for item in entry.findall("atom:category", {"atom": _ATOM_NS})
            if item.attrib.get("term", "").strip()
        )
    )
    if len(categories) > 12 or any(not _CATEGORY.fullmatch(item) for item in categories):
        raise ResearchSourceUnavailableError("arXiv returned invalid category metadata.")
    authors = tuple(
        _clean_text(author.text or "")
        for author in entry.findall("atom:author/atom:name", {"atom": _ATOM_NS})
        if _clean_text(author.text or "")
    )
    if len(authors) > 50:
        raise ResearchSourceUnavailableError(
            "arXiv returned too many authors for a bounded record."
        )
    doi_value = _text_at(entry, "arxiv:doi")
    try:
        doi = normalize_doi(doi_value) if doi_value else None
    except ResearchInputError as exc:
        raise ResearchSourceUnavailableError("arXiv returned an invalid DOI field.") from exc
    paper_key = f"doi:{doi}" if doi else f"arxiv:{arxiv_base_id(arxiv_id)}"
    try:
        return ResearchPaper.from_verified_source(
            paper_key=paper_key,
            source=ResearchSource.ARXIV,
            source_id=arxiv_id,
            arxiv_id=arxiv_id,
            doi=doi,
            title=title,
            abstract=abstract,
            published_at=published,
            categories=categories,
            authors=authors,
            source_url=f"https://arxiv.org/abs/{arxiv_id}",
            fetched_at=datetime.now(UTC),
        )
    except ValueError as exc:
        raise ResearchSourceUnavailableError("arXiv record failed metadata validation.") from exc


class ArxivAdapter:
    """Bounded read-only arXiv Atom search and exact-ID resolver."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Use an optional transport for deterministic offline tests."""
        self._transport = transport

    async def search(self, query: str, *, limit: int) -> tuple[ResearchPaper, ...]:
        """Search arXiv by an already constrained query and newest submission date."""
        if not 1 <= limit <= _MAX_ARXIV_RESULTS:
            raise ResearchInputError("arXiv search limit must be between 1 and 50.")
        body = await _get_bounded(
            ARXIV_API_URL,
            params={
                "search_query": query,
                "start": "0",
                "max_results": str(limit),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
            transport=self._transport,
        )
        return self._parse_feed(body)

    async def resolve(self, identifier: PaperIdentifier) -> ResearchPaper:
        """Fetch and exactly match one arXiv identifier, preserving an explicit version."""
        if identifier.kind is not IdentifierKind.ARXIV:
            raise ResearchInputError("arXiv resolver requires an arXiv identifier.")
        body = await _get_bounded(
            ARXIV_API_URL,
            params={"id_list": identifier.value, "start": "0", "max_results": "2"},
            transport=self._transport,
        )
        papers = self._parse_feed(body)
        for paper in papers:
            if identifier.value == paper.arxiv_id or (
                identifier.value == arxiv_base_id(identifier.value)
                and identifier.value == arxiv_base_id(paper.arxiv_id or "")
            ):
                return paper
        raise ResearchSourceNotFoundError("arXiv did not return the exact requested identifier.")

    @staticmethod
    def _parse_feed(body: bytes) -> tuple[ResearchPaper, ...]:
        """Parse a bounded Atom response and reject DTD/entity payloads."""
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ResearchSourceUnavailableError(
                "arXiv response contained a forbidden XML declaration."
            )
        try:
            # DTD/entities are rejected above and the decoded body has a strict byte cap.
            root = ET.fromstring(body)  # noqa: S314
        except ET.ParseError as exc:
            raise ResearchSourceUnavailableError("arXiv returned malformed Atom metadata.") from exc
        entries = root.findall(f"{{{_ATOM_NS}}}entry")
        return tuple(_arxiv_paper(entry) for entry in entries[:_MAX_ARXIV_RESULTS])


class OpenAlexAdapter:
    """Read-only DOI verification through the no-key OpenAlex works API."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Use an optional transport for deterministic offline tests."""
        self._transport = transport

    async def resolve(self, identifier: PaperIdentifier) -> ResearchPaper:
        """Resolve one DOI and require an exact DOI match in the OpenAlex response."""
        if identifier.kind is not IdentifierKind.DOI:
            raise ResearchInputError("OpenAlex resolver requires a DOI.")
        body = await _get_bounded(
            OPENALEX_API_URL,
            params={
                "filter": f"doi:https://doi.org/{identifier.value}",
                "per-page": "2",
                "select": "id,doi,title,publication_date,authorships",
            },
            transport=self._transport,
        )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchSourceUnavailableError(
                "OpenAlex returned malformed JSON metadata."
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ResearchSourceUnavailableError(
                "OpenAlex response did not match the works schema."
            )
        matches = [
            item
            for item in payload["results"]
            if isinstance(item, dict)
            and isinstance(item.get("doi"), str)
            and self._same_doi(item["doi"], identifier.value)
        ]
        if not matches:
            raise ResearchSourceNotFoundError("OpenAlex has no work matching that exact DOI.")
        if len(matches) != 1:
            raise ResearchSourceUnavailableError(
                "OpenAlex returned ambiguous records for this DOI."
            )
        item = matches[0]
        work_id = self._work_id(item.get("id"))
        title = item.get("title")
        published_at = item.get("publication_date")
        if not isinstance(title, str) or not isinstance(published_at, str):
            raise ResearchSourceUnavailableError(
                "OpenAlex record is missing a title or publication date."
            )
        authorships = item.get("authorships", [])
        if not isinstance(authorships, list) or len(authorships) > 50:
            raise ResearchSourceUnavailableError("OpenAlex returned invalid author metadata.")
        authors: list[str] = []
        for authorship in authorships:
            if not isinstance(authorship, dict):
                raise ResearchSourceUnavailableError("OpenAlex returned malformed author metadata.")
            author = authorship.get("author")
            if isinstance(author, dict) and isinstance(author.get("display_name"), str):
                name = _clean_text(author["display_name"])
                if name:
                    authors.append(name)
        raw_doi = str(item["doi"]).strip()
        if raw_doi.lower().startswith("https://doi.org/"):
            raw_doi = raw_doi[len("https://doi.org/") :]
        try:
            doi = normalize_doi(raw_doi)
            return ResearchPaper.from_verified_source(
                paper_key=f"doi:{identifier.value}",
                source=ResearchSource.OPENALEX,
                source_id=work_id,
                arxiv_id=None,
                doi=doi,
                title=_clean_text(title),
                abstract="",
                published_at=_parse_published(published_at),
                categories=(),
                authors=tuple(authors),
                source_url=f"https://openalex.org/{work_id}",
                fetched_at=datetime.now(UTC),
            )
        except ValueError as exc:
            raise ResearchSourceUnavailableError(
                "OpenAlex record failed metadata validation."
            ) from exc

    @staticmethod
    def _same_doi(raw: str, expected: str) -> bool:
        """Compare OpenAlex DOI URL/identifier without accepting a different DOI."""
        value = raw.strip()
        if value.lower().startswith("https://doi.org/"):
            value = value[len("https://doi.org/") :]
        try:
            return normalize_doi(value) == expected
        except ResearchInputError:
            return False

    @staticmethod
    def _work_id(raw: object) -> str:
        """Extract an OpenAlex W-ID from its canonical HTTPS record URL."""
        if not isinstance(raw, str):
            raise ResearchSourceUnavailableError("OpenAlex returned a missing work identifier.")
        try:
            parsed = urlsplit(raw)
        except ValueError as exc:
            raise ResearchSourceUnavailableError(
                "OpenAlex returned a malformed work identifier."
            ) from exc
        match = re.fullmatch(r"/([Ww]\d+)", parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "openalex.org"
            or parsed.query
            or parsed.fragment
            or match is None
        ):
            raise ResearchSourceUnavailableError(
                "OpenAlex returned a non-canonical work identifier."
            )
        return match.group(1).upper()


class OpenAcademicGateway:
    """Compose the free arXiv search and OpenAlex DOI metadata adapters."""

    def __init__(
        self,
        *,
        arxiv: ArxivAdapter | None = None,
        openalex: OpenAlexAdapter | None = None,
    ) -> None:
        """Allow adapter injection without changing the trust boundary."""
        self._arxiv = arxiv or ArxivAdapter()
        self._openalex = openalex or OpenAlexAdapter()

    async def search(self, query: str, *, limit: int) -> tuple[ResearchPaper, ...]:
        """Search arXiv only; searches never auto-write candidates to the registry."""
        return await self._arxiv.search(query, limit=limit)

    async def resolve(self, identifier: PaperIdentifier) -> ResearchPaper:
        """Route identifiers to their fixed authoritative metadata adapter."""
        if identifier.kind is IdentifierKind.ARXIV:
            return await self._arxiv.resolve(identifier)
        return await self._openalex.resolve(identifier)
