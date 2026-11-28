"""Static guards for the $0 Supabase Edge deployment (docs/DEPLOY-FREE-NO-CARD.md).

* the parity vectors the Deno tests rely on are exactly what the REFERENCE Python
  stack produces today (so a Python change cannot silently desync the port);
* every table the migration creates has RLS enabled, every ai2027_* function is
  revoked from anon/authenticated, and the watchdog log is append-only;
* the Edge Function has no third-party imports and never logs/echoes secrets.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FN = ROOT / "supabase" / "functions" / "ycloud-webhook"
MIGRATION = ROOT / "supabase" / "migrations" / "20261128000000_ai2027_free_stack.sql"


def test_parity_vectors_match_reference_python() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "supabase_edge_parity_vectors.py")],
        check=True, capture_output=True, text=True, cwd=ROOT,
    ).stdout
    committed = json.loads((FN / "parity_vectors.json").read_text(encoding="utf-8"))
    assert json.loads(out) == committed, "regenerate parity_vectors.json from the Python reference"


def test_every_table_has_rls_and_functions_are_service_role_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    tables = set(re.findall(r"create table if not exists public\.(\w+)", sql))
    rls = set(re.findall(r"alter table public\.(\w+)\s+enable row level security", sql))
    assert tables and tables == rls, f"RLS missing on: {sorted(tables - rls)}"
    assert "create policy" not in sql.lower(), "no policies: anon/authenticated must read nothing"
    defined = set(re.findall(r"create or replace function (public\.ai2027_\w+)", sql))
    granted_block = sql[sql.rindex("foreach f in array array["):]
    revoked = set(re.findall(r"'(public\.ai2027_\w+)\(", granted_block))
    assert defined == revoked, f"not revoked from anon/authenticated: {sorted(defined - revoked)}"


def test_watchdog_log_is_append_only_and_skip_is_never_pass() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "before update or delete on public.watchdog_runs" in sql
    assert "before truncate on public.watchdog_runs" in sql
    verdict = re.search(r"v_verdict := case(.*?)end;", sql, re.S).group(1)
    assert "when v_passed = v_total then 'pass'" in verdict
    assert "else 'degraded'" in verdict


def test_edge_function_is_dependency_free_and_does_not_leak() -> None:
    src = (FN / "index.ts").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s", src, re.M), "single-file deploy: no imports"
    assert "console.log" not in src and "console.error" not in src
    assert "verify_jwt=false" in src  # documented: HMAC is the authentication
    # error path must not echo PostgREST bodies
    assert "failed: HTTP ${res.status}`" in src
