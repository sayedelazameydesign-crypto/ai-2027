"""Local state and token-file tests, without live credentials."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from ai2027_protocol.connector_hub_bridge import LiveConnectorClient
from ai2027_protocol.proactive_watcher import ProactiveWatcher, _read_token
from ai2027_protocol.watcher_state import WatcherState


def private_dir(tmp_path: Path) -> Path:
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    return path


def test_state_survives_restart_and_rejects_wrong_scope(tmp_path: Path) -> None:
    path = private_dir(tmp_path) / "state.sqlite"
    a, b = "a" * 64, "b" * 64
    first = WatcherState(path, "account-one")
    assert first.observe({a}) == (True, 1)
    assert path.stat().st_mode & 0o777 == 0o600
    second = WatcherState(path, "account-one")
    assert second.observe({a, b}) == (False, 1)
    assert WatcherState(path, "account-one").observe({a, b}) == (False, 0)
    with pytest.raises(ValueError, match="different account"):
        WatcherState(path, "account-two")
    assert WatcherState(path, "account-one").observe({a, b}) == (False, 0)


def test_state_atomic_concurrent_snapshots(tmp_path: Path) -> None:
    path = private_dir(tmp_path) / "state.sqlite"
    first = WatcherState(path, "one")
    second = WatcherState(path, "one")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.observe({"a" * 64}), [first, second]))
    assert sorted(results) == [(False, 0), (True, 1)]


def test_state_rejects_unsafe_paths_and_invalid_ids(tmp_path: Path) -> None:
    directory = private_dir(tmp_path)
    directory.chmod(0o755)
    with pytest.raises(ValueError, match="directory"):
        WatcherState(directory / "state", "one")
    directory.chmod(0o700)
    secret = directory / "secret"
    secret.write_text("secret")
    secret.chmod(0o600)
    link = directory / "link"
    link.symlink_to(secret)
    with pytest.raises(OSError):
        WatcherState(link, "one")
    store = WatcherState(directory / "state", "one")
    with pytest.raises(ValueError, match="identifier"):
        store.observe({"raw id"})
    assert store.observe(set()) == (True, 0)


def test_token_file_reloaded_and_permissions_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = private_dir(tmp_path)
    path = directory / "token"
    path.write_text("first\n")
    path.chmod(0o600)
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN_FILE", str(path))
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "must-not-be-used")
    assert _read_token() == "first"
    path.write_text("second\n")
    assert _read_token() == "second"
    path.chmod(0o644)
    with pytest.raises(ValueError):
        _read_token()
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN_FILE", str(directory / "missing"))
    with pytest.raises(RuntimeError):
        _read_token()


@pytest.mark.asyncio
async def test_durable_state_unchanged_on_incomplete_provider_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test")
    monkeypatch.delenv("GOOGLE_ACCESS_TOKEN_FILE", raising=False)
    state = WatcherState(private_dir(tmp_path) / "state", "account")
    fail = True

    def handler(request: httpx.Request) -> httpx.Response:
        if "gmail" in request.url.host:
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if fail:
            return httpx.Response(200, json={"items": "invalid"})
        return httpx.Response(200, json={"items": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        watcher = ProactiveWatcher(LiveConnectorClient(client=http), state=state)
        with pytest.raises(ValueError):
            await watcher.poll()
        fail = False
        assert (await watcher.poll()).baseline
        watcher = ProactiveWatcher(LiveConnectorClient(client=http), state=state)
        result = await watcher.poll()
        assert result.new_count == 0 and not result.baseline
