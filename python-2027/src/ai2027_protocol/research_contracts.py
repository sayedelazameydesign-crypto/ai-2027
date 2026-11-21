"""Strict contracts and identifier normalization for verified research ingestion."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

from pydantic import Field, field_validator, model_validator

from .contracts import StrictModel

_ARXIV_NEW_ID = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE)
_ARXIV_OLD_ID = re.compile(r"^[a-z][a-z0-9.-]*(?:\.[a-z]{2})?/\d{7}(?:v\d+)?$", re.IGNORECASE)
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
_DOI_ID = re.compile(r"^10\.\d{4,9}/[-._;()/:a-z0-9]+$", re.IGNORECASE)
_TOPIC_ID = re.compile(r"^[a-z][a-z0-9-]{1,48}$")
_TOPIC_KEYWORD = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 ._+\-]{0,79}$")
_CATEGORY = re.compile(r"^[a-z]{2,8}\.[A-Z]{2,4}$")


class ResearchError(RuntimeError):
    """Base class for expected, sanitized research-radar refusals."""


class ResearchInputError(ResearchError):
    """An identifier, topic, or request is outside the supported contract."""


class ResearchSourceUnavailableError(ResearchError):
    """A fixed upstream source did not return a bounded, valid response."""


class ResearchSourceNotFoundError(ResearchError):
    """A valid identifier was not present in its selected academic source."""


class ResearchConfigurationError(ResearchError):
    """Research topics configuration is missing or invalid."""


class IdentifierKind(StrEnum):
    """Identifier namespace accepted at the ingestion boundary."""

    ARXIV = "arxiv"
    DOI = "doi"


class ResearchSource(StrEnum):
    """Metadata provider that was actually queried for a paper record."""

    ARXIV = "arxiv"
    OPENALEX = "openalex"


class ResearchStatus(StrEnum):
    """Research triage state; none of these states means production adoption."""

    CANDIDATE = "candidate"
    TESTED = "tested"
    REJECTED = "rejected"


def normalize_arxiv_id(value: str) -> str:
    """Normalize a modern or legacy arXiv identifier while preserving its version."""
    clean = value.strip().lower()
    if not (_ARXIV_NEW_ID.fullmatch(clean) or _ARXIV_OLD_ID.fullmatch(clean)):
        raise ResearchInputError("Expected an arXiv ID such as 2401.01234 or cs/0401001.")
    return clean


def arxiv_base_id(value: str) -> str:
    """Remove an arXiv version suffix for stable work-level deduplication."""
    clean = normalize_arxiv_id(value)
    return _ARXIV_VERSION.sub("", clean)


def normalize_doi(value: str) -> str:
    """Return the canonical lower-case DOI or reject a non-DOI value."""
    clean = value.strip()
    if clean.lower().startswith("doi:"):
        clean = clean[4:].strip()
    if not _DOI_ID.fullmatch(clean):
        raise ResearchInputError("Expected a DOI beginning with 10.<registrant>/<suffix>.")
    return clean.lower()


class PaperIdentifier(StrictModel):
    """Canonical DOI or arXiv ID; raw source URLs are never accepted as fetch targets."""

    kind: IdentifierKind
    value: str = Field(min_length=1, max_length=180)

    @model_validator(mode="after")
    def check_is_canonical(self) -> PaperIdentifier:
        """Refuse identifiers that would be changed by canonicalization."""
        canonical = (
            normalize_arxiv_id(self.value)
            if self.kind is IdentifierKind.ARXIV
            else normalize_doi(self.value)
        )
        if self.value != canonical:
            raise ValueError("Identifier must be canonical; normalize it before storing.")
        return self

    @property
    def key(self) -> str:
        """Return a safe, stable identifier alias key."""
        return f"{self.kind.value}:{self.value}"


def parse_paper_identifier(raw: str) -> PaperIdentifier:
    """Parse a DOI/arXiv ID (or canonical DOI URL) without trusting arbitrary URLs."""
    value = raw.strip()
    if not value:
        raise ResearchInputError("A DOI or arXiv ID is required.")
    lowered = value.lower()
    if lowered.startswith("arxiv:"):
        return PaperIdentifier(kind=IdentifierKind.ARXIV, value=normalize_arxiv_id(value[6:]))
    if lowered.startswith("doi:"):
        return PaperIdentifier(kind=IdentifierKind.DOI, value=normalize_doi(value[4:]))
    if lowered.startswith("https://"):
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ResearchInputError("Malformed DOI URL.") from exc
        if (
            parsed.hostname is None
            or parsed.hostname.lower() != "doi.org"
            or parsed.username
            or parsed.password
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ResearchInputError("Only the canonical https://doi.org/<DOI> URL is accepted.")
        return PaperIdentifier(
            kind=IdentifierKind.DOI,
            value=normalize_doi(unquote(parsed.path.lstrip("/"))),
        )
    if _ARXIV_NEW_ID.fullmatch(value) or _ARXIV_OLD_ID.fullmatch(value):
        return PaperIdentifier(kind=IdentifierKind.ARXIV, value=normalize_arxiv_id(value))
    return PaperIdentifier(kind=IdentifierKind.DOI, value=normalize_doi(value))


def _canonical_json(value: object) -> str:
    """Serialize evidence deterministically and reject non-JSON values."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_value(value: object) -> object:
    """Convert dates, enums, and nested containers to stable JSON-native values."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _metadata_digest(fields: Mapping[str, object]) -> str:
    """Hash only the verified public metadata fields, not a model-generated summary."""
    payload = {key: value for key, value in fields.items() if key != "metadata_sha256"}
    return hashlib.sha256(_canonical_json(_json_value(payload)).encode("utf-8")).hexdigest()


def _clean_text(value: str) -> str:
    """Collapse provider whitespace without changing the words of a source field."""
    return " ".join(value.split())


class ResearchTopic(StrictModel):
    """Allow-listed project interest used to constrain search relevance."""

    id: str = Field(min_length=2, max_length=49)
    name: str = Field(min_length=2, max_length=100)
    priority: int = Field(ge=1, le=5)
    categories: tuple[str, ...] = Field(min_length=1, max_length=12)
    keywords: tuple[str, ...] = Field(min_length=1, max_length=12)
    relevance_note: str = Field(min_length=8, max_length=500)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """Restrict topic IDs to URL/config-safe slugs."""
        if not _TOPIC_ID.fullmatch(value):
            raise ValueError("Topic ID must be a lower-case slug.")
        return value

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Allow only arXiv category tokens, not query-language fragments."""
        clean = tuple(dict.fromkeys(item.strip() for item in values))
        if not clean or any(not _CATEGORY.fullmatch(item) for item in clean):
            raise ValueError("Categories must be arXiv category codes such as cs.AI.")
        return clean

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Keep terms safe for quoted arXiv search expressions."""
        clean = tuple(dict.fromkeys(_clean_text(item) for item in values))
        if not clean or any(not _TOPIC_KEYWORD.fullmatch(item) for item in clean):
            raise ValueError("Keywords contain unsupported search syntax.")
        return clean


def load_research_topics(path: Path) -> tuple[ResearchTopic, ...]:
    """Load and strictly validate the local TOML topic allow-list."""
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ResearchConfigurationError("Research topics configuration is unavailable.") from exc
    if set(parsed) != {"topic"} or not isinstance(parsed.get("topic"), list):
        raise ResearchConfigurationError(
            "Research topics TOML must contain only [[topic]] entries."
        )
    try:
        topics = tuple(ResearchTopic.model_validate(item) for item in parsed["topic"])
    except (TypeError, ValueError) as exc:
        raise ResearchConfigurationError(
            "Research topics configuration failed validation."
        ) from exc
    ids = [topic.id for topic in topics]
    if not topics or len(ids) != len(set(ids)):
        raise ResearchConfigurationError("Research topics must be non-empty and have unique IDs.")
    return tuple(sorted(topics, key=lambda topic: (topic.priority, topic.id)))


def build_arxiv_query(topic: ResearchTopic) -> str:
    """Build a bounded arXiv query using only validated local topic terms."""
    keyword_query = " OR ".join(f'all:"{keyword}"' for keyword in topic.keywords)
    category_query = " OR ".join(f"cat:{category}" for category in topic.categories)
    return f"({keyword_query}) AND ({category_query})"


class ResearchPaper(StrictModel):
    """Immutable paper metadata fetched from one allow-listed academic source."""

    paper_key: str = Field(min_length=4, max_length=200)
    source: ResearchSource
    source_id: str = Field(min_length=1, max_length=180)
    arxiv_id: str | None = Field(default=None, max_length=80)
    doi: str | None = Field(default=None, max_length=180)
    title: str = Field(min_length=3, max_length=500)
    abstract: str = Field(default="", max_length=16_000)
    published_at: date
    categories: tuple[str, ...] = Field(default=(), max_length=12)
    authors: tuple[str, ...] = Field(default=(), max_length=50)
    source_url: str = Field(min_length=1, max_length=300)
    fetched_at: datetime
    metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("title", "abstract")
    @classmethod
    def normalize_source_text(cls, value: str) -> str:
        """Keep display text bounded and whitespace-normalized."""
        return _clean_text(value)

    @field_validator("doi")
    @classmethod
    def normalize_optional_doi(cls, value: str | None) -> str | None:
        """Canonicalize provider DOI metadata before it enters the registry."""
        return normalize_doi(value) if value is not None else None

    @field_validator("fetched_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Require explicit timezone provenance for fetch timestamps."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware.")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_provenance_and_digest(self) -> ResearchPaper:
        """Check identifier, canonical URL, work key, categories, and metadata digest."""
        if self.source is ResearchSource.ARXIV:
            normalized = normalize_arxiv_id(self.source_id)
            if self.arxiv_id != normalized:
                raise ValueError("arXiv source_id and arxiv_id must match exactly.")
            if self.source_url != f"https://arxiv.org/abs/{normalized}":
                raise ValueError("arXiv source URL must be constructed from its canonical ID.")
            expected_key = f"doi:{self.doi}" if self.doi else f"arxiv:{arxiv_base_id(normalized)}"
        else:
            if not re.fullmatch(r"W\d+", self.source_id):
                raise ValueError("OpenAlex source_id must be a work ID such as W1234567890.")
            if not self.doi:
                raise ValueError("OpenAlex records require the DOI that was used to verify them.")
            if self.source_url != f"https://openalex.org/{self.source_id}":
                raise ValueError("OpenAlex source URL must be constructed from its work ID.")
            expected_key = f"doi:{self.doi}"
        if self.paper_key != expected_key:
            raise ValueError("paper_key does not match the verified DOI/arXiv identifier.")
        if any(not _CATEGORY.fullmatch(item) for item in self.categories):
            raise ValueError("Provider returned an invalid arXiv category token.")
        if any(not _clean_text(name) or len(name) > 200 for name in self.authors):
            raise ValueError("Provider returned an invalid author name.")
        if self.metadata_sha256 != _metadata_digest(
            self.model_dump(mode="json", exclude={"metadata_sha256"})
        ):
            raise ValueError("Research metadata digest does not match its fields.")
        return self

    @classmethod
    def from_verified_source(
        cls,
        *,
        paper_key: str,
        source: ResearchSource,
        source_id: str,
        arxiv_id: str | None,
        doi: str | None,
        title: str,
        abstract: str,
        published_at: date,
        categories: tuple[str, ...],
        authors: tuple[str, ...],
        source_url: str,
        fetched_at: datetime,
    ) -> ResearchPaper:
        """Create a record with a digest over the normalized source fields."""
        payload: dict[str, object] = {
            "paper_key": paper_key,
            "source": source,
            "source_id": source_id,
            "arxiv_id": arxiv_id,
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "published_at": published_at,
            "categories": categories,
            "authors": authors,
            "source_url": source_url,
            "fetched_at": fetched_at,
        }
        normalized = cls.model_construct(
            paper_key=paper_key,
            source=source,
            source_id=source_id,
            arxiv_id=arxiv_id,
            doi=doi,
            title=title,
            abstract=abstract,
            published_at=published_at,
            categories=categories,
            authors=authors,
            source_url=source_url,
            fetched_at=fetched_at,
            metadata_sha256="0" * 64,
        ).model_dump(mode="json", exclude={"metadata_sha256"})
        payload["metadata_sha256"] = _metadata_digest(normalized)
        return cls.model_validate(payload)

    @property
    def identifiers(self) -> tuple[PaperIdentifier, ...]:
        """Return canonical aliases available for cross-source deduplication."""
        identifiers: list[PaperIdentifier] = []
        if self.arxiv_id:
            identifiers.append(
                PaperIdentifier(
                    kind=IdentifierKind.ARXIV,
                    value=arxiv_base_id(self.arxiv_id),
                )
            )
        if self.doi:
            identifiers.append(PaperIdentifier(kind=IdentifierKind.DOI, value=self.doi))
        return tuple(identifiers)


class QuotedEvidence(StrictModel):
    """A source claim tied to an exact title/abstract excerpt, not model memory."""

    field: str = Field(pattern=r"^(title|abstract)$")
    quote: str = Field(min_length=8, max_length=1000)


class EngineeringAssessment(StrictModel):
    """Explicitly inferential engineering assessment kept separate from paper facts."""

    problem_and_method: str = Field(min_length=8, max_length=2000)
    assumptions_and_limitations: str = Field(min_length=8, max_length=2000)
    system_fit: str = Field(min_length=8, max_length=2000)
    proposed_experiment: str = Field(min_length=8, max_length=2000)
    incremental_cost: str = Field(min_length=4, max_length=1000)
    likely_breakage: str = Field(min_length=8, max_length=2000)


class ResearchEvaluation(StrictModel):
    """LLM/user extraction with quoted source evidence and a separately labeled inference."""

    source_evidence: tuple[QuotedEvidence, ...] = Field(min_length=1, max_length=12)
    engineering_assessment: EngineeringAssessment
    evaluator: str = Field(default="agent", min_length=2, max_length=40)


class VerificationGates(StrictModel):
    """Measured local sandbox checks required before a candidate can be marked tested."""

    accuracy_passed: bool
    latency_passed: bool
    resource_passed: bool
    edge_cases_passed: bool

    @property
    def all_passed(self) -> bool:
        """Return true only when every independent acceptance gate passed."""
        return all(
            (
                self.accuracy_passed,
                self.latency_passed,
                self.resource_passed,
                self.edge_cases_passed,
            )
        )


class TestEvidence(StrictModel):
    """Reproducible proof for a sandbox experiment; not a production deployment grant."""

    experiment: str = Field(min_length=8, max_length=1000)
    environment: str = Field(min_length=3, max_length=300)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gates: VerificationGates
    measurements: dict[str, float] = Field(min_length=1, max_length=24)
    tested_at: datetime

    @field_validator("measurements")
    @classmethod
    def require_finite_measurements(cls, values: dict[str, float]) -> dict[str, float]:
        """Reject NaN/infinite measurements that cannot be compared deterministically."""
        import math

        if any(
            not math.isfinite(value) or not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", key)
            for key, value in values.items()
        ):
            raise ValueError("Measurement keys and values must be finite and bounded.")
        return values

    @field_validator("tested_at")
    @classmethod
    def require_test_timezone(cls, value: datetime) -> datetime:
        """Require an explicit timezone for experiment evidence."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("tested_at must be timezone-aware.")
        return value.astimezone(UTC)


class ResearchRecord(StrictModel):
    """Stored research candidate plus triage/evaluation state."""

    paper: ResearchPaper
    topics: tuple[str, ...] = Field(min_length=1, max_length=12)
    status: ResearchStatus = ResearchStatus.CANDIDATE
    evaluation: ResearchEvaluation | None = None
    decision_reason: str | None = Field(default=None, max_length=1000)
    test_evidence: TestEvidence | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def check_state_evidence(self) -> ResearchRecord:
        """Ensure status cannot claim a test without passing measured gates."""
        if self.status is ResearchStatus.TESTED:
            if self.test_evidence is None or not self.test_evidence.gates.all_passed:
                raise ValueError("tested status requires all verification gates to pass.")
        elif self.status is ResearchStatus.REJECTED:
            if not self.decision_reason or self.test_evidence is not None:
                raise ValueError("rejected status requires a reason and no test evidence.")
        elif self.test_evidence is not None or self.decision_reason is not None:
            raise ValueError("candidate status cannot contain a terminal decision.")
        return self


class ResearchGateway(Protocol):
    """Source boundary used by the radar; no arbitrary URL or caller metadata crosses it."""

    async def search(self, query: str, *, limit: int) -> tuple[ResearchPaper, ...]:
        """Search the configured read-only paper index using a constrained query."""
        ...

    async def resolve(self, identifier: PaperIdentifier) -> ResearchPaper:
        """Resolve one canonical identifier against its configured academic source."""
        ...
