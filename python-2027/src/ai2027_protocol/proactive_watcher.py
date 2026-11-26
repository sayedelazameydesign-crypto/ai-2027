"""Read-only, metadata-minimising Gmail/Calendar polling for P1.

This module intentionally has no write or action executor. Connector results are
untrusted data, never prompts or instructions. See docs/PROACTIVE-WATCHER.md.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .connector_hub_bridge import LiveConnectorClient
from .watcher_state import WatcherState

_MAX_ITEMS = 1000
_MAX_PAGES = 10


def _identity(kind: str, external_id: str) -> str:
    # This is an identifier, not anonymisation: IDs may be guessable.
    return hashlib.sha256(f"{kind}:{external_id}".encode()).hexdigest()


def _moment(value: dict[str, Any], zone: ZoneInfo) -> datetime:
    if not isinstance(value, dict):
        raise ValueError("invalid Calendar time")
    if isinstance(value.get("dateTime"), str):
        parsed = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Calendar time has no UTC offset")
        return parsed.astimezone(UTC)
    if isinstance(value.get("date"), str):
        return datetime.combine(date.fromisoformat(value["date"]), time.min, zone).astimezone(UTC)
    raise ValueError("Calendar event is missing a time")


@dataclass(frozen=True)
class Source:
    """Minimal ephemeral source record; no subject, address, body, or URL."""

    id: str
    source_type: str
    taint: str = "tainted"
    consent: str = "not_granted"


@dataclass(frozen=True)
class Event:
    """Calendar interval with a hashed source identifier."""

    source: Source
    start: datetime
    end: datetime


@dataclass(frozen=True)
class TaskProposal:
    """A non-executable, transient inferred conflict requiring human review."""

    source_id: str
    related_source_id: str
    dedupe_key: str
    title: str = "Review a possible calendar conflict"
    status: str = "open"
    epistemic: str = "inferred"
    taint: str = "tainted"
    consent: str = "not_granted"


@dataclass(frozen=True)
class Observation:
    """One complete poll, held in memory only."""

    sources: tuple[Source, ...]
    conflicts: tuple[tuple[str, str], ...]
    task_proposals: tuple[TaskProposal, ...]
    new_count: int
    baseline: bool


def _read_token() -> str:
    """Read a token afresh on each poll, preferring a private file to the environment."""
    token_path = os.environ.get("GOOGLE_ACCESS_TOKEN_FILE", "").strip()
    if token_path:
        if not Path(token_path).is_absolute():
            raise ValueError("token path must be absolute")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(token_path, flags)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
                    raise ValueError("token file must be owner-only")
                data = stream.read(16385)
        except OSError:
            raise RuntimeError("cannot read token file") from None
        if len(data) > 16384:
            raise ValueError("token file too large")
        try:
            token = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise ValueError("invalid token file") from None
    else:
        token = os.environ.get("GOOGLE_ACCESS_TOKEN", "").strip()
    if not token or any(ch.isspace() for ch in token):
        raise RuntimeError("Google access token is not configured")
    return token


class ProactiveWatcher:
    """Poll both providers read-only and report aggregate observations."""

    def __init__(
        self,
        client: LiveConnectorClient,
        *,
        calendar_zone: str = "UTC",
        state: WatcherState | None = None,
    ) -> None:
        """Use the connector transport and optional local state."""
        self._client = client
        self._state = state
        self._zone = ZoneInfo(calendar_zone)
        self._seen: set[str] | None = None

    async def _get(self, url: str, token: str, params: dict[str, Any]) -> dict[str, Any]:
        # Reads only. Do not echo provider errors (which can contain private data).
        for attempt in range(3):
            try:
                result: dict[str, Any] = await self._client._request(  # noqa: SLF001
                    "GET", url, headers={"Authorization": f"Bearer {token}"}, params=params
                )
                return result
            except Exception:
                if attempt == 2:
                    raise RuntimeError("connector read failed after 3 attempts") from None
                await asyncio.sleep(0.25 * (2**attempt))
        raise AssertionError("unreachable")

    async def _pages(
        self, url: str, token: str, params: dict[str, Any], key: str
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(_MAX_PAGES):
            page_params = dict(params)
            if page_token:
                page_params["pageToken"] = page_token
            page = await self._get(url, token, page_params)
            items = page.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("invalid connector page")
            results.extend(items)
            if len(results) > _MAX_ITEMS:
                raise ValueError("poll item limit exceeded; no snapshot committed")
            next_token = page.get("nextPageToken")
            if not next_token:
                return results
            if not isinstance(next_token, str) or next_token in seen_tokens:
                raise ValueError("invalid pagination token")
            seen_tokens.add(next_token)
            page_token = next_token
        raise ValueError("poll page limit exceeded; no snapshot committed")

    async def poll(self, *, now: datetime | None = None) -> Observation:
        """Read a complete snapshot. Failure leaves the previous baseline untouched."""
        token = _read_token()
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        current = current.astimezone(UTC)
        gmail = await self._pages(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            token,
            {
                "q": "in:inbox newer_than:7d",
                "maxResults": 100,
                "fields": "messages(id),nextPageToken",
            },
            "messages",
        )
        calendar = await self._pages(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            token,
            {
                "timeMin": current.isoformat(),
                "timeMax": (current + timedelta(days=7)).isoformat(),
                "singleEvents": "true",
                "maxResults": 250,
                "fields": "items(id,start,end,status,transparency),nextPageToken",
            },
            "items",
        )
        sources: dict[str, Source] = {}
        events: dict[str, Event] = {}
        for item in gmail:
            external_id = item.get("id")
            if not isinstance(external_id, str) or not external_id:
                raise ValueError("message has no id")
            source = Source(_identity("gmail", external_id), "gmail")
            sources[source.id] = source
        for item in calendar:
            if item.get("status") == "cancelled":
                continue
            external_id = item.get("id")
            if not isinstance(external_id, str) or not external_id:
                raise ValueError("event has no id")
            source = Source(_identity("calendar", external_id), "calendar")
            sources[source.id] = source
            if item.get("transparency") == "transparent":
                continue
            start, end = (
                _moment(item.get("start", {}), self._zone),
                _moment(item.get("end", {}), self._zone),
            )
            if end <= start:
                raise ValueError("invalid event interval")
            events[source.id] = Event(source, start, end)
        ordered = sorted(events.values(), key=lambda event: (event.start, event.source.id))
        conflicts: list[tuple[str, str]] = []
        for index, event in enumerate(ordered):
            for other in ordered[index + 1 :]:
                if other.start >= event.end:
                    break
                first, second = sorted((event.source.id, other.source.id))
                conflicts.append((first, second))
        ids = set(sources)
        if self._state is not None:
            baseline, new_count = self._state.observe(ids)
        else:
            baseline = self._seen is None
            previous = self._seen if self._seen is not None else set()
            new_count = len(ids - previous)
            self._seen = previous | ids
        proposals = tuple(
            TaskProposal(first, second, _identity("calendar-conflict", f"{first}:{second}"))
            for first, second in conflicts
        )
        return Observation(
            tuple(sources.values()), tuple(conflicts), proposals, new_count, baseline
        )


async def _run(interval: int, once: bool, zone: str, state: WatcherState | None = None) -> int:
    watcher = ProactiveWatcher(LiveConnectorClient(), calendar_zone=zone, state=state)
    while True:
        try:
            result = await watcher.poll()
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "baseline": result.baseline,
                        "sources": len(result.sources),
                        "new_sources": result.new_count,
                        "calendar_conflicts": len(result.conflicts),
                        "task_proposals": len(result.task_proposals),
                        "writes": 0,
                    }
                ),
                flush=True,
            )
        except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
            # ValueError messages in this module are fixed; never print provider responses.
            failure = {"status": "failed", "error": type(exc).__name__, "writes": 0}
            print(json.dumps(failure), flush=True)
            if once:
                return 1
        if once:
            return 0
        await asyncio.sleep(interval)


def main() -> int:
    """Run a read-only poll once or every 15–30 minutes while the process lives."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=900, help="Seconds, 900–1800")
    parser.add_argument("--calendar-timezone", default="UTC", help="IANA zone for all-day events")
    parser.add_argument("--state-db", help="Absolute path in an owner-only directory")
    parser.add_argument("--account-scope", help="Stable operator-assigned account label")
    args = parser.parse_args()
    if not 900 <= args.interval <= 1800:
        parser.error("interval must be between 900 and 1800 seconds")
    if bool(args.state_db) != bool(args.account_scope):
        parser.error("--state-db and --account-scope must be supplied together")
    try:
        # Validate the time zone before opening any persistent state.
        ZoneInfo(args.calendar_timezone)
        state = WatcherState(Path(args.state_db), args.account_scope) if args.state_db else None
        return asyncio.run(_run(args.interval, args.once, args.calendar_timezone, state))
    except KeyboardInterrupt:
        return 1
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "stage": "initialization",
                    "error": type(exc).__name__,
                    "writes": 0,
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
