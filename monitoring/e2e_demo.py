"""End-to-end: a watchdog watching a REAL http endpoint that dies mid-run.

This is the demonstration the design has to survive, not a unit test:
  1. the endpoint answers            -> poll, state recorded
  2. the endpoint is killed          -> poll_failed (NOT silence), loop survives
  3. the endpoint comes back         -> poll, recovery recorded
  4. the process is killed hard      -> the log is left without a `stop`
  5. the loop restarts               -> the hole is recorded as `silence`
  6. the file is edited by hand      -> verify exits 1

Run: python3 monitoring/e2e_demo.py
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import local_watchdog as wd  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    mode = {"down": False}

    def do_GET(self):
        if self.mode["down"]:
            self.send_error(503, "service unavailable")
            return
        body = json.dumps({"status": "live", "deploy": "d-1"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def make_source(port: int):
    def snap():
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            return json.loads(r.read())
    return snap


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="watchdog-e2e-"))
    log_path = tmp / "watch.jsonl"
    log = wd.EventLog(log_path, anchor_path=tmp / "watch.anchor")

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"source listening on 127.0.0.1:{port}")

    # --- phase 1+2: healthy, then the endpoint dies ---------------------- #
    polls = {"n": 0}
    original = make_source(port)

    def source():
        polls["n"] += 1
        if polls["n"] == 4:
            Handler.mode["down"] = True
        if polls["n"] == 7:
            Handler.mode["down"] = False
        return original()

    result = wd.run(interval=0.05, log=log, source=source, max_polls=10)
    print(f"ran {result['polls']} polls, {result['failures']} failures")

    # --- phase 3: a hard kill, so the log has no `stop` ------------------ #
    killer = subprocess.Popen(
        [sys.executable, str(HERE / "local_watchdog.py"), "run",
         "--log", str(log_path), "--interval", "0.05", "--polls", "10000",
         "--source", str(HERE / "e2e_fixture.json")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.2)
    killer.send_signal(signal.SIGKILL)
    killer.wait()
    (HERE / "e2e_fixture.json").unlink(missing_ok=True)
    print("killed a running watchdog with SIGKILL (no stop record written)")

    # --- phase 4: restart after a real pause ----------------------------- #
    # The pause must exceed the watchdog's min_gap floor (1s) or the gap is
    # correctly classified as jitter and NOT recorded as silence. That floor is
    # the point: sub-second lateness is not an outage.
    time.sleep(1.6)
    wd.run(interval=0.05, log=log, source=original, max_polls=3)

    srv.shutdown()
    recs, _ = log.read_all()
    rep = wd.report(recs)
    verify = log.verify()
    kinds = rep["kinds"]

    print("\n--- what the log actually contains ---")
    print(json.dumps({k: kinds.get(k, 0) for k in
                      ("start", "poll", "poll_failed", "silence", "stop")},
                     indent=2))
    print(f"failure classes:   {rep['failure_classes']}")
    print(f"silence events:    {rep['silence_events']}"
          f" (largest {rep['silence_seconds_largest']}s)")
    print(f"observed fraction: {rep['observed_fraction']}")
    print(f"chain intact:      {verify['intact']}  clean: {verify['clean']}")

    checks = {
        "a failure was recorded as poll_failed, not silence":
            kinds.get("poll_failed", 0) >= 1,
        "the hard kill left a gap that was recorded as silence":
            kinds.get("silence", 0) >= 1,
        "the loop survived the dead endpoint": result["failures"] >= 1,
        "state was tracked across the outage": bool(rep["last_snapshot"]),
        "the chain verified after a SIGKILL": verify["intact"],
    }

    # --- phase 5: edit the file by hand --------------------------------- #
    raw = log_path.read_bytes().replace(b'"live"', b'"down"', 1)
    log_path.write_bytes(raw)
    tampered = log.verify()
    checks["hand-editing one value is detected"] = not tampered["intact"]

    print("\n--- checks ---")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
