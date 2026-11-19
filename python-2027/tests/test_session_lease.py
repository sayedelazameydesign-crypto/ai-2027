"""Evidence tests for gap R1: one writer per session, ordered by a fence.

The audit measured the defect directly (4 writers x 60 saves on one session,
23-61 lost saves out of 240, up to 13 torn reads). These tests pin the fix so a
regression fails here instead of surfacing as a corrupted session.json.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ai2027_protocol.lease import LeaseError, LeaseLostError, SqliteLeaseStore
from ai2027_protocol.server import create_app
from ai2027_protocol.session_state import AgentSessionState
from ai2027_protocol.session_store import SessionBusyError, SessionStore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SID = "sess_lease00000001"


def _state(session_id: str = _SID, *, title: str = "Lease") -> AgentSessionState:
    now = datetime.now(UTC).isoformat()
    return AgentSessionState(session_id=session_id, title=title, created_at=now, updated_at=now)


def _store(tmp_path: Path, mode: str) -> SessionStore:
    return SessionStore(tmp_path / "studio", lease_mode=mode, lease_ttl_s=5.0)


def test_off_mode_is_the_historical_behaviour(tmp_path: Path) -> None:
    """Default mode: no lease database, no refusal — nothing silently changes."""
    store = SessionStore(tmp_path / "studio")
    assert store.lease_mode == "off"
    assert store.lease_enabled is False
    store.save_sync(_state())
    store.save_sync(_state(title="second"))
    assert store.load_sync(_SID).title == "second"
    with pytest.raises(LeaseError):
        store.acquire_lease(_SID)


def test_live_lease_refuses_a_second_writer(tmp_path: Path) -> None:
    """A held lease answers precisely: who holds it, and for how much longer."""
    store = _store(tmp_path, "enforce")
    held = store.acquire_lease(_SID, owner="worker-a")
    with pytest.raises(SessionBusyError) as busy:
        store.acquire_lease(_SID, owner="worker-b")
    assert busy.value.owner == "worker-a"
    assert busy.value.session_id == _SID
    assert 0.0 < busy.value.remaining_s <= 5.0
    current = store.current_lease(_SID)
    assert current is not None and current.fence == held.fence


def test_lease_is_contended_across_processes(tmp_path: Path) -> None:
    """The guarantee is cross-process, not just threads in one interpreter."""
    studio = tmp_path / "studio"
    script = textwrap.dedent(
        """
        import sys, time
        from pathlib import Path
        from ai2027_protocol.session_store import SessionStore

        store = SessionStore(Path(sys.argv[1]), lease_mode="enforce", lease_ttl_s=30.0)
        lease = store.acquire_lease("sess_lease00000001", owner="child")
        print(lease.fence, flush=True)
        time.sleep(30)
        """
    )
    child = subprocess.Popen(  # noqa: S603 - fixed interpreter, repo-owned script
        [sys.executable, "-c", script, str(studio)],
        stdout=subprocess.PIPE,
        text=True,
        env={"PYTHONPATH": str(_REPO_ROOT / "python-2027" / "src"), "PATH": "/usr/bin:/bin"},
    )
    try:
        assert child.stdout is not None
        fence = int(child.stdout.readline().strip())
        parent = SessionStore(studio, lease_mode="enforce")
        with pytest.raises(SessionBusyError) as busy:
            parent.acquire_lease(_SID)
        assert busy.value.owner == "child"
        current = parent.current_lease(_SID)
        assert current is not None and current.fence == fence
    finally:
        child.kill()
        child.wait(timeout=10)


def test_expired_lease_is_taken_over_with_a_new_fence(tmp_path: Path) -> None:
    """A crashed writer must not lock a session forever: expiry moves the fence."""
    store = SqliteLeaseStore(tmp_path / "leases.db")
    store.acquire(_SID, "crashed", ttl_s=0.01)
    time.sleep(0.05)
    taken = store.acquire(_SID, "successor", ttl_s=5.0)
    assert taken.fence == 2
    assert taken.owner == "successor"


def test_stale_writer_is_refused_under_enforce(tmp_path: Path) -> None:
    """The whole point of the fence: a slow writer cannot clobber newer state."""
    store = _store(tmp_path, "enforce")
    store.lease_ttl_s = 0.05
    stale = store.acquire_lease(_SID)
    store.save_sync(_state(), lease=stale)

    time.sleep(0.15)
    fresh = store.acquire_lease(_SID, owner="successor")
    assert fresh.fence == stale.fence + 1

    with pytest.raises(LeaseLostError):
        store.save_sync(_state(title="stale overwrite"), lease=stale)
    store.save_sync(_state(title="fresh"), lease=fresh)
    assert store.load_sync(_SID).title == "fresh"


def test_first_write_of_a_new_session_needs_no_lease(tmp_path: Path) -> None:
    """A brand-new uuid cannot be contended, so creation stays frictionless."""
    store = _store(tmp_path, "enforce")
    store.save_sync(_state())
    assert store.load_sync(_SID).title == "Lease"
    with pytest.raises(LeaseLostError):
        store.save_sync(_state(title="no lease"))


def test_warn_mode_records_the_refusal_but_still_writes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``warn`` exists for staged rollout: visible in logs, behaviour unchanged."""
    store = _store(tmp_path, "warn")
    store.lease_ttl_s = 0.05
    stale = store.acquire_lease(_SID)
    store.save_sync(_state(), lease=stale)
    time.sleep(0.15)
    store.acquire_lease(_SID, owner="successor")

    with caplog.at_level("WARNING"):
        store.save_sync(_state(title="warned"), lease=stale)
    assert store.load_sync(_SID).title == "warned"
    assert any("lease stale" in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize("path", ["/run", "/stream"])
async def test_busy_session_answers_409_before_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Neither endpoint may open a run or a stream on a session someone else owns."""
    monkeypatch.setenv("AI2027_SESSION_LEASE", "enforce")
    app = create_app(workspace_root=_REPO_ROOT, studio_dir=tmp_path / "studio")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        created = await client.post("/v1/studio/sessions", json={"title": "Busy"})
        sid = created.json()["session_id"]

        holder = SessionStore(tmp_path / "studio", lease_mode="enforce")
        lease = holder.acquire_lease(sid, owner="other-worker")
        try:
            resp = await client.post(
                f"/v1/studio/sessions/{sid}{path}", json={"task": "Build SVG chart"}
            )
        finally:
            holder.release_lease(lease)

        assert resp.status_code == 409
        assert "other-worker" in resp.json()["detail"]

        after = await client.post(
            f"/v1/studio/sessions/{sid}{path}", json={"task": "Build SVG chart"}
        )
        assert after.status_code == 200


def test_a_finished_run_leaves_no_lease_behind(tmp_path: Path) -> None:
    """A completed run must free the session, or the next user click hits a 409."""
    store = _store(tmp_path, "enforce")
    lease = store.acquire_lease(_SID)
    store.save_sync(_state(), lease=lease)
    store.release_lease(lease)
    freed = store.current_lease(_SID)
    assert freed is not None and not freed.is_live
    store.release_lease(lease)  # releasing twice is safe
    assert _store(tmp_path, "enforce").acquire_lease(_SID).fence == lease.fence + 1
