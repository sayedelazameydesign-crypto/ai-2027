#!/usr/bin/env python3
"""Verify a deployed Agent Studio instance from the outside.

Checks the posture that the blueprint is supposed to guarantee, using only
the public HTTP surface. It reads no secret and prints no credential: the
instance never returns one, and this script never asks for one.

Usage:
    python scripts/verify_studio_instance.py https://<your-service>.onrender.com

Exit codes:
    0  every check passed
    1  at least one check FAILED
    2  the instance could not be reached, or its payload was not understood
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any

# A credential-shaped string must never appear in a response body.
SECRET_PATTERN = re.compile(r"(sk-|ghp_|gsk_|AIza|gsk-ant|Bearer )[A-Za-z0-9_\-]{6,}")


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.rows.append((ok, label, detail))
        return ok

    def report(self) -> int:
        width = max(len(label) for _, label, _ in self.rows)
        failures = 0
        for ok, label, detail in self.rows:
            mark = "PASS" if ok else "FAIL"
            if not ok:
                failures += 1
            suffix = f"  {detail}" if detail else ""
            print(f"  [{mark}] {label.ljust(width)}{suffix}")
        print()
        if failures:
            print(f"FAILED: {failures} of {len(self.rows)} checks did not pass.")
            return 1
        print(f"OK: all {len(self.rows)} checks passed.")
        return 0


class Response:
    """The facts about one HTTP response that are safe to report.

    The raw body is deliberately NOT retained here, and nothing printed by
    this program is derived from it. A response body is untrusted input
    that could carry a credential, so it is inspected once and reduced to
    facts that cannot leak its contents:

    * ``size_bytes`` is the length of the raw bytes read off the socket,
      measured before the body is decoded into text;
    * ``looks_like_html`` is a boolean, never a substring;
    * ``contains_credential`` is a boolean;
    * ``json`` is the parsed structure, which the caller reads field by
      field rather than printing.
    """

    def __init__(
        self, status: int, body: str, headers: dict[str, str], size_bytes: int
    ) -> None:
        self.status = status
        self.headers = headers
        self.size_bytes = size_bytes
        self.looks_like_html = "<html" in body.lower() and size_bytes > 1000
        self.contains_credential = bool(SECRET_PATTERN.search(body))
        # Only a JSON body is parsed, and only into a structure the caller
        # reads field by field. A non-JSON body leaves this as None.
        self.json: Any = None
        stripped = body.strip()
        if stripped.startswith("{"):
            try:
                self.json = json.loads(stripped)
            except json.JSONDecodeError:
                self.json = None


def fetch(url: str, timeout: int = 90) -> Response:
    """GET a URL and reduce it to the facts that are safe to report.

    The timeout is generous on purpose: a free Render instance sleeps after
    roughly 15 minutes of inactivity, so the first request of a session can
    take 30-50 seconds to wake it. That is the sleep, not a failure.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": "ai-2027-studio-verify/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            body = raw.decode("utf-8", errors="replace")
            return Response(response.status, body, dict(response.headers), len(raw))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = raw.decode("utf-8", errors="replace")
        return Response(exc.code, body, dict(exc.headers or {}), len(raw))
    except Exception as exc:  # the message is the useful part
        raise SystemExit(
            f"could not reach {url}: {exc}\n"
            "If this is the first request after an idle period, a free instance "
            "takes 30-50 seconds to wake. Retry once before treating it as a fault."
        ) from exc


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    base = argv[1].rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}"

    result = Result()
    print(f"Verifying {base}\n")

    # ---- the UI ----------------------------------------------------------
    ui = fetch(f"{base}/studio")
    result.check(ui.status == 200, "GET /studio returns 200", f"got {ui.status}")
    result.check(
        ui.looks_like_html,
        "GET /studio serves a real HTML page",
        f"{ui.size_bytes} bytes",
    )
    # The UI must not ship a credential to the browser.
    result.check(
        not ui.contains_credential,
        "GET /studio contains no credential-shaped text",
    )

    # ---- the status API --------------------------------------------------
    api = fetch(f"{base}/v1/studio/status")
    result.check(
        api.status == 200, "GET /v1/studio/status returns 200", f"got {api.status}"
    )

    payload = api.json
    if not isinstance(payload, dict):
        print("  [FAIL] the status route did not return a JSON object")
        return 2

    result.check(
        not api.contains_credential,
        "status payload contains no credential-shaped text",
    )

    # ---- same-origin, not CORS-open --------------------------------------
    cors = [
        k for k in api.headers if k.lower().startswith("access-control-allow-origin")
    ]
    result.check(not cors, "no Access-Control-Allow-Origin header", ", ".join(cors))

    # ---- the safety posture ---------------------------------------------
    result.check(
        payload.get("read_only") is True,
        "read_only is true",
        str(payload.get("read_only")),
    )
    result.check(
        payload.get("cost_usd") == 0.0, "cost_usd is 0.0", str(payload.get("cost_usd"))
    )

    budget = payload.get("budget") or {}
    result.check(
        budget.get("max_spend_usd") == 0.0,
        "max_spend_usd is 0.0",
        str(budget.get("max_spend_usd")),
    )
    result.check(
        budget.get("spent_usd") == 0.0, "spent_usd is 0.0", str(budget.get("spent_usd"))
    )
    result.check(
        budget.get("api_key_never_implies_free") is True,
        "api_key_never_implies_free is true",
    )

    # ---- provider presence ------------------------------------------------
    providers = {p.get("provider"): p for p in (payload.get("providers") or [])}
    openrouter = providers.get("openrouter")
    if openrouter is None:
        result.check(False, "the openrouter provider is present in the payload")
    else:
        result.check(
            openrouter.get("billing_tier") == "free_tier",
            "openrouter is classified free_tier",
            str(openrouter.get("billing_tier")),
        )
        result.check(
            openrouter.get("allowed_under_zero_budget") is True,
            "openrouter is allowed under a zero budget",
        )
        model = str(openrouter.get("model") or "")
        # The model id is reported, but only after it has been checked
        # against the credential shape: if a payload ever carried a
        # credential in that field, it must not be printed back out.
        model_is_safe = not SECRET_PATTERN.search(model)
        result.check(
            model_is_safe and (model == "openrouter/free" or model.endswith(":free")),
            "the openrouter model id is a free one",
            model if model_is_safe else "<redacted>",
        )
        # configured is a boolean; the value itself must not be present.
        result.check(
            isinstance(openrouter.get("configured"), bool),
            "openrouter reports credential presence as a boolean",
            str(openrouter.get("configured")),
        )
        result.check(
            openrouter.get("credential_state") in {"configured", "absent"},
            "openrouter credential_state is configured or absent",
            str(openrouter.get("credential_state")),
        )

    # ---- connectors: an explicit state, never a 500 ------------------------
    connectors = payload.get("connectors")
    result.check(isinstance(connectors, list), "the connectors block is present")
    if isinstance(connectors, list):
        bad = [
            c.get("connector_id")
            for c in connectors
            if not isinstance(c.get("configured"), bool)
        ]
        result.check(
            not bad, "every connector reports a boolean configured flag", str(bad)
        )
        states = {c.get("credential_state") for c in connectors}
        result.check(
            states <= {"configured", "absent"},
            "every connector state is configured or absent",
            str(sorted(s for s in states if s)),
        )

    return result.report()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
