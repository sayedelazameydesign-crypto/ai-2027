"""Topic-allow-listed research search with no automatic persistence or adoption."""

from __future__ import annotations

from datetime import date, timedelta

from .research_contracts import (
    ResearchConfigurationError,
    ResearchGateway,
    ResearchInputError,
    ResearchPaper,
    ResearchSource,
    ResearchTopic,
    build_arxiv_query,
    parse_paper_identifier,
)


class ResearchRadar:
    """Topic-constrained search and verified-ID resolution without autonomous adoption."""

    def __init__(
        self,
        topics: tuple[ResearchTopic, ...],
        gateway: ResearchGateway,
    ) -> None:
        """Create a radar with an explicit allow-list and a replaceable source gateway."""
        self._topics = {topic.id: topic for topic in topics}
        self._gateway = gateway
        if not self._topics:
            raise ResearchConfigurationError("At least one research topic is required.")

    @property
    def topics(self) -> tuple[ResearchTopic, ...]:
        """Return configured topics in deterministic priority order."""
        return tuple(sorted(self._topics.values(), key=lambda topic: (topic.priority, topic.id)))

    async def search(
        self,
        topic_id: str,
        *,
        window_days: int = 180,
        limit: int = 10,
        today: date | None = None,
    ) -> tuple[ResearchPaper, ...]:
        """Return recent, category-matched source records without persisting them."""
        if not 1 <= window_days <= 3650 or not 1 <= limit <= 20:
            raise ResearchInputError("Search window must be 1–3650 days and limit 1–20.")
        topic = self._topics.get(topic_id)
        if topic is None:
            raise ResearchInputError("Unknown research topic; choose a configured topic ID.")
        as_of = today or date.today()
        cutoff = as_of - timedelta(days=window_days)
        results = await self._gateway.search(build_arxiv_query(topic), limit=min(50, limit * 5))
        allowed_categories = set(topic.categories)
        seen: set[str] = set()
        accepted: list[ResearchPaper] = []
        for paper in sorted(
            results, key=lambda item: (item.published_at, item.paper_key), reverse=True
        ):
            if (
                paper.source is not ResearchSource.ARXIV
                or paper.published_at < cutoff
                or paper.published_at > as_of
                or not allowed_categories.intersection(paper.categories)
                or paper.paper_key in seen
            ):
                continue
            seen.add(paper.paper_key)
            accepted.append(paper)
            if len(accepted) >= limit:
                break
        return tuple(accepted)

    async def resolve(self, raw_identifier: str) -> ResearchPaper:
        """Fetch metadata by verified identifier; reject arbitrary links and model payloads."""
        return await self._gateway.resolve(parse_paper_identifier(raw_identifier))
