#!/usr/bin/env python3
"""Tests for the network preflight — blindness must never be published as death.

No test in this file touches the network: every probe is driven by an injected
opener, so the suite is deterministic inside a closed sandbox and inside CI.

Run: python3 scripts/test_network_preflight.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import network_preflight as npf  # noqa: E402


class _Resp:
    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_where(ok_hosts: set[str], status: int = 200, http_errors: dict[str, int] | None = None):
    """Return a fake urlopen: hosts in `ok_hosts` answer, everything else fails."""
    http_errors = http_errors or {}

    def _open(req, timeout=None):
        host = (npf._host(req.full_url))
        if host in http_errors:
            raise urllib.error.HTTPError(req.full_url, http_errors[host], "err", {}, None)
        if host in ok_hosts:
            return _Resp(status)
        raise urllib.error.URLError("connection refused (simulated)")

    return _open


CANARY_HOSTS = {"example.com", "www.wikipedia.org", "www.cloudflare.com"}


class ClassificationTests(unittest.TestCase):
    def test_01_two_open_canaries_mean_general_egress(self):
        rep = npf.preflight(opener=opener_where({"example.com", "www.wikipedia.org",
                                                "api.github.com"}))
        self.assertEqual(rep.egress, npf.OPEN)
        self.assertTrue(rep.general_egress)
        self.assertTrue(rep.allows("https://elazamet1.vercel.app/"))
        self.assertEqual(rep.verdict_for_failure("https://elazamet1.vercel.app/"), "dead")

    def test_02_allowlist_only_is_not_general_egress(self):
        """The 2026-11-18 sandbox: github answers, the public web does not."""
        rep = npf.preflight(opener=opener_where({"api.github.com", "github.com"}))
        self.assertEqual(rep.egress, npf.ALLOWLISTED_ONLY)
        self.assertFalse(rep.allows("https://elazamet1.vercel.app/"))
        self.assertTrue(rep.allows("https://api.github.com/repos/x/y"))
        self.assertEqual(rep.verdict_for_failure("https://elazamet1.vercel.app/"), "blocked")

    def test_03_closed_environment_blocks_every_judgement(self):
        rep = npf.preflight(opener=opener_where(set()))
        self.assertEqual(rep.egress, npf.CLOSED)
        self.assertFalse(rep.allows("https://api.github.com/"))
        self.assertEqual(rep.verdict_for_failure("https://example.com/"), "blocked")

    def test_04_a_single_canary_is_not_enough_for_general_egress(self):
        """One lucky host must not license verdicts about the whole internet."""
        rep = npf.preflight(opener=opener_where({"example.com", "api.github.com"}))
        self.assertEqual(rep.egress, npf.ALLOWLISTED_ONLY)
        self.assertFalse(rep.allows("https://cela-umber.vercel.app/"))

    def test_05_http_error_status_still_proves_reachability(self):
        """404 means a server answered: the site is dead, the network is not."""
        rep = npf.preflight(opener=opener_where(CANARY_HOSTS | {"api.github.com"},
                                                http_errors={}))
        self.assertEqual(rep.egress, npf.OPEN)
        dead_site = npf.probe("https://dead.example.org/",
                              opener=opener_where(set(), http_errors={"dead.example.org": 404}))
        self.assertTrue(dead_site.reachable)
        self.assertEqual(dead_site.status, 404)
        self.assertEqual(rep.verdict_for_failure("https://dead.example.org/"), "dead")

    def test_06_timeout_is_reported_as_unreachable_not_as_dead(self):
        import socket

        def _timeout(req, timeout=None):
            raise urllib.error.URLError(socket.timeout("timed out"))

        p = npf.probe("https://slow.example.org/", opener=_timeout)
        self.assertFalse(p.reachable)
        self.assertIsNone(p.status)
        self.assertIn("timed out", p.error)


class ReportShapeTests(unittest.TestCase):
    def test_10_report_is_json_serialisable_and_self_describing(self):
        rep = npf.preflight(opener=opener_where({"api.github.com"}))
        payload = json.loads(json.dumps(rep.to_dict()))
        self.assertEqual(payload["egress"], npf.ALLOWLISTED_ONLY)
        self.assertIs(payload["can_judge_public_urls"], False)
        self.assertTrue(all("reachable" in p for p in payload["probes"]))
        self.assertTrue(any(p["allowlisted"] for p in payload["probes"]))

    def test_11_allowlist_reference_is_always_measured(self):
        rep = npf.preflight(urls=["https://example.com/"],
                            opener=opener_where({"api.github.com"}))
        hosts = [p.host for p in rep.probes]
        self.assertIn("api.github.com", hosts)

    def test_12_preflight_is_memoised_once_per_process(self):
        calls = {"n": 0}

        def counting(req, timeout=None):
            calls["n"] += 1
            if npf._host(req.full_url) == "api.github.com":
                return _Resp(200)
            raise urllib.error.URLError("closed")

        npf.reset_cache()
        a = npf.cached_preflight(opener=counting)
        b = npf.cached_preflight(opener=counting)
        self.assertIs(a, b)
        self.assertEqual(calls["n"], len(npf.CANARIES) + 1)  # one pass, not two
        npf.reset_cache()
        npf.cached_preflight(opener=counting)
        self.assertEqual(calls["n"], 2 * (len(npf.CANARIES) + 1))


class CliTests(unittest.TestCase):
    def test_20_require_open_fails_when_egress_is_not_general(self):
        original = npf.preflight
        npf.preflight = lambda *a, **k: npf.Report(npf.ALLOWLISTED_ONLY, ())
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(npf.main(["--require-open"]), 1)
            self.assertIn("BLOCKED, not dead", out.getvalue())
            npf.preflight = lambda *a, **k: npf.Report(npf.OPEN, ())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(npf.main(["--require-open"]), 0)
        finally:
            npf.preflight = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
