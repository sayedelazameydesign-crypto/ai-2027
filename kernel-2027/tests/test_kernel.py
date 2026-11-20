"""Behaviour tests for the shared kernel.

Each test pins a property the *repository* depends on, not merely that a
function returns something. Where a primitive was extracted from an existing
engine, the test asserts the property that engine relied on, so a future edit
here cannot silently change an engine's behaviour.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel2027 import (
    KERNEL_VERSION,
    ApprovalRequest,
    ApprovalState,
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
    IdempotencyKey,
    KernelError,
    RetryPolicy,
    UnsafeValueError,
    audit_link,
    backoff_delay,
    canonical_json,
    clamp,
    decide,
    effect_key,
    redact,
    redact_mapping,
    require_approval,
    require_safe_id,
    sha256_file,
    sha256_text,
    stable_id,
    utc_now,
    validate_endpoint,
    verify_chain,
)
from kernel2027.audit import GENESIS


class ContractTests(unittest.TestCase):
    """Deterministic serialisation is the foundation every digest rests on."""

    def test_canonical_json_is_key_order_independent(self) -> None:
        a = {"b": 1, "a": {"z": 2, "y": [3, 1]}}
        b = {"a": {"y": [3, 1], "z": 2}, "b": 1}
        self.assertEqual(canonical_json(a), canonical_json(b))
        self.assertEqual(canonical_json(a), '{"a":{"y":[3,1],"z":2},"b":1}')

    def test_canonical_json_rejects_nothing_silently(self) -> None:
        with self.assertRaises(TypeError):
            canonical_json({"a": object()})

    def test_sha256_text_matches_known_vector(self) -> None:
        self.assertEqual(
            sha256_text("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_sha256_file_streams_large_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob.bin"
            path.write_bytes(b"x" * (3 << 20))
            self.assertEqual(sha256_file(str(path)), sha256_text("x" * (3 << 20)))

    def test_utc_alias_is_portable_to_python_310(self) -> None:
        """`from datetime import UTC` is 3.11+ and would break the 3.10 CI job.

        The kernel declares requires-python >= 3.10 and is now imported by
        engines that CI exercises on 3.10, so this is a compatibility contract,
        not a style preference.
        """
        from datetime import timezone

        from kernel2027 import contracts

        self.assertIs(contracts.UTC, timezone.utc)
        source = Path(contracts.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from datetime import UTC", source)

    def test_utc_now_shape(self) -> None:
        stamp = utc_now()
        self.assertTrue(stamp.endswith("Z"))
        self.assertEqual(len(stamp), len("2027-01-01T00:00:00.000000Z"))

    def test_require_safe_id_accepts_and_refuses(self) -> None:
        self.assertEqual(require_safe_id("task-1.2:x", where="t"), "task-1.2:x")
        for bad in ("../etc/passwd", "", "a/b", "-lead", "x" * 200):
            with self.assertRaises(UnsafeValueError, msg=bad):
                require_safe_id(bad, where="t")

    def test_stable_id_is_order_independent_and_stable(self) -> None:
        self.assertEqual(stable_id("a", {"x": 1, "y": 2}), stable_id("a", {"y": 2, "x": 1}))
        self.assertNotEqual(stable_id("a", {"x": 1}), stable_id("b", {"x": 1}))
        self.assertEqual(len(stable_id("a", prefix="id-", length=8)), len("id-") + 8)

    def test_clamp_bounds_and_inversions(self) -> None:
        self.assertEqual(clamp(5, 0, 3), 3)
        self.assertEqual(clamp(-1, 0, 3), 0)
        with self.assertRaises(KernelError):
            clamp(1, 5, 0)


class RedactionTests(unittest.TestCase):
    """Redaction is the one primitive where a miss is a leaked credential."""

    def test_every_provider_key_shape_is_masked(self) -> None:
        body = " ".join(
            [
                "AIza" + "a" * 30,
                "nvapi-" + "b" * 30,
                "sk-proj-" + "c" * 30,
                "gsk_" + "d" * 30,
                "e2b_" + "e" * 30,
                "ghp_" + "f" * 30,
                "AKIA" + "G" * 16,
            ]
        )
        cleaned = redact(body)
        self.assertNotIn("AIza", cleaned)
        self.assertNotIn("nvapi", cleaned)
        self.assertNotIn("sk-proj", cleaned)
        self.assertNotIn("gsk_", cleaned)
        self.assertNotIn("e2b_", cleaned)
        self.assertNotIn("ghp_", cleaned)
        self.assertNotIn("AKIA", cleaned)

    def test_literal_secrets_are_masked_even_without_a_recognisable_shape(self) -> None:
        self.assertNotIn("hunter2hunter2", redact("token=hunter2hunter2", ["hunter2hunter2"]))

    def test_short_literals_are_left_alone(self) -> None:
        # Redacting a 3-character string would mangle every log line.
        self.assertIn("abc", redact("abc def", ["abc"]))

    def test_query_parameter_values_are_masked_but_key_kept(self) -> None:
        cleaned = redact("https://x.dev/v1?key=SUPERSECRETVALUE&other=1")
        self.assertIn("key=", cleaned)
        self.assertNotIn("SUPERSECRETVALUE", cleaned)

    def test_mapping_masks_by_key_name_even_with_a_harmless_value(self) -> None:
        out = redact_mapping({"api_key": "plain", "nested": {"Authorization": "x"}, "ok": "fine"})
        self.assertEqual(out["api_key"], "[REDACTED]")
        self.assertEqual(out["nested"]["Authorization"], "[REDACTED]")
        self.assertEqual(out["ok"], "fine")

    def test_mapping_handles_lists_without_crashing(self) -> None:
        out = redact_mapping({"items": [{"token": "abc"}, "sk-" + "z" * 30]})
        self.assertEqual(out["items"][0]["token"], "[REDACTED]")
        self.assertNotIn("sk-", out["items"][1])

    def test_non_string_passes_through(self) -> None:
        self.assertEqual(redact(None), None)  # type: ignore[arg-type]


class RetryTests(unittest.TestCase):
    """Retries must be bounded, jittered, and must not hide the real error."""

    def test_delay_grows_then_caps(self) -> None:
        policy = RetryPolicy(max_attempts=10, base_delay_s=1.0, max_delay_s=4.0, jitter=0.0)
        self.assertEqual(backoff_delay(1, policy, rng=lambda: 0.0), 1.0)
        self.assertEqual(backoff_delay(2, policy, rng=lambda: 0.0), 2.0)
        self.assertEqual(backoff_delay(3, policy, rng=lambda: 0.0), 4.0)
        self.assertEqual(backoff_delay(9, policy, rng=lambda: 0.0), 4.0)

    def test_jitter_stays_within_bounds(self) -> None:
        policy = RetryPolicy(base_delay_s=1.0, max_delay_s=1.0, jitter=0.5)
        for sample in (0.0, 0.5, 1.0):
            delay = backoff_delay(1, policy, rng=lambda s=sample: s)
            self.assertGreaterEqual(delay, 0.5)
            self.assertLessEqual(delay, 1.0)

    def test_success_returns_value_and_counts_attempts(self) -> None:
        attempts: list[int] = []

        def flaky() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise TimeoutError("boom")
            return "ok"

        from kernel2027 import with_retry

        result = with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=5, base_delay_s=0.0),
            sleep=lambda _: None,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 3)

    def test_exhaustion_reraises_the_real_cause(self) -> None:
        from kernel2027 import with_retry

        def always() -> None:
            raise TimeoutError("provider said 429")

        with self.assertRaises(TimeoutError):
            with_retry(always, policy=RetryPolicy(max_attempts=2), sleep=lambda _: None)

    def test_non_retryable_fails_fast(self) -> None:
        from kernel2027 import with_retry

        calls: list[int] = []

        def fatal() -> None:
            calls.append(1)
            raise ValueError("bad request")

        with self.assertRaises(ValueError):
            with_retry(
                fatal,
                policy=RetryPolicy(max_attempts=5),
                is_retryable=lambda exc: not isinstance(exc, ValueError),
                sleep=lambda _: None,
            )
        self.assertEqual(len(calls), 1)

    def test_policy_refuses_impossible_values(self) -> None:
        with self.assertRaises(KernelError):
            RetryPolicy(max_attempts=0)
        with self.assertRaises(KernelError):
            RetryPolicy(jitter=2.0)


class CircuitTests(unittest.TestCase):
    """An open breaker must refuse work; a cooled one must retry exactly once."""

    def test_opens_at_threshold_and_refuses(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2, cooldown_s=30)
        breaker.record_failure(now=100.0)
        self.assertTrue(breaker.is_available(100.0))
        breaker.record_failure(now=100.0)
        self.assertFalse(breaker.is_available(101.0))
        self.assertEqual(breaker.state, "open")

    def test_half_open_after_cooldown_then_closes_on_success(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, cooldown_s=10)
        breaker.record_failure(now=100.0)
        self.assertEqual(breaker.breaker_state, BreakerState.OPEN)
        self.assertTrue(breaker.is_available(111.0))
        self.assertEqual(breaker.breaker_state, BreakerState.HALF_OPEN)
        breaker.record_success()
        self.assertEqual(breaker.breaker_state, BreakerState.CLOSED)

    def test_call_raises_while_open_and_records_failures(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, cooldown_s=1000)
        with self.assertRaises(ValueError):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("x")))
        with self.assertRaises(CircuitOpenError):
            breaker.call(lambda: "never")

    def test_success_resets_the_failure_count(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3, cooldown_s=10)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        self.assertEqual(breaker.failures, 0)
        breaker.record_failure()
        self.assertEqual(breaker.state, "closed")

    def test_pool_keeps_breakers_per_target(self) -> None:
        from kernel2027.circuit import BreakerPool

        pool = BreakerPool(failure_threshold=1)
        pool.for_target("a").record_failure()
        self.assertFalse(pool.for_target("a").is_available())
        self.assertTrue(pool.for_target("b").is_available())
        self.assertIn("a", pool.snapshot())


class ApprovalTests(unittest.TestCase):
    """Approval is a security boundary: silence must never mean yes."""

    def test_default_is_pending_and_not_approved(self) -> None:
        decision = decide(ApprovalRequest(action="deploy"))
        self.assertEqual(decision.state, ApprovalState.PENDING)
        self.assertFalse(decision.approved)

    def test_allow_list_auto_approves(self) -> None:
        decision = decide(
            ApprovalRequest(action="read_status", risk="low"),
            auto_allow=frozenset({"read_status"}),
        )
        self.assertTrue(decision.approved)

    def test_critical_can_never_be_auto_approved(self) -> None:
        decision = decide(
            ApprovalRequest(action="wipe", risk="critical"),
            auto_allow=frozenset({"wipe"}),
        )
        self.assertFalse(decision.approved)
        self.assertIn("human", decision.note)

    def test_explicit_denial_beats_everything(self) -> None:
        decision = decide(
            ApprovalRequest(action="read_status", risk="low"),
            auto_allow=frozenset({"read_status"}),
            granted=False,
        )
        self.assertFalse(decision.approved)
        self.assertEqual(decision.state, ApprovalState.DENIED)

    def test_require_approval_raises_unless_approved(self) -> None:
        with self.assertRaises(PermissionError):
            require_approval(decide(ApprovalRequest(action="deploy")), action="deploy")

    def test_blank_action_and_bad_risk_are_refused(self) -> None:
        with self.assertRaises(KernelError):
            ApprovalRequest(action="   ")
        with self.assertRaises(KernelError):
            ApprovalRequest(action="x", risk="catastrophic")

    def test_decision_is_serialisable(self) -> None:
        payload = decide(ApprovalRequest(action="a")).to_dict()
        self.assertEqual(json.loads(json.dumps(payload))["approved"], False)


class IdempotencyTests(unittest.TestCase):
    """A key that differs between writer and checker protects nothing."""

    def test_same_inputs_same_key(self) -> None:
        a = effect_key(namespace="fleet", operation="charge", scope={"step": 1, "user": "u"})
        b = effect_key(namespace="fleet", operation="charge", scope={"user": "u", "step": 1})
        self.assertEqual(a.value, b.value)

    def test_different_scope_different_key(self) -> None:
        a = effect_key(namespace="fleet", operation="charge", scope={"step": 1})
        b = effect_key(namespace="fleet", operation="charge", scope={"step": 2})
        self.assertNotEqual(a.value, b.value)

    def test_namespace_is_part_of_the_key(self) -> None:
        a = effect_key(namespace="a", operation="op")
        b = effect_key(namespace="b", operation="op")
        self.assertNotEqual(a.value, b.value)
        self.assertTrue(a.value.startswith("a:"))

    def test_blank_inputs_refused(self) -> None:
        with self.assertRaises(KernelError):
            effect_key(namespace="", operation="op")
        with self.assertRaises(KernelError):
            IdempotencyKey(value="  ")


class AuditTests(unittest.TestCase):
    """Tamper-evidence is only meaningful if verification names the broken row."""

    def _chain(self, count: int) -> list[dict]:
        rows: list[dict] = []
        previous = GENESIS
        for index in range(count):
            body, digest = audit_link(
                previous_sha=previous,
                kind="step",
                payload={"index": index},
                at=f"2027-01-01T00:00:0{index}.000000Z",
                task_id="t1",
            )
            rows.append({"prev_sha": previous, "payload_json": body, "sha256": digest})
            previous = digest
        return rows

    def test_valid_chain_verifies(self) -> None:
        ok, checked, problem = verify_chain(self._chain(5))
        self.assertTrue(ok)
        self.assertEqual(checked, 5)
        self.assertIsNone(problem)

    def test_edited_row_is_caught_with_its_position(self) -> None:
        rows = self._chain(4)
        rows[2]["payload_json"] = rows[2]["payload_json"].replace('"index":2', '"index":99')
        ok, _checked, problem = verify_chain(rows)
        self.assertFalse(ok)
        self.assertIn("row 3", problem)

    def test_removed_row_breaks_the_chain(self) -> None:
        rows = self._chain(4)
        del rows[1]
        ok, checked, problem = verify_chain(rows)
        self.assertFalse(ok)
        self.assertEqual(checked, 1)  # only the row before the gap was verified
        self.assertIn("prev_sha", problem)

    def test_chain_is_reproducible(self) -> None:
        self.assertEqual(self._chain(3), self._chain(3))

    def test_short_previous_digest_refused(self) -> None:
        with self.assertRaises(ValueError):
            audit_link(previous_sha="abc", kind="k", payload={}, at="t")


class EndpointTests(unittest.TestCase):
    """SSRF is the failure mode this rule exists to prevent."""

    def test_https_public_allowed(self) -> None:
        self.assertEqual(
            validate_endpoint("https://api.example.com/v1/"), "https://api.example.com/v1"
        )

    def test_loopback_http_allowed_by_default(self) -> None:
        self.assertTrue(validate_endpoint("http://127.0.0.1:11434").startswith("http://"))

    def test_loopback_http_refused_when_disabled(self) -> None:
        with self.assertRaises(UnsafeValueError):
            validate_endpoint("http://127.0.0.1:11434", allow_loopback_http=False)

    def test_plain_http_public_refused(self) -> None:
        with self.assertRaises(UnsafeValueError):
            validate_endpoint("http://example.com")

    def test_private_link_local_refused(self) -> None:
        with self.assertRaises(UnsafeValueError):
            validate_endpoint("https://10.0.0.5/x")
        with self.assertRaises(UnsafeValueError):
            validate_endpoint("https://169.254.169.254/latest/meta-data")

    def test_credentials_and_query_refused(self) -> None:
        with self.assertRaises(UnsafeValueError):
            validate_endpoint("https://user:pass@example.com")
        with self.assertRaises(UnsafeValueError):
            validate_endpoint("https://example.com/v1?key=abc")

    def test_empty_and_bad_scheme_refused(self) -> None:
        for bad in ("", "   ", "ftp://example.com", "not a url"):
            with self.assertRaises(UnsafeValueError, msg=bad):
                validate_endpoint(bad)


class PackageShapeTests(unittest.TestCase):
    """The kernel's constraints are part of its contract with the repository."""

    def test_version_is_present(self) -> None:
        self.assertRegex(KERNEL_VERSION, r"^\d+\.\d+\.\d+$")

    def test_kernel_imports_nothing_from_an_engine(self) -> None:
        """The dependency arrow must point one way: engines -> kernel only."""
        forbidden = (
            "fleet2027", "ai2027_protocol", "connectors_hub", "render_control",
            "browser_skill", "command_hud", "memory2027",
        )
        root = Path(__file__).resolve().parent.parent / "kernel2027"
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for name in forbidden:
                self.assertNotIn(f"import {name}", source, msg=f"{path.name} imports {name}")

    def test_kernel_is_stdlib_only(self) -> None:
        """No third-party import may creep in: that is what makes it shareable."""
        import ast

        allowed = {
            "__future__", "ast", "collections", "dataclasses", "datetime", "enum",
            "hashlib", "ipaddress", "json", "random", "re", "time", "typing",
            "unicodedata", "urllib",
        }
        root = Path(__file__).resolve().parent.parent / "kernel2027"
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        self.assertIn(top, allowed, msg=f"{path.name} imports {top}")
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    top = node.module.split(".")[0]
                    self.assertTrue(
                        top in allowed or top == "kernel2027",
                        msg=f"{path.name} imports {top}",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
