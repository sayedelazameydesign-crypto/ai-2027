"""Local, single-use approvals for the verified file action.

This is an audit/workflow gate, NOT an authentication or OS security boundary.
Only use a private, trusted runtime; anyone with write access can forge approvals.
"""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


def canonical(params):
    if not isinstance(params, dict) or set(params) != {"path", "content"}:
        raise ValueError("expected exactly path and content")
    if not all(isinstance(params[k], str) and len(params[k]) <= 4096 for k in params):
        raise ValueError("invalid parameters")
    return json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class ApprovalGate:
    def __init__(self, runtime):
        runtime = Path(runtime).resolve()
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = runtime / "approvals.sqlite"
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, action TEXT NOT NULL, params TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
                expires REAL NOT NULL, decided REAL, claimed REAL, result TEXT
            )""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def request(self, action, params, ttl=900):
        if action != "verified_file":
            raise ValueError("unsupported action")
        if not isinstance(ttl, int) or not 1 <= ttl <= 3600:
            raise ValueError("expiry must be between 1 and 3600 seconds")
        payload = canonical(params)
        digest = hashlib.sha256((action + "\n" + payload).encode()).hexdigest()
        request_id = uuid.uuid4().hex
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO approvals (id,action,params,digest,state,created,expires) VALUES (?,?,?,?,?,?,?)",
                       (request_id, action, payload, digest, "PENDING", now, now + ttl))
        return self.get(request_id)

    def get(self, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("approval request not found")
        result = dict(row)
        result["params"] = json.loads(result["params"])
        if result["result"] is not None:
            result["result"] = json.loads(result["result"])
        return result

    def decide(self, request_id, decision, expected_digest):
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError("invalid decision")
        now = time.time()
        with self.connect() as db:
            changed = db.execute("UPDATE approvals SET state=?, decided=? WHERE id=? AND digest=? AND state='PENDING' AND expires>?",
                                 (decision, now, request_id, expected_digest, now)).rowcount
        if changed != 1:
            raise ValueError("request missing, changed, expired, or already decided")
        return self.get(request_id)

    def execute(self, request_id, expected_digest, executor):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM approvals WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("approval request not found")
            actual = hashlib.sha256((row['action'] + "\n" + row['params']).encode()).hexdigest()
            if (row['state'] != 'APPROVED' or row['expires'] <= now or
                    row['digest'] != expected_digest or row['digest'] != actual or row['action'] != 'verified_file'):
                raise ValueError("approval missing, expired, changed, or already used")
            db.execute("UPDATE approvals SET state='CLAIMED', claimed=? WHERE id=?", (now, request_id))
        # A crash after claim is an unknown outcome: never automatically retry.
        try:
            result = executor.run('verified_file', json.loads(row['params']))
            memory = result.get('memory') or {}
            success = (result.get('status') == 'VERIFIED' and result.get('independent_hash_matches') is True
                       and memory.get('admitted') is True and memory.get('artifact_verified') is True)
            state = 'COMPLETED' if success else 'FAILED'
        except Exception:
            result, state = {"error": "execution failed; outcome may be unknown"}, 'UNKNOWN'
        with self.connect() as db:
            db.execute("UPDATE approvals SET state=?, result=? WHERE id=? AND state='CLAIMED'",
                       (state, json.dumps(result, ensure_ascii=False), request_id))
        return self.get(request_id)
