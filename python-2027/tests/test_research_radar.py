from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from ai2027_protocol.research_contracts import (
    IdentifierKind,
    PaperIdentifier,
    ResearchConfigurationError,
    ResearchInputError,
    ResearchPaper,
    ResearchSource,
    ResearchSourceNotFoundError,
    ResearchSourceUnavailableError,
    load_research_topics,
    normalize_arxiv_id,
    normalize_doi,
    parse_paper_identifier,
)
from ai2027_protocol.research_radar import ResearchRadar
from ai2027_protocol.research_sources import (
    MAX_SOURCE_BYTES,
    OPENALEX_API_URL,
    ArxivAdapter,
    OpenAlexAdapter,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARXIV_NS = "http://www.w3.org/2005/Atom"


def _atom_entry(
    arxiv_id: str = "2401.01234v2",
    *,
    title: str = "Robust Agent Evaluation in Local Systems",
    abstract: str = "We measure an evaluation framework for local engineering agents.",
    published: str = "2025-01-10T00:00:00Z",
    category: str = "cs.AI",
    doi: str = "10.1234/Research.Paper",
) -> str:
    """Build one deterministic arXiv Atom record for offline adapter tests."""
    doi_xml = f"<arxiv:doi>{doi}</arxiv:doi>" if doi else ""
    return (
        f'<entry xmlns="{_ARXIV_NS}" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        f"<id>http://arxiv.org/abs/{arxiv_id}</id>"
        f"<title>{title}</title><summary>{abstract}</summary>"
        f"<published>{published}</published><updated>{published}</updated>"
        f"<author><name>Example Researcher</name></author>"
        f'<category term="{category}"/>{doi_xml}</entry>'
    )


def _atom_feed(*entries: str) -> bytes:
    """Wrap records in the arXiv Atom feed namespace."""
    body = "".join(entries)
    return f'<feed xmlns="{_ARXIV_NS}">{body}</feed>'.encode()


def _paper(
    key: str,
    *,
    source: ResearchSource = ResearchSource.ARXIV,
    arxiv_id: str | None = "2401.01234v2",
    doi: str | None = "10.1234/research.paper",
    title: str = "Robust Agent Evaluation in Local Systems",
    published: date = date(2025, 1, 10),
    categories: tuple[str, ...] = ("cs.AI",),
) -> ResearchPaper:
    """Build a digest-bound verified record without a live network dependency."""
    source_id = arxiv_id if source is ResearchSource.ARXIV else "W1234567890"
    assert source_id is not None
    return ResearchPaper.from_verified_source(
        paper_key=key,
        source=source,
        source_id=source_id,
        arxiv_id=arxiv_id if source is ResearchSource.ARXIV else None,
        doi=doi,
        title=title,
        abstract="We measure an evaluation framework for local engineering agents.",
        published_at=published,
        categories=categories if source is ResearchSource.ARXIV else (),
        authors=("Example Researcher",),
        source_url=(
            f"https://arxiv.org/abs/{arxiv_id}"
            if source is ResearchSource.ARXIV
            else f"https://openalex.org/{source_id}"
        ),
        fetched_at=datetime(2026, 11, 19, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("raw", "kind", "normalized"),
    [
        ("2401.01234V2", IdentifierKind.ARXIV, "2401.01234v2"),
        ("CS.AI/0401001", IdentifierKind.ARXIV, "cs.ai/0401001"),
        ("10.1234/Research.Paper", IdentifierKind.DOI, "10.1234/research.paper"),
        ("doi:10.1234/Research.Paper", IdentifierKind.DOI, "10.1234/research.paper"),
        (
            "https://doi.org/10.1234/Research.Paper",
            IdentifierKind.DOI,
            "10.1234/research.paper",
        ),
    ],
)
def test_identifier_normalization_accepts_only_canonical_namespaces(
    raw: str, kind: IdentifierKind, normalized: str
) -> None:
    """Recognize supported IDs and normalize them before any source request."""
    identifier = parse_paper_identifier(raw)
    assert identifier.kind is kind
    assert identifier.value == normalized


def test_identifier_parser_rejects_arbitrary_or_malformed_urls() -> None:
    """Reject SSRF-like URLs and malformed IDs rather than treating them as search terms."""
    for raw in (
        "https://evil.example/10.1234/paper",
        "http://doi.org/10.1234/paper",
        "https://user@doi.org/10.1234/paper",
        "https://doi.org:bad/10.1234/paper",
        "https://doi.org/10.1234/paper?redirect=https://evil.example",
        "https://arxiv.org/abs/2401.01234",
        "not-an-id",
    ):
        with pytest.raises(ResearchInputError):
            parse_paper_identifier(raw)
    with pytest.raises(ResearchInputError):
        normalize_arxiv_id("2401.12")
    with pytest.raises(ResearchInputError):
        normalize_doi("10.1234/")


def test_paper_model_recomputes_source_digest_and_rejects_noncanonical_url() -> None:
    """Metadata hashes bind a record and source URLs are constructed from IDs."""
    paper = _paper("doi:10.1234/research.paper")
    altered = paper.model_dump(mode="python")
    altered["title"] = "Different invented paper title"
    with pytest.raises(ValidationError):
        ResearchPaper.model_validate(altered)
    altered = paper.model_dump(mode="python")
    altered["source_url"] = "https://evil.example/paper"
    with pytest.raises(ValidationError):
        ResearchPaper.model_validate(altered)


def test_topics_config_is_validated_and_builds_scoped_query(tmp_path: Path) -> None:
    """Only the curated local topics can turn into a source search expression."""
    topics = load_research_topics(_REPO_ROOT / "research" / "topics.toml")
    assert len(topics) == 5
    assert topics[0].id == "agent-evaluation"
    query = ResearchRadar(topics, _FakeGateway(()))._topics["agent-evaluation"]
    from ai2027_protocol.research_contracts import build_arxiv_query

    expression = build_arxiv_query(query)
    assert 'all:"agent evaluation"' in expression
    assert "cat:cs.AI" in expression
    bad = tmp_path / "topics.toml"
    bad.write_text(
        '[[topic]]\nid="unsafe"\nname="Bad"\npriority=1\n'
        'categories=["cs.AI) OR all:*" ]\nkeywords=["paper"]\n'
        'relevance_note="Not an allow-listed query."\n',
        encoding="utf-8",
    )
    with pytest.raises(ResearchConfigurationError):
        load_research_topics(bad)
    with pytest.raises(ResearchConfigurationError):
        load_research_topics(tmp_path / "missing.toml")


def test_topic_loader_rejects_duplicate_ids_and_unknown_top_level_keys(tmp_path: Path) -> None:
    """Malformed or ambiguous interest profiles do not silently fall back to defaults."""
    topic = (
        'id="one"\nname="One topic"\npriority=1\ncategories=["cs.AI"]\n'
        'keywords=["agent evaluation"]\nrelevance_note="A scoped test topic."\n'
    )
    duplicate = tmp_path / "duplicate.toml"
    duplicate.write_text(f"[[topic]]\n{topic}\n[[topic]]\n{topic}", encoding="utf-8")
    with pytest.raises(ResearchConfigurationError):
        load_research_topics(duplicate)
    unknown = tmp_path / "unknown.toml"
    unknown.write_text("version=1\n", encoding="utf-8")
    with pytest.raises(ResearchConfigurationError):
        load_research_topics(unknown)


def test_arxiv_adapter_resolves_exact_id_from_bounded_atom_source() -> None:
    """arXiv fields and canonical URL come from the source response, not the caller."""
    requests: list[httpx.Request] = []
    body = _atom_feed(_atom_entry())

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=body)

    adapter = ArxivAdapter(transport=httpx.MockTransport(handler))
    import asyncio

    paper = asyncio.run(
        adapter.resolve(PaperIdentifier(kind=IdentifierKind.ARXIV, value="2401.01234"))
    )
    assert paper.source is ResearchSource.ARXIV
    assert paper.arxiv_id == "2401.01234v2"
    assert paper.doi == "10.1234/research.paper"
    assert paper.paper_key == "doi:10.1234/research.paper"
    assert paper.title == "Robust Agent Evaluation in Local Systems"
    assert paper.published_at == date(2025, 1, 10)
    assert paper.source_url == "https://arxiv.org/abs/2401.01234v2"
    assert requests[0].url.host == "export.arxiv.org"
    assert parse_qs(requests[0].url.query.decode())["id_list"] == ["2401.01234"]


def test_arxiv_search_sends_constrained_query_and_returns_source_records() -> None:
    """A manually requested query uses the fixed arXiv endpoint and bounded result count."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_atom_feed(_atom_entry(doi="")))

    import asyncio

    result = asyncio.run(
        ArxivAdapter(transport=httpx.MockTransport(handler)).search(
            '(all:"agent evaluation") AND (cat:cs.AI)', limit=7
        )
    )
    params = parse_qs(seen[0].url.query.decode())
    assert len(result) == 1
    assert result[0].paper_key == "arxiv:2401.01234"
    assert params["max_results"] == ["7"]
    assert params["sortBy"] == ["submittedDate"]
    assert params["sortOrder"] == ["descending"]


def test_arxiv_resolver_rejects_different_version_and_non_arxiv_identifier() -> None:
    """An explicit version is exact; a DOI cannot accidentally pass through the arXiv client."""
    adapter = ArxivAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=_atom_feed(_atom_entry()))
        )
    )
    import asyncio

    with pytest.raises(ResearchSourceNotFoundError):
        asyncio.run(
            adapter.resolve(PaperIdentifier(kind=IdentifierKind.ARXIV, value="2401.01234v1"))
        )
    with pytest.raises(ResearchInputError):
        asyncio.run(adapter.resolve(PaperIdentifier(kind=IdentifierKind.DOI, value="10.1234/x")))
    with pytest.raises(ResearchInputError):
        asyncio.run(adapter.search("all:*", limit=51))


def test_arxiv_parser_fails_closed_on_bad_xml_fields_and_entity_declarations() -> None:
    """Malformed XML, missing metadata, and entity declarations are never trusted."""
    adapter = ArxivAdapter()
    with pytest.raises(ResearchSourceUnavailableError):
        adapter._parse_feed(b"not xml")
    with pytest.raises(ResearchSourceUnavailableError):
        adapter._parse_feed(b'<!DOCTYPE feed [<!ENTITY x "boom">]><feed/>')
    missing_title = _atom_entry(title="", doi="")
    with pytest.raises(ResearchSourceUnavailableError):
        adapter._parse_feed(_atom_feed(missing_title))
    invalid_date = _atom_entry(published="not-a-date", doi="")
    with pytest.raises(ResearchSourceUnavailableError):
        adapter._parse_feed(_atom_feed(invalid_date))
    invalid_id = _atom_entry(arxiv_id="evil.example/abs", doi="")
    with pytest.raises(ResearchSourceUnavailableError):
        adapter._parse_feed(_atom_feed(invalid_id))
    invalid_category = _atom_entry(category="all:*", doi="")
    with pytest.raises(ResearchSourceUnavailableError):
        adapter._parse_feed(_atom_feed(invalid_category))
    invalid_doi = _atom_entry(doi="https://evil.example/doi")
    with pytest.raises(ResearchSourceUnavailableError):
        adapter._parse_feed(_atom_feed(invalid_doi))


@pytest.mark.parametrize("status", [302, 401, 429, 503])
def test_academic_source_refuses_redirects_and_upstream_failures(status: int) -> None:
    """Non-200 results are not followed or interpreted as paper metadata."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, headers={"Location": "https://evil.example"})

    adapter = ArxivAdapter(transport=httpx.MockTransport(handler))
    import asyncio

    with pytest.raises(ResearchSourceUnavailableError):
        asyncio.run(adapter.resolve(PaperIdentifier(kind=IdentifierKind.ARXIV, value="2401.01234")))
    assert calls == [1]


def test_source_404_and_unreachable_transport_have_typed_refusals() -> None:
    """Missing papers and network blindness remain distinct, honest outcomes."""
    missing = ArxivAdapter(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    import asyncio

    with pytest.raises(ResearchSourceNotFoundError):
        asyncio.run(missing.resolve(PaperIdentifier(kind=IdentifierKind.ARXIV, value="2401.01234")))

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    unavailable = ArxivAdapter(transport=httpx.MockTransport(broken))
    with pytest.raises(ResearchSourceUnavailableError):
        asyncio.run(
            unavailable.resolve(PaperIdentifier(kind=IdentifierKind.ARXIV, value="2401.01234"))
        )


def test_source_response_size_is_bounded() -> None:
    """An upstream cannot force the radar to buffer an unbounded response."""
    oversized = ArxivAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"x" * (MAX_SOURCE_BYTES + 1))
        )
    )
    import asyncio

    with pytest.raises(ResearchSourceUnavailableError, match="size limit"):
        asyncio.run(oversized.search('(all:"local")', limit=1))


def test_openalex_resolves_exact_doi_and_builds_safe_canonical_metadata() -> None:
    """OpenAlex results are accepted only when the DOI, work ID, title, and date match."""
    requests: list[httpx.Request] = []
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W1234567890",
                "doi": "https://doi.org/10.1234/Research.Paper",
                "title": "Robust Agent Evaluation in Local Systems",
                "publication_date": "2025-01-10",
                "authorships": [
                    {"author": {"display_name": "Example Researcher"}},
                    {"author": None},
                ],
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url).startswith(OPENALEX_API_URL)
        return httpx.Response(200, json=payload)

    import asyncio

    paper = asyncio.run(
        OpenAlexAdapter(transport=httpx.MockTransport(handler)).resolve(
            PaperIdentifier(kind=IdentifierKind.DOI, value="10.1234/research.paper")
        )
    )
    params = parse_qs(requests[0].url.query.decode())
    assert paper.source is ResearchSource.OPENALEX
    assert paper.source_id == "W1234567890"
    assert paper.paper_key == "doi:10.1234/research.paper"
    assert paper.source_url == "https://openalex.org/W1234567890"
    assert paper.authors == ("Example Researcher",)
    assert params["per-page"] == ["2"]
    assert "10.1234/research.paper" in params["filter"][0]


def test_openalex_rejects_nonmatching_ambiguous_and_malformed_records() -> None:
    """Index results that cannot prove the requested DOI are not ingested."""
    import asyncio

    identifier = PaperIdentifier(kind=IdentifierKind.DOI, value="10.1234/research.paper")
    mismatch = OpenAlexAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"results": [{"id": "https://openalex.org/W1", "doi": "10.1234/other"}]},
            )
        )
    )
    with pytest.raises(ResearchSourceNotFoundError):
        asyncio.run(mismatch.resolve(identifier))

    ambiguous_payload = {
        "results": [
            {
                "id": f"https://openalex.org/W{i}",
                "doi": "https://doi.org/10.1234/research.paper",
                "title": "Robust Agent Evaluation in Local Systems",
                "publication_date": "2025-01-10",
                "authorships": [],
            }
            for i in (1, 2)
        ]
    }
    ambiguous = OpenAlexAdapter(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=ambiguous_payload))
    )
    with pytest.raises(ResearchSourceUnavailableError, match="ambiguous"):
        asyncio.run(ambiguous.resolve(identifier))

    invalid_json = OpenAlexAdapter(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"{"))
    )
    with pytest.raises(ResearchSourceUnavailableError, match="malformed JSON"):
        asyncio.run(invalid_json.resolve(identifier))

    wrong_schema = OpenAlexAdapter(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": "bad"}))
    )
    with pytest.raises(ResearchSourceUnavailableError, match="schema"):
        asyncio.run(wrong_schema.resolve(identifier))

    no_title = OpenAlexAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "doi": "10.1234/research.paper",
                            "title": "",
                            "publication_date": "2025-01-10",
                            "authorships": [],
                        }
                    ]
                },
            )
        )
    )
    with pytest.raises(ResearchSourceUnavailableError, match="validation"):
        asyncio.run(no_title.resolve(identifier))


def test_openalex_rejects_noncanonical_work_urls_and_bad_author_shapes() -> None:
    """OpenAlex metadata cannot smuggle a redirect URL or unbounded author list."""
    import asyncio

    identifier = PaperIdentifier(kind=IdentifierKind.DOI, value="10.1234/research.paper")
    for unsafe_url in (
        "http://openalex.org/W1",
        "https://openalex.org.evil.example/W1",
        "https://user@openalex.org/W1",
        "https://openalex.org:444/W1",
        "https://openalex.org/W1?next=evil",
    ):
        adapter = OpenAlexAdapter(
            transport=httpx.MockTransport(
                lambda _, value=unsafe_url: httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": value,
                                "doi": "10.1234/research.paper",
                                "title": "Robust Agent Evaluation in Local Systems",
                                "publication_date": "2025-01-10",
                                "authorships": [],
                            }
                        ]
                    },
                )
            )
        )
        with pytest.raises(ResearchSourceUnavailableError):
            asyncio.run(adapter.resolve(identifier))

    bad_authors = OpenAlexAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "doi": "10.1234/research.paper",
                            "title": "Robust Agent Evaluation in Local Systems",
                            "publication_date": "2025-01-10",
                            "authorships": ["not-an-author"],
                        }
                    ]
                },
            )
        )
    )
    with pytest.raises(ResearchSourceUnavailableError, match="author"):
        asyncio.run(bad_authors.resolve(identifier))


class _FakeGateway:
    """In-memory source gateway that lets the radar tests inspect query constraints."""

    def __init__(self, papers: tuple[ResearchPaper, ...]) -> None:
        self.papers = papers
        self.query = ""
        self.limit = 0
        self.resolved: ResearchPaper | None = None
        self.identifier: PaperIdentifier | None = None

    async def search(self, query: str, *, limit: int) -> tuple[ResearchPaper, ...]:
        """Record the constrained search and return deterministic source results."""
        self.query = query
        self.limit = limit
        return self.papers

    async def resolve(self, identifier: PaperIdentifier) -> ResearchPaper:
        """Return one pre-verified record for a canonical identifier."""
        self.identifier = identifier
        if self.resolved is None:
            raise ResearchSourceNotFoundError("fixture missing")
        return self.resolved


@pytest.mark.asyncio
async def test_radar_filters_date_category_source_and_duplicates() -> None:
    """Only recent arXiv records in the selected topic survive; none are persisted."""
    as_of = date(2026, 11, 19)
    recent = _paper(
        "arxiv:2501.00001",
        arxiv_id="2501.00001",
        doi=None,
        published=as_of - timedelta(days=4),
    )
    older = _paper(
        "arxiv:2401.00002",
        arxiv_id="2401.00002",
        doi=None,
        published=as_of - timedelta(days=400),
    )
    future = _paper(
        "arxiv:2601.00003",
        arxiv_id="2601.00003",
        doi=None,
        published=as_of + timedelta(days=1),
    )
    other_category = _paper(
        "arxiv:2501.00004",
        arxiv_id="2501.00004",
        doi=None,
        categories=("cs.CR",),
        published=as_of - timedelta(days=2),
    )
    openalex = _paper(
        "doi:10.1234/openalex",
        source=ResearchSource.OPENALEX,
        arxiv_id=None,
        doi="10.1234/openalex",
        published=as_of - timedelta(days=1),
    )
    fake = _FakeGateway((older, recent, future, other_category, openalex, recent))
    radar = ResearchRadar(load_research_topics(_REPO_ROOT / "research" / "topics.toml"), fake)
    result = await radar.search("agent-evaluation", window_days=30, limit=2, today=as_of)
    assert result == (recent,)
    assert 'all:"agent evaluation"' in fake.query
    assert "cat:cs.AI" in fake.query
    assert fake.limit == 10


@pytest.mark.asyncio
async def test_radar_refuses_unknown_topics_and_invalid_bounds() -> None:
    """No arbitrary search string or oversized time/limit can bypass topic scoping."""
    radar = ResearchRadar(
        load_research_topics(_REPO_ROOT / "research" / "topics.toml"), _FakeGateway(())
    )
    with pytest.raises(ResearchInputError):
        await radar.search("unknown-topic")
    with pytest.raises(ResearchInputError):
        await radar.search("agent-evaluation", window_days=0)
    with pytest.raises(ResearchInputError):
        await radar.search("agent-evaluation", limit=21)
    with pytest.raises(ResearchConfigurationError):
        ResearchRadar((), _FakeGateway(()))


@pytest.mark.asyncio
async def test_radar_resolves_only_identifier_and_keeps_source_boundary() -> None:
    """An arXiv ID reaches the gateway as a normalized identifier, never a free URL."""
    paper = _paper("arxiv:2401.01234", arxiv_id="2401.01234", doi=None)
    gateway = _FakeGateway(())
    gateway.resolved = paper
    radar = ResearchRadar(load_research_topics(_REPO_ROOT / "research" / "topics.toml"), gateway)
    assert await radar.resolve("ARXIV:2401.01234") == paper
    assert gateway.identifier == PaperIdentifier(kind=IdentifierKind.ARXIV, value="2401.01234")
    with pytest.raises(ResearchInputError):
        await radar.resolve("https://evil.example/paper")
