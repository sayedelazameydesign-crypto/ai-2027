"""Governed WRITE executor — the approval-to-write contract (fail-closed).

Closes the P1 loop: Watcher -> Approval Bot -> **GovernedExecutor** -> audit
ledger. Before any WRITE tool runs, four gates must pass (AD-004 deny by
default):

1. **Approval gate** — the ``pa_approvals`` row exists, is not deleted, has an
   explicit approval status (``granted`` / ``approved``), was never consumed,
   its ``action`` matches, its ``payload_hash`` is present and equals
   ``compute_sha256(payload)`` (anti-tamper), and ``expires_at`` is in the
   future.
2. **Delegation gate** — an active, unrevoked ``pa_delegation_grants`` row
   allows the action; ``denied_actions`` wins over ``allowed_actions``; and
   ``limits`` (``max_per_day``) can be parsed and verified against today's
   executor audit rows. Unparseable or unenforceable limits deny.
3. **Consume gate** — the approval row flips to ``consumed`` (version bump +
   checksum recompute) *before* the tool runs, so a failed tool cannot retry
   on the same approval (single-use).
4. **Dispatch gate** — the registered handler runs; both the allow-decision
   and the tool outcome are appended to ``pa_audit_logs_202611`` through the
   watcher's chained ``append_audit_log`` (``prev_hash -> self_hash``).

Design notes
------------
- Pure stdlib with two injected collaborators (``spreadsheet``,
  ``audit_logger``): no gspread import, so CI can run the contract bare.
  Runtime binding of the chained audit logger is shown in
  ``scripts/live_smoke_runner.py``.
- ``approval_checksum`` mirrors ``approval_bot.approval_checksum`` and
  ``compute_sha256`` mirrors the watcher's; ``tests/test_governed_executor.py``
  asserts both equivalences so the copies cannot drift.
- Column indexes for ``pa_delegation_grants`` (A:W) were verified against the
  live sheet: O=scope, P=status, R=allowed_actions, S=denied_actions,
  T=limits, W=revoked_at.
- Known limits (documented, not silent): a truly atomic consume is impossible
  across processes on Google Sheets — best-effort single-writer; the
  live-agent fencing token remains the authority for concurrent writers. The
  ``consent`` column is data-processing consent, not action authorization;
  authorization = approval status + delegation grant. Seeded approval rows
  carry an unknown historical ``payload_hash`` canonicalisation — they deny
  (``payload_hash_mismatch``) until re-issued through this module's
  ``compute_sha256``.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _load_sibling(name: str) -> Any:
    """Import a stdlib sibling module from this directory by path."""
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ed25519 = _load_sibling("ed25519")


class ExecutionDeniedError(PermissionError):
    """Raised when a governance gate fails (fail-closed)."""


@dataclass
class ExecutionResult:
    ok: bool
    approval_id: str
    action: str
    outcome: str
    reason: str
    output: Any = None


# pa_approvals header — 22 columns A:V (verified against the live sheet).
APPROVAL_HEADER = [
    "id", "created_at", "updated_at", "version", "source_id", "confidence",
    "epistemic", "sensitivity", "consent", "retention", "review_at",
    "deleted_at", "taint", "checksum", "action", "payload_hash", "summary",
    "status", "issued_at", "expires_at", "consumed_at", "single_use",
]

# pa_delegation_grants header — 23 columns A:W (verified against the live
# sheet 2026-11-26): O=scope, P=status, R=allowed, S=denied, T=limits,
# W=revoked_at.
GRANT_HEADER = [
    "id", "created_at", "updated_at", "version", "source_id", "confidence",
    "epistemic", "sensitivity", "consent", "retention", "review_at",
    "deleted_at", "taint", "checksum", "scope", "status", "model_plan_ref",
    "allowed_actions", "denied_actions", "limits", "requires_approval",
    "granted_by", "revoked_at",
]

# Positional indexes into an approvals row (A:V).
COL_APPROVAL_UPDATED_AT = 2
COL_APPROVAL_VERSION = 3
COL_APPROVAL_DELETED_AT = 11
COL_APPROVAL_CHECKSUM = 13
COL_APPROVAL_ACTION = 14
COL_APPROVAL_PAYLOAD_HASH = 15
COL_APPROVAL_STATUS = 17
COL_APPROVAL_EXPIRES_AT = 19
COL_APPROVAL_CONSUMED_AT = 20

# Positional indexes into a grants row (A:W).
COL_GRANT_STATUS = 15
COL_GRANT_ALLOWED = 17
COL_GRANT_DENIED = 18
COL_GRANT_LIMITS = 19
COL_GRANT_REVOKED_AT = 22

# The 12 fields covered by the approval-row checksum — must stay identical to
# approval_bot.approval_checksum (equivalence asserted in tests).
CHECKSUM_FIELDS = (
    "id", "created_at", "updated_at", "version", "action", "payload_hash",
    "summary", "status", "issued_at", "expires_at", "consumed_at",
    "single_use",
)

# Audit-sheet positions used by the max_per_day limiter (A:Y).
COL_AUDIT_AT = 14
COL_AUDIT_UNIT = 15
COL_AUDIT_ACTION = 16
COL_AUDIT_OUTCOME = 18


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%S.000Z")


def compute_sha256(data: Any) -> str:
    """Canonical SHA-256 — byte-identical to the watcher's implementation."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_iso_utc(value: str) -> Optional[datetime]:
    if not value or not value.strip():
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def approval_checksum(ap: Dict[str, Any]) -> str:
    """Checksum over CHECKSUM_FIELDS — mirrors approval_bot.approval_checksum."""
    core = {key: ap.get(key, "") for key in CHECKSUM_FIELDS}
    return compute_sha256(core)


def approval_from_row(row: List[Any]) -> Dict[str, Any]:
    values = list(row) + [""] * (len(APPROVAL_HEADER) - len(row))
    return dict(zip(APPROVAL_HEADER, values[: len(APPROVAL_HEADER)]))


def canonical_assertion_message(approval_id: str, action: str,
                                payload_hash: str, decision: str,
                                exp: str) -> bytes:
    """Canonical bytes signed by the passkey Worker (ADR-0006).

    Pipe-delimited — every field is charset-constrained (ids/hex/dotted
    action/ISO-8601), so no escaping ambiguity exists, and the JS Worker
    builds the identical string without a JSON canonicalisation step.
    """
    return ("%s|%s|%s|%s|%s"
            % (approval_id, action, payload_hash, decision, exp)
            ).encode("utf-8")


class GovernedExecutor:
    """Fail-closed executor linking pa_approvals + pa_delegation_grants +
    pa_audit_logs before any WRITE."""

    VALID_APPROVED_STATES = {"approved", "granted"}

    def __init__(self, spreadsheet: Any,
                 audit_logger: Callable[..., None],
                 now_fn: Optional[Callable[[], datetime]] = None,
                 verification_public_key: Optional[str] = None):
        self.sheet = spreadsheet
        self.append_audit_log = audit_logger
        self.now_fn = now_fn or utc_now
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        # Hex-encoded Ed25519 public key of the passkey Worker (ADR-0006).
        # None disables the assertion path: pending rows then deny, exactly
        # as before (fail-closed).
        self.verification_public_key = (verification_public_key or "").strip()
        if self.verification_public_key:
            try:
                key = bytes.fromhex(self.verification_public_key)
            except ValueError as exc:
                raise ValueError(
                    "verification_public_key must be hex") from exc
            if len(key) != 32:
                raise ValueError("verification_public_key must be 32 bytes")

    def register_tool(self, action: str, handler: Callable[[Dict[str, Any]], Any]) -> None:
        if not action or not action.strip():
            raise ValueError("register_tool: action must be non-blank")
        self._handlers[action] = handler

    # -- denial path --------------------------------------------------------
    def _deny(self, approval_id: str, action: str, reason: str,
              extra: Optional[Dict[str, Any]] = None) -> ExecutionResult:
        payload: Dict[str, Any] = {
            "approval_id": approval_id,
            "action": action,
            "reason": reason,
        }
        if extra:
            payload.update(extra)
        self.append_audit_log(
            unit="executor",
            action="write.%s" % action,
            outcome="denied",
            subjects=[approval_id] if approval_id else [],
            payload=payload,
            policy_id="AD-004-FAIL-CLOSED",
        )
        return ExecutionResult(
            ok=False,
            approval_id=approval_id,
            action=action,
            outcome="denied",
            reason=reason,
        )

    # -- gate 1: approval record --------------------------------------------
    def _find_approval_row(self, approval_id: str) -> Tuple[Optional[int], Optional[List[str]]]:
        ws = self.sheet.worksheet("pa_approvals")
        rows = ws.get_all_values()
        for idx, row in enumerate(rows[1:], start=2):  # sheet row numbers
            if row and str(row[0]).strip() == approval_id:
                return idx, list(row)
        return None, None

    # -- gate 2: delegation + limits ----------------------------------------
    def _check_delegation_grant(self, action: str) -> Tuple[bool, str, str]:
        """Verify pa_delegation_grants (A:W).

        Returns (ok, reason, limits_raw). Deny wins across every row; the
        limits cell comes from the first allowing row (single-grant sheets
        today; documented first-match semantics for multi-grant sheets).
        """
        ws = self.sheet.worksheet("pa_delegation_grants")
        rows = ws.get_all_values()
        if len(rows) <= 1:
            return False, "no_delegation_grants_configured", ""

        matched_active = False
        limits_raw = ""
        for row in rows[1:]:
            if len(row) <= COL_GRANT_ALLOWED:
                continue  # too short to hold allowed_actions — fail-closed skip
            status = str(row[COL_GRANT_STATUS]).strip().lower()
            revoked_at = str(row[COL_GRANT_REVOKED_AT]).strip() if len(row) > COL_GRANT_REVOKED_AT else ""
            if status != "active" or revoked_at:
                continue

            try:
                allowed = json.loads(row[COL_GRANT_ALLOWED]) if str(row[COL_GRANT_ALLOWED]).strip() else []
                denied = json.loads(row[COL_GRANT_DENIED]) if str(row[COL_GRANT_DENIED]).strip() else []
            except json.JSONDecodeError:
                return False, "malformed_delegation_policy_json", ""
            if not isinstance(allowed, list) or not isinstance(denied, list):
                return False, "malformed_delegation_policy_json", ""

            # Explicit deny wins — checked on every active row, in any order.
            if action in denied or "*" in denied:
                return False, "action_explicitly_denied:%s" % action, ""

            if (action in allowed or "*" in allowed) and not matched_active:
                matched_active = True
                limits_raw = str(row[COL_GRANT_LIMITS]) if len(row) > COL_GRANT_LIMITS else ""

        if not matched_active:
            return False, "no_active_grant_for_action:%s" % action, ""
        return True, "grant_verified", limits_raw

    def _count_today_executions(self, action: str, now: datetime) -> Optional[int]:
        """Count today's successful write.<action> audit rows (None = unknown)."""
        try:
            ws = self.sheet.worksheet("pa_audit_logs_202611")
            rows = ws.get_all_values()
        except Exception:
            return None
        today = now.strftime("%Y-%m-%d")
        expected_action = "write.%s" % action
        count = 0
        for row in rows[1:]:
            if len(row) <= COL_AUDIT_OUTCOME:
                continue
            if (str(row[COL_AUDIT_UNIT]) == "executor"
                    and str(row[COL_AUDIT_ACTION]) == expected_action
                    and str(row[COL_AUDIT_OUTCOME]) == "allowed"
                    and str(row[COL_AUDIT_AT])[:10] == today):
                count += 1
        return count

    def _check_limits(self, limits_raw: str, action: str,
                      now: datetime) -> Tuple[bool, str]:
        """Enforce the grant's limits cell. Fail-closed on anything unknown."""
        raw = (limits_raw or "").strip()
        if not raw:
            return True, "no_limits"
        try:
            limits = json.loads(raw)
        except json.JSONDecodeError:
            return False, "malformed_limits_json"
        if not isinstance(limits, dict):
            return False, "malformed_limits_json"

        for key, value in limits.items():
            if key == "max_per_day":
                try:
                    cap = int(value)
                except (TypeError, ValueError):
                    return False, "malformed_limits_json"
                used = self._count_today_executions(action, now)
                if used is None:
                    return False, "limits_unverifiable"
                if used >= cap:
                    return False, "limits_exceeded:max_per_day:%d>=%d" % (used, cap)
            else:
                return False, "unenforceable_limit:%s" % key
        return True, "limits_verified"

    # -- gate 3: single-use consume -----------------------------------------
    def _consume_approval(self, row_idx: int, row: List[str], now_iso: str) -> None:
        """Flip pa_approvals to consumed (version bump + checksum) via
        batch_update (version-stable gspread API, same as approval_bot)."""
        ws = self.sheet.worksheet("pa_approvals")
        padded = list(row) + [""] * max(0, len(APPROVAL_HEADER) - len(row))
        padded = padded[: len(APPROVAL_HEADER)]

        try:
            current_version = int(padded[COL_APPROVAL_VERSION]) if padded[COL_APPROVAL_VERSION] else 1
        except ValueError:
            current_version = 1
        new_version = current_version + 1

        padded[COL_APPROVAL_UPDATED_AT] = now_iso
        padded[COL_APPROVAL_VERSION] = str(new_version)
        padded[17] = "consumed"                 # R: status
        padded[COL_APPROVAL_CONSUMED_AT] = now_iso
        padded[COL_APPROVAL_CHECKSUM] = approval_checksum(approval_from_row(padded))

        ws.batch_update([{
            "range": "A%d:V%d" % (row_idx, row_idx),
            "values": [padded],
        }])

    # -- gate 1b: passkey assertion (ADR-0006) ------------------------------
    def _check_assertion(self, approval_id: str, action: str,
                         row: List[str], payload: Dict[str, Any],
                         assertion: Any, now: datetime) -> Optional[str]:
        """Verify a signed passkey decision attached to a PENDING row.

        Returns None when the assertion authorises execution, otherwise the
        deny reason (fail-closed on every structural or cryptographic gap).
        The signature binds approval_id + action + payload_hash + decision +
        exp, so a captured assertion cannot be replayed for another row,
        another payload, another action, or after its expiry — and the
        single-use consume still applies afterwards.
        """
        if not self.verification_public_key:
            return "assertion_verification_key_missing"
        if not isinstance(assertion, dict):
            return "assertion_malformed"
        required = ("approval_id", "action", "payload_hash", "decision",
                    "exp", "signature")
        if any(key not in assertion for key in required):
            return "assertion_malformed"

        env_id = str(assertion["approval_id"])
        env_action = str(assertion["action"])
        env_hash = str(assertion["payload_hash"])
        decision = str(assertion["decision"])
        exp_raw = str(assertion["exp"])
        if env_id != approval_id or env_id != str(row[0]).strip():
            return "assertion_field_mismatch:approval_id"
        if (env_action != action
                or env_action != str(row[COL_APPROVAL_ACTION]).strip()):
            return "assertion_field_mismatch:action"
        if (env_hash != str(row[COL_APPROVAL_PAYLOAD_HASH]).strip()
                or env_hash != compute_sha256(payload)):
            return "assertion_field_mismatch:payload_hash"
        if decision != "grant":
            return "assertion_invalid_decision:%s" % decision
        exp_dt = parse_iso_utc(exp_raw)
        if exp_dt is None or now >= exp_dt:
            return "assertion_expired"
        try:
            signature = base64.b64decode(str(assertion["signature"]),
                                         validate=True)
        except (ValueError, TypeError):
            return "assertion_malformed"
        public_key = bytes.fromhex(self.verification_public_key)
        message = canonical_assertion_message(env_id, env_action, env_hash,
                                              decision, exp_raw)
        if not ed25519.verify(signature, message, public_key):
            return "assertion_signature_invalid"
        return None

    # -- entry point --------------------------------------------------------
    def execute(self, approval_id: str, action: str,
                payload: Dict[str, Any],
                assertion: Optional[Dict[str, Any]] = None) -> ExecutionResult:
        """The single entry point for any WRITE operation.

        ``assertion``: optional signed passkey decision (ADR-0006) for a
        pending approval row; without it only bot-granted/approved rows
        execute.
        """
        if action not in self._handlers:
            return self._deny(approval_id, action, "unregistered_write_action")

        row_idx, row = self._find_approval_row(approval_id)
        if not row or row_idx is None:
            return self._deny(approval_id, action, "approval_record_not_found")

        padded = list(row) + [""] * max(0, len(APPROVAL_HEADER) - len(row))
        padded = padded[: len(APPROVAL_HEADER)]

        # 1. Deleted or already consumed (single-use).
        if str(padded[COL_APPROVAL_DELETED_AT]).strip():
            return self._deny(approval_id, action, "approval_deleted")
        if str(padded[COL_APPROVAL_CONSUMED_AT]).strip() or padded[17].strip().lower() == "consumed":
            return self._deny(approval_id, action, "approval_already_consumed")

        # 2. Explicit approval status, or a valid passkey assertion on a
        #    pending row (ADR-0006). Everything else denies.
        now = self.now_fn()
        status = padded[COL_APPROVAL_STATUS].strip().lower()
        if status in self.VALID_APPROVED_STATES:
            pass  # bot-voted path (transitional fallback, ADR-0006)
        elif status == "pending" and assertion is not None:
            assertion_reason = self._check_assertion(
                approval_id, action, padded, payload, assertion, now)
            if assertion_reason:
                return self._deny(approval_id, action, assertion_reason)
        else:
            return self._deny(approval_id, action,
                              "invalid_approval_status:%s" % status)

        # 3. Action match + payload binding (anti-tamper).
        row_action = str(padded[COL_APPROVAL_ACTION]).strip()
        if row_action != action:
            return self._deny(approval_id, action,
                              "action_mismatch:%s!=%s" % (row_action, action))
        expected_hash = str(padded[COL_APPROVAL_PAYLOAD_HASH]).strip()
        if not expected_hash:
            # The contract requires payload_hash to EXIST — absence denies.
            return self._deny(approval_id, action, "missing_payload_hash")
        actual_hash = compute_sha256(payload)
        if actual_hash != expected_hash:
            return self._deny(approval_id, action, "payload_hash_mismatch")

        # 4. Validity window (unparsable expiry counts as expired).
        expires_dt = parse_iso_utc(str(padded[COL_APPROVAL_EXPIRES_AT]))
        if expires_dt is None or now >= expires_dt:
            return self._deny(approval_id, action, "approval_expired_or_missing_expiry")

        # 5. Delegation grant + limits.
        grant_ok, grant_reason, limits_raw = self._check_delegation_grant(action)
        if not grant_ok:
            return self._deny(approval_id, action, grant_reason)
        limits_ok, limits_reason = self._check_limits(limits_raw, action, now)
        if not limits_ok:
            return self._deny(approval_id, action, limits_reason)

        # 6. Single-use consume BEFORE dispatch (no retry on the same approval).
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self._consume_approval(row_idx, padded, now_iso)

        # 7. Dispatch + chained audit of the outcome.
        try:
            output = self._handlers[action](payload)
        except Exception as exc:  # noqa: BLE001 — tool failure is an outcome, not a crash
            self.append_audit_log(
                unit="executor",
                action="write.%s" % action,
                outcome="failed",
                subjects=[approval_id],
                payload={"approval_id": approval_id, "error": str(exc),
                         "payload_hash": actual_hash},
                policy_id="AD-004-GOVERNED-WRITE",
            )
            return ExecutionResult(
                ok=False,
                approval_id=approval_id,
                action=action,
                outcome="failed",
                reason="tool_execution_error:%s" % exc,
            )

        self.append_audit_log(
            unit="executor",
            action="write.%s" % action,
            outcome="allowed",
            subjects=[approval_id],
            payload={"approval_id": approval_id, "payload_hash": actual_hash,
                     "status": "executed", "limits": limits_reason},
            policy_id="AD-004-GOVERNED-WRITE",
        )
        return ExecutionResult(
            ok=True,
            approval_id=approval_id,
            action=action,
            outcome="allowed",
            reason="executed_after_approval_and_grant_verification",
            output=output,
        )
