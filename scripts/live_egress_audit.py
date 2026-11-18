#!/usr/bin/env python3
"""Re-measure every public URL this repository makes a claim about.

The repository claims four sites are live and three are dead
(`docs/GROUND-TRUTH-2026-11-17.md` §3). Those claims decay silently: a Vercel
deployment is deleted, a Render service stops answering — and the README goes on
saying "live".

This script turns those claims into a register (`docs/public-urls.json`) and
re-measures it, but only where measurement is meaningful:

* `scripts/network_preflight.py` must report `OPEN` egress first. In a sandbox
  limited to github/pypi/npm every URL "fails", and reporting that as *dead*
  would be a lie produced by the environment (measured 2026-11-18).
* A URL that cannot be reached **because we are blind** is `blocked`.
  A URL that answers with an error status **while we can see the internet** is
  `dead` — the network worked, the site did not.

Verdicts (measured, compared against the recorded claim):

    ok          measured state matches the register
    regression  register says live, the measurement says not
    recovered   register says dead, the site answers 2xx (update the register)
    blocked     cannot be judged from here (no general egress)

    python3 scripts/live_egress_audit.py                 # human table
    python3 scripts/live_egress_audit.py --json          # machine
    python3 scripts/live_egress_audit.py --markdown      # job summary
    python3 scripts/live_egress_audit.py --strict-exit   # exit 1 on regression

Exit codes: 0 measured (no regression) · 1 regression · 2 cannot measure at all.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "docs" / "public-urls.json"
sys.path.insert(0, str(ROOT / "scripts"))
import network_preflight  # noqa: E402  (same-directory helper, stdlib only)

OK, REGRESSION, RECOVERED, BLOCKED = "ok", "regression", "recovered", "blocked"
LIVE_CODES = range(200, 300)


@dataclass(frozen=True)
class Measurement:
    url: str
    what: str
    expected: str            # "live" | "dead" — the recorded claim
    verdict: str             # ok | regression | recovered | blocked
    code: int | None
    ms: float
    error: str | None = None

    def to_dict(self) -> dict:
        return {"url": self.url, "what": self.what, "expected": self.expected,
                "verdict": self.verdict, "code": self.code,
                "ms": round(self.ms, 1), "error": self.error}

    def as_row(self) -> str:
        code = "—" if self.code is None else str(self.code)
        return f"{self.verdict:<10} {code:>4}  {self.url}"


def load_register(path: Path = REGISTER) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("urls"), list):
        raise ValueError(f"{path} has no 'urls' list")
    return data


def classify(expected: str, reachable: bool, code: int | None, blind: bool) -> str:
    """One URL's verdict. `blind` means preflight proved we cannot judge."""
    if blind or not reachable:
        return BLOCKED if blind else ("ok" if expected == "dead" else REGRESSION)
    live_now = code is not None and code in LIVE_CODES
    if expected == "live":
        return OK if live_now else REGRESSION
    return RECOVERED if live_now else OK


def probe(url: str, timeout: float = 15.0, opener=None) -> tuple[bool, int | None, float, str | None]:
    """(reachable, status, ms, error). Any HTTP status proves the host answered."""
    import time

    open_fn = opener or urllib.request.urlopen
    started = time.time()
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "ai-2027-live-egress/1"})
    try:
        with open_fn(req, timeout=timeout) as resp:
            return True, getattr(resp, "status", None), (time.time() - started) * 1000, None
    except urllib.error.HTTPError as exc:
        return True, int(exc.code), (time.time() - started) * 1000, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, None, (time.time() - started) * 1000, str(reason)[:120]


def audit(register: dict, report: network_preflight.Report, probe_fn=None,
          timeout: float = 15.0) -> dict:
    probe_fn = probe_fn or probe          # late binding so tests can inject one
    blind = not report.general_egress
    rows: list[Measurement] = []
    for entry in register["urls"]:
        url = entry["url"]
        if blind:
            rows.append(Measurement(url, entry.get("what", ""), entry.get("expected", "live"),
                                    BLOCKED, None, 0.0, f"no general egress ({report.egress})"))
            continue
        reachable, code, ms, error = probe_fn(url, timeout=timeout)
        rows.append(Measurement(url, entry.get("what", ""), entry.get("expected", "live"),
                                classify(entry.get("expected", "live"), reachable, code, blind),
                                code, ms, error))
    counts = {k: sum(1 for r in rows if r.verdict == k)
              for k in (OK, REGRESSION, RECOVERED, BLOCKED)}
    return {"egress": report.to_dict(), "measured_at": _now(),
            "counts": counts, "regressions": [r.to_dict() for r in rows
                                              if r.verdict == REGRESSION],
            "results": [r.to_dict() for r in rows],
            "_rows": rows}


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def to_markdown(result: dict) -> str:
    eg = result["egress"]
    counts = result["counts"]
    lines = [
        "# 🌐 Live egress audit",
        "",
        f"**Measured:** {result['measured_at']} · **Egress:** `{eg.get('egress')}` · "
        f"**Can judge public URLs:** `{eg.get('general_egress')}`",
        "",
        "| Verdict | Count |",
        "|---|---:|",
        f"| ok (claim holds) | {counts[OK]} |",
        f"| regression (claim is now false) | {counts[REGRESSION]} |",
        f"| recovered (register is stale) | {counts[RECOVERED]} |",
        f"| blocked (cannot judge here) | {counts[BLOCKED]} |",
        "",
        "| Verdict | Code | URL | Claimed | Error |",
        "|---|---:|---|---|---|",
    ]
    for r in result["results"]:
        code = "—" if r["code"] is None else r["code"]
        lines.append(f"| {r['verdict']} | {code} | <{r['url']}> | {r['expected']} | "
                     f"{r['error'] or ''} |")
    if counts[BLOCKED]:
        lines += ["", "> ⚠️ `blocked` rows were **not judged**: this runner has no general "
                      "egress. They are not dead sites, and this report does not claim they are."]
    if counts[REGRESSION]:
        lines += ["", "> 🔴 **A claim in the README/docs is now false.** Either the deployment "
                      "is gone (fix the claim) or the service is down (fix the service)."]
    lines += ["", "*Register: `docs/public-urls.json` · measurer: `scripts/live_egress_audit.py`.*"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Re-measure public URL claims.")
    ap.add_argument("--register", type=Path, default=REGISTER)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--write-json", type=Path)
    ap.add_argument("--write-markdown", type=Path)
    ap.add_argument("--strict-exit", action="store_true",
                    help="exit 1 when a live claim regressed")
    ap.add_argument("--assume-egress", choices=("OPEN", "ALLOWLISTED_ONLY", "CLOSED"),
                    help="skip the preflight and assume this egress class (offline tests)")
    args = ap.parse_args(argv)

    register = load_register(args.register)
    if args.assume_egress:
        report = network_preflight.Report(args.assume_egress, ())
    else:
        report = network_preflight.preflight(timeout=min(6.0, args.timeout))

    result = audit(register, report, timeout=args.timeout)
    rows = result.pop("_rows")

    if args.write_json:
        args.write_json.parent.mkdir(parents=True, exist_ok=True)
        args.write_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    if args.write_markdown:
        args.write_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.write_markdown.write_text(to_markdown(result), encoding="utf-8")

    if args.markdown:
        print(to_markdown(result))
    elif args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("LIVE EGRESS AUDIT — are the public claims still true?")
        print("=" * 70)
        print(f"  egress: {report.egress} · can judge public URLs: {report.general_egress}")
        for row in rows:
            print(f"  {row.as_row()}")
        c = result["counts"]
        print("-" * 70)
        print(f"  ok {c[OK]} · regression {c[REGRESSION]} · recovered {c[RECOVERED]} "
              f"· blocked {c[BLOCKED]}")
        if c[BLOCKED]:
            print("  ! blocked rows were NOT judged — no general egress in this environment.")

    if args.strict_exit and result["counts"][REGRESSION]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
