#!/usr/bin/env python3
"""Phase 0 — Read-only Render audit. Changes NOTHING.

Run this first, before any integration work, with a real key:

    export RENDER_API_KEY=rnd_...
    python3 render_control_adapter/probe.py --report render-probe.json

It only issues GETs:
    /owners, /services, /services/{id}, /services/{id}/deploys,
    /services/{id}/env-vars   (keys and lengths ONLY — values are discarded)

Exit codes
    0  audit completed, every checked endpoint answered
    1  audit completed but at least one endpoint failed (see `failures`)
    2  no key present (refuses to run rather than pretending to be "not deployed")

WHY THIS EXISTS
---------------
The adapter itself has never been run against the live API: this sandbox's
egress policy blocks api.render.com (TLS reset; api.github.com returns 200).
Writing an adapter from documentation and *claiming* it works is exactly the
kind of unsupported claim this project refuses to make. This script is the
evidence generator: run it, keep the JSON, and the claim becomes real.

It reads no secret values. `env-vars` is fetched with the value discarded
immediately — the report records only the key name and value length.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapter  # noqa: E402


def redact_key(key: str) -> str:
    """Never print the API key, even truncated, in a report someone may share."""
    if not key:
        return ""
    return f"{key[:4]}…{key[-2:]}" if len(key) > 8 else "***"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", type=Path, default=None,
                    help="write the JSON audit here")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        sys.stderr.write(
            "NO KEY: set RENDER_API_KEY first. Phase 0 refuses to run blind —\n"
            "an empty audit and a successful audit must not look the same.\n")
        return 2

    client = adapter.RenderClient(api_key=key, timeout=args.timeout)
    report: dict = {
        "phase": 0,
        "mode": "read-only",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "key_fingerprint": redact_key(key),
        "base": client.base,
        "endpoints": {},
        "services": [],
        "env_vars": "KEY NAMES AND LENGTHS ONLY — values never read into the report",
        "failures": [],
        "mutations_performed": 0,
    }

    def step(name: str, fn):
        t0 = time.perf_counter()
        try:
            out = fn()
            report["endpoints"][name] = {"ok": True,
                                         "ms": round((time.perf_counter() - t0) * 1000, 1)}
            return out
        except adapter.ApiError as exc:
            report["endpoints"][name] = {
                "ok": False, "status": exc.status, "code": exc.code,
                "hint": ("check the key and its scopes" if exc.is_auth else
                         "rate limited — retry later" if exc.is_rate_limited else ""),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
            report["failures"].append(f"{name}: HTTP {exc.status} {exc.code}")
            return None
        except Exception as exc:  # noqa: BLE001
            report["endpoints"][name] = {"ok": False,
                                         "error": f"{type(exc).__name__}: {exc}"}
            report["failures"].append(f"{name}: {type(exc).__name__}: {exc}")
            return None

    owners = step("owners", lambda: client.list_owners())
    if owners is not None:
        report["workspaces"] = [{"id": o.get("id"), "name": o.get("name"),
                                 "type": o.get("type")} for o in owners]

    services = step("services", lambda: client.list_services())
    if services is not None:
        for svc in services:
            sid = svc.get("id")
            row = {
                "id": sid,
                "name": svc.get("name"),
                "type": svc.get("type"),
                "suspended": svc.get("suspended"),
                "autoDeploy": svc.get("autoDeploy"),
                "branch": svc.get("branch"),
                "repo": svc.get("repo"),
                "region": (svc.get("serviceDetails") or {}).get("region")
                          or svc.get("region"),
                "plan": (svc.get("serviceDetails") or {}).get("plan"),
                "runtime": (svc.get("serviceDetails") or {}).get("runtime"),
                "healthCheckPath": (svc.get("serviceDetails") or {}).get("healthCheckPath"),
                "url": (svc.get("serviceDetails") or {}).get("url"),
            }
            deploys = step(f"deploys[{sid}]",
                           lambda s=sid: client.list_deploys(s, limit=5))
            if deploys:
                row["recent_deploys"] = [
                    {"id": d.get("id"), "status": d.get("status"),
                     "trigger": d.get("trigger"), "createdAt": d.get("createdAt"),
                     "finishedAt": d.get("finishedAt")} for d in deploys]
            masked = step(f"env_vars[{sid}]",
                          lambda s=sid: client.list_env_vars_masked(s))
            if masked is not None:
                row["env_var_keys"] = [m["key"] for m in masked]
                row["env_var_count"] = len(masked)
                row["env_vars_with_values"] = sum(1 for m in masked if m["value_present"])
            report["services"].append(row)

    if services is not None:
        report["infrastructure"] = adapter.audit_infrastructure(services)

    report["summary"] = {
        "workspaces": len(report.get("workspaces") or []),
        "services": len(report["services"]),
        "endpoints_ok": sum(1 for e in report["endpoints"].values() if e.get("ok")),
        "endpoints_failed": sum(1 for e in report["endpoints"].values() if not e.get("ok")),
        # Phase 0 must never change anything. If this is not 0, the probe is broken.
        "mutations_performed": report["mutations_performed"],
    }

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
        print(f"wrote {args.report}")
    else:
        print(text)

    print("\nPhase 0 summary:", file=sys.stderr)
    for k, v in report["summary"].items():
        print(f"  {k}: {v}", file=sys.stderr)
    if report["summary"]["mutations_performed"]:
        print("  ⚠ THIS VIOLATES PHASE 0: a mutation was performed", file=sys.stderr)
        return 1
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
