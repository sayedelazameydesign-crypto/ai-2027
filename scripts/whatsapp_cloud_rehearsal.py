#!/usr/bin/env python3
"""Hermetic end-to-end rehearsal for WhatsApp Cloud + GovernedExecutor ($0, stdlib-only).

Simulates a complete customer-service cycle without network calls or live secrets:
1. Constant-time HMAC-SHA256 verification of an inbound Meta webhook event.
2. Inbound event parsing (`accepted_text`, 24h free window check on `messages[].timestamp`).
3. Pre-consume guard (`preflight_reply_payload`) before touching single-use approval.
4. Four-gate `GovernedExecutor` execution (`pa_approvals` + `payload_hash` +
   `pa_delegation_grants` `max_per_day` + consume-before-dispatch) with chained audit.
5. Replay defense (`approval_already_consumed`) and duplicate outbound defense
   (`duplicate_outbound_reply`).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]


def _load_by_path(mod_name: str, rel_path: str) -> Any:
    full_path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(mod_name, full_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module {mod_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


class _MemorySheet:
    """In-memory worksheet/spreadsheet double matching GovernedExecutor's contract."""

    def __init__(self, tables: Dict[str, List[List[str]]]) -> None:
        self._tables = {name: [list(r) for r in rows] for name, rows in tables.items()}

    def worksheet(self, name: str) -> "_MemorySheetView":
        return _MemorySheetView(self._tables[name])


class _MemorySheetView:
    def __init__(self, rows: List[List[str]]) -> None:
        self._rows = rows

    def get_all_values(self) -> List[List[str]]:
        return [list(r) for r in self._rows]

    def batch_update(self, updates: List[Dict[str, Any]]) -> None:
        for item in updates:
            row_idx = int(str(item["range"]).split(":")[0][1:]) - 1
            self._rows[row_idx] = list(item["values"][0])


def _scrub_live_messaging_env() -> None:
    """Remove any live WhatsApp/Meta/YCloud/WAAPI env vars inside the rehearsal scope."""
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith(("WHATSAPP_", "WAAPI_", "META_", "YCLOUD_", "AI2027_WHATSAPP_")):
            os.environ.pop(name, None)


def run_rehearsal() -> Dict[str, Any]:
    """Execute the full offline WhatsApp Cloud + GovernedExecutor rehearsal."""
    wc = _load_by_path("whatsapp_cloud_rehearsal_wc", "src/connectors/whatsapp_cloud.py")
    ge = _load_by_path("whatsapp_cloud_rehearsal_ge", "src/executors/governed_executor.py")

    saved_env = dict(os.environ)
    saved_transport = wc._transport
    try:
        _scrub_live_messaging_env()
        os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "fixture-" + "rehearsal-bearer"
        os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "109876543210987"
        signing_material = b"fixture-rehearsal-signing-2027"

        now_dt = datetime(2026, 11, 28, 15, 0, 0, tzinfo=timezone.utc)
        now_epoch = int(now_dt.timestamp())
        inbound_ts = now_epoch - 120
        sample_wamid = "wamid.HBgMMjAxMDEyMzQ1Njc4FQIAEhggUkVIRUFSU0FM=="

        webhook_obj = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "waba_rehearsal",
                    "time": now_epoch,
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "109876543210987"},
                                "messages": [
                                    {
                                        "from": "201012345678",
                                        "id": sample_wamid,
                                        "timestamp": str(inbound_ts),
                                        "type": "text",
                                        "text": {"body": "مرحباً، أريد تأكيد موعد التسليم"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        raw_webhook = json.dumps(webhook_obj, ensure_ascii=False).encode("utf-8")
        sig_header = "sha256=" + hmac.new(signing_material, raw_webhook, hashlib.sha256).hexdigest()

        sig_ok, sig_stage = wc.verify_hub_signature(raw_webhook, sig_header, signing_material)
        seen_inbound: set[str] = set()
        events = wc.parse_inbound_events(
            webhook_obj,
            now_epoch_s=now_epoch,
            seen_effect_keys=seen_inbound,
        )
        inbound_stage = events[0].stage if events else "none"

        outbound_payload = {
            "recipient_phone": events[0].sender_wa_id,
            "message_text": "تم تأكيد موعد التسليم غداً الساعة 2 ظهراً بإذن الله.",
            "inbound_timestamp_s": events[0].timestamp_s,
            "reply_to_wamid": events[0].wamid,
        }
        pre_check = wc.preflight_reply_payload(outbound_payload, now_epoch_s=now_epoch)

        wc._transport = lambda method, url, body, _b: wc.CloudTransportResult(
            "ok",
            200,
            {"messages": [{"id": "wamid.REHEARSAL_OUT_01"}]},
        )

        appr_row = [""] * 22
        appr_row[0] = "01APPR_REHEARSAL"
        appr_row[1] = "2026-11-28T14:59:00.000Z"
        appr_row[2] = "2026-11-28T14:59:00.000Z"
        appr_row[3] = "1"
        appr_row[13] = "cd" * 32
        appr_row[14] = "whatsapp_cloud.send_reply"
        appr_row[15] = ge.compute_sha256(outbound_payload)
        appr_row[16] = "Rehearsal customer reply"
        appr_row[17] = "granted"
        appr_row[18] = "2026-11-28T14:59:00.000Z"
        appr_row[19] = "2026-11-28T16:00:00.000Z"
        appr_row[21] = "true"

        appr_row_2 = list(appr_row)
        appr_row_2[0] = "01APPR_REHEARSAL_CAP"

        grant_row = [""] * 23
        grant_row[0] = "01GRANT_REHEARSAL"
        grant_row[14] = "agent.write"
        grant_row[15] = "active"
        grant_row[17] = json.dumps(["whatsapp_cloud.send_reply"])
        grant_row[18] = json.dumps([])
        grant_row[19] = json.dumps({"max_per_day": 1})
        grant_row[21] = "owner"

        sheet = _MemorySheet(
            {
                "pa_approvals": [["h"] * 22, appr_row, appr_row_2],
                "pa_delegation_grants": [["h"] * 23, grant_row],
                "pa_audit_logs_202611": [["h"] * 25],
            }
        )
        audit_rows: List[Dict[str, Any]] = []

        def _record_audit(**kw: Any) -> None:
            audit_rows.append(kw)
            row25 = [""] * 25
            row25[14] = "2026-11-28T15:00:00.000Z"
            row25[15] = str(kw.get("unit") or "")
            row25[16] = str(kw.get("action") or "")
            row25[18] = str(kw.get("outcome") or "")
            sheet._tables["pa_audit_logs_202611"].append(row25)

        sent_outbound: set[str] = set()
        executor = ge.GovernedExecutor(
            sheet,
            _record_audit,
            now_fn=lambda: now_dt,
        )
        executor.register_tool(
            "whatsapp_cloud.send_reply",
            wc.make_send_reply_handler(
                now_epoch_fn=lambda: now_epoch,
                sent_effect_keys=sent_outbound,
            ),
        )

        first_exec = executor.execute(
            "01APPR_REHEARSAL", "whatsapp_cloud.send_reply", outbound_payload
        )
        replay_exec = executor.execute(
            "01APPR_REHEARSAL", "whatsapp_cloud.send_reply", outbound_payload
        )
        cap_exec = executor.execute(
            "01APPR_REHEARSAL_CAP", "whatsapp_cloud.send_reply", outbound_payload
        )

        # Also verify explicit delegation denial (`denied_actions` wins over `allowed_actions`).
        grant_denied = list(grant_row)
        grant_denied[18] = json.dumps(["whatsapp_cloud.send_reply"])
        sheet._tables["pa_delegation_grants"][1] = grant_denied
        appr_row_3 = list(appr_row)
        appr_row_3[0] = "01APPR_REHEARSAL_DENY"
        sheet._tables["pa_approvals"].append(appr_row_3)
        delegation_denied = executor.execute(
            "01APPR_REHEARSAL_DENY", "whatsapp_cloud.send_reply", outbound_payload
        )

        all_ok = bool(
            sig_ok
            and inbound_stage == "accepted_text"
            and pre_check["ok"]
            and first_exec.ok
            and not replay_exec.ok
            and replay_exec.reason == "approval_already_consumed"
            and not cap_exec.ok
            and cap_exec.reason.startswith("limits_exceeded:max_per_day")
            and not delegation_denied.ok
            and delegation_denied.reason.startswith("action_explicitly_denied")
            and len(audit_rows) == 4
        )
        return {
            "ok": all_ok,
            "signature_stage": sig_stage,
            "inbound_stage": inbound_stage,
            "preflight_stage": pre_check["stage"],
            "execute_outcome": first_exec.outcome,
            "replay_denied_reason": replay_exec.reason,
            "daily_cap_denied_reason": cap_exec.reason,
            "delegation_denied_reason": delegation_denied.reason,
            "audit_rows_recorded": len(audit_rows),
        }
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        wc._transport = saved_transport


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run hermetic WhatsApp Cloud rehearsal.")
    parser.add_argument("--assert", dest="must_pass", action="store_true")
    args = parser.parse_args(argv)
    report = run_rehearsal()
    if report["ok"]:
        sys.stdout.write(
            "OK: whatsapp_cloud + governed_executor rehearsal passed (0 network, $0)\n"
        )
        return 0
    sys.stdout.write("FAIL: whatsapp_cloud rehearsal failed\n")
    return 1 if args.must_pass else 0


if __name__ == "__main__":
    raise SystemExit(main())
