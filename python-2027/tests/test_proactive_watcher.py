"""Hermetic read-only watcher tests; no Google credentials or network."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from ai2027_protocol.connector_hub_bridge import LiveConnectorClient
from ai2027_protocol.proactive_watcher import ProactiveWatcher, _run

NOW = datetime(2026, 11, 26, tzinfo=UTC)


def event(id: str, start: str, end: str, **extra: Any) -> dict[str, Any]:
    return {"id": id, "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


@pytest.mark.asyncio
async def test_snapshot_pagination_conflicts_and_no_private_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token")
    requests = []
    generation = 0

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer test-token"
        if "gmail" in request.url.host:
            if request.url.params.get("pageToken"):
                return httpx.Response(200, json={"messages": [{"id": "m2"}]})
            msgs = [{"id": "m1"}]
            if generation:
                msgs.append({"id": "m3"})
            return httpx.Response(200, json={"messages": msgs, "nextPageToken": "p2"})
        return httpx.Response(
            200,
            json={
                "items": [
                    event("a", "2026-11-26T10:00:00+02:00", "2026-11-26T11:00:00+02:00"),
                    event("b", "2026-11-26T08:30:00Z", "2026-11-26T09:30:00Z"),
                    event(
                        "c",
                        "2026-11-26T08:30:00Z",
                        "2026-11-26T09:30:00Z",
                        transparency="transparent",
                    ),
                    event("d", "2026-11-26T09:30:00Z", "2026-11-26T10:30:00Z"),
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        watcher = ProactiveWatcher(LiveConnectorClient(client=http))
        first = await watcher.poll(now=NOW)
        assert first.baseline and first.new_count == 6
        assert len(first.conflicts) == 1
        proposal = first.task_proposals[0]
        assert proposal.epistemic == "inferred" and proposal.taint == "tainted"
        assert proposal.consent == "not_granted" and proposal.status == "open"
        assert (proposal.source_id, proposal.related_source_id) == first.conflicts[0]
        assert all(s.taint == "tainted" and s.consent == "not_granted" for s in first.sources)
        assert all("m1" not in s.id for s in first.sources)
        generation = 1
        second = await watcher.poll(now=NOW)
        assert not second.baseline and second.new_count == 1
        assert second.task_proposals[0].dedupe_key == proposal.dedupe_key
        assert (await watcher.poll(now=NOW)).new_count == 0
    assert all("subject" not in str(req.url) for req in requests)
    assert len(requests) == 9


@pytest.mark.asyncio
async def test_failure_never_advances_baseline_or_retries_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "x")
    fail = False
    calendar_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calendar_attempts
        assert request.method == "GET"
        if "gmail" in request.url.host:
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        calendar_attempts += 1
        if fail:
            return httpx.Response(503, text="private content")
        return httpx.Response(200, json={"items": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        watcher = ProactiveWatcher(LiveConnectorClient(client=http))
        assert (await watcher.poll(now=NOW)).new_count == 2
        # Failed cycles must not update state.
        assert watcher._seen is not None
        watcher._seen = {watcher._seen.pop()}  # retain exactly one previous ID
        fail = True
        with pytest.raises(RuntimeError, match="after 3 attempts"):
            await watcher.poll(now=NOW)
        assert calendar_attempts == 4
        fail = False
        assert (await watcher.poll(now=NOW)).new_count == 1


@pytest.mark.asyncio
async def test_overflow_and_bad_page_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": [{"id": str(i)} for i in range(1001)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        watcher = ProactiveWatcher(LiveConnectorClient(client=http))
        with pytest.raises(ValueError, match="limit exceeded"):
            await watcher.poll(now=NOW)
        assert watcher._seen is None


@pytest.mark.asyncio
async def test_all_day_event_uses_calendar_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "x")

    def handler(request: httpx.Request) -> httpx.Response:
        if "gmail" in request.url.host:
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "a", "start": {"date": "2026-11-26"}, "end": {"date": "2026-11-27"}},
                    event("b", "2026-11-25T22:00:00Z", "2026-11-25T23:00:00Z"),
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        watcher = ProactiveWatcher(LiveConnectorClient(client=http), calendar_zone="Africa/Cairo")
        assert len((await watcher.poll(now=NOW)).conflicts) == 1


@pytest.mark.asyncio
async def test_no_credentials_fails_without_outputting_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GOOGLE_ACCESS_TOKEN", raising=False)
    assert await _run(900, True, "UTC") == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "failed", "error": "RuntimeError", "writes": 0}
