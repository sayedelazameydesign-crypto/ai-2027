import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# يُفترض وجود موصلات Google و Sheets في المستودع
# من طبقة G0/G1 OAuth2 المهيأة مسبقاً
import gspread
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# ==========================================
# 1. إعدادات النظام والثوابت الحاكمة
# ==========================================
SPREADSHEET_ID = os.getenv("PA_MEMORY_SHEET_ID", "")
POLL_INTERVAL_SECONDS = int(os.getenv("WATCHER_INTERVAL_SEC", "900"))  # 15 دقيقة افتراضياً

# قواعد الحجب المحلية (ssGateway Local Rules)
SENSITIVE_PATTERNS = [
    r"\b\d{14}\b",                          # الرقم القومي المصري
    r"\b(?:\d[ -]*?){13,16}\b",             # أرقام البطاقات البنكية
    r"(?i)(password|otp|pin|secret|token)\s*[:=]\s*\S+",  # كلمات المرور والرموز
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",    # أرقام الحسابات الدولية IBAN
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def generate_ulid_like() -> str:
    """توليد معرّف زمني متوافق مع صيغة 01M7... في الجدول."""
    ts = int(time.time() * 1000)
    rand_part = hashlib.sha256(os.urandom(16)).hexdigest()[:16].upper()
    return f"01{ts:X}{rand_part}"[:26]


def compute_sha256(data: Any) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ==========================================
# 2. طبقة الحماية وحجب البيانات الحساسة (ssGateway)
# ==========================================
def sanitize_and_redact(text: str) -> tuple[str, str]:
    """
    تنقيح النص وحجب البيانات الحساسة.
    تُرجع: (النص المنقّح، مستوى الحساسية 'personal' أو 'restricted')
    """
    if not text:
        return "", "personal"

    redacted = text
    sensitivity = "personal"

    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, redacted):
            sensitivity = "restricted"
            redacted = re.sub(pattern, "[REDACTED_SENSITIVE]", redacted)

    # تقليص المقتطف لحماية الخصوصية ومنع تضخم الذاكرة
    excerpt = redacted.strip()[:280]
    return excerpt, sensitivity


# ==========================================
# 3. محرك المراقبة الاستباقية (Proactive Watcher)
# ==========================================
class ProactiveWatcher:
    def __init__(self, creds: Credentials):
        self.creds = creds
        self.gc = gspread.authorize(creds)
        self.sheet = self.gc.open_by_key(SPREADSHEET_ID)
        self.gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def _get_existing_external_ids(self, ws) -> set:
        """قراءة المعرّفات الخارجية المسجلة مسبقاً لمنع التكرار."""
        records = ws.get_all_values()
        if len(records) <= 1:
            return set()
        # العمود P (index 15) يمثل المعرّف الخارجي في pa_source_records
        return {row[15] for row in records[1:] if len(row) > 15 and row[15]}

    def _get_last_audit_hash(self, audit_ws) -> str:
        """جلب self_hash لآخر صف في سجل التدقيق للحفاظ على السلسلة."""
        rows = audit_ws.get_all_values()
        if len(rows) <= 1:
            return "GENESIS_00000000000000000000000000000000"
        last_row = rows[-1]
        # العمود Y (index 24) هو self_hash
        return last_row[24] if len(last_row) > 24 and last_row[24] else "GENESIS"

    def append_audit_log(
        self,
        unit: str,
        action: str,
        outcome: str,
        subjects: List[str],
        payload: Dict[str, Any],
        policy_id: str = "P1-WATCHER-READONLY",
    ) -> None:
        audit_ws = self.sheet.worksheet("pa_audit_logs_202611")
        now = utc_now_iso()
        log_id = generate_ulid_like()
        prev_hash = self._get_last_audit_hash(audit_ws)
        payload_hash = compute_sha256(payload)

        core_record = {
            "id": log_id,
            "at": now,
            "unit": unit,
            "action": action,
            "actor": "system",
            "outcome": outcome,
            "subjects": subjects,
            "payload_hash": payload_hash,
        }
        checksum = compute_sha256(core_record)
        self_hash = compute_sha256({"prev_hash": prev_hash, "checksum": checksum})

        # مطابق لترتيب الأعمدة A:Y في pa_audit_logs_202611
        row = [
            log_id,                     # A: id
            now,                        # B: created_at
            now,                        # C: updated_at
            1,                          # D: version
            "",                         # E: source_id
            1,                          # F: confidence
            "fact",                     # G: epistemic
            "personal",                 # H: sensitivity
            "not_granted",              # I: consent
            "review",                   # J: retention
            "2028-11-26",               # K: review_at
            "",                         # L: deleted_at
            "clean",                    # M: taint
            checksum,                   # N: checksum
            now,                        # O: at
            unit,                       # P: unit
            action,                     # Q: action
            "system",                   # R: actor
            outcome,                    # S: outcome
            json.dumps(subjects),       # T: subjects
            payload_hash,               # U: payload_hash
            policy_id,                  # V: policy_id
            "",                         # W: approval_id
            prev_hash,                  # X: prev_hash
            self_hash,                  # Y: self_hash
        ]
        audit_ws.append_row(row, value_input_option="RAW")

    def poll_gmail(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """قراءة الرسائل غير المقروءة الحديثة من Gmail (قراءة فقط)."""
        src_ws = self.sheet.worksheet("pa_source_records")
        existing_ids = self._get_existing_external_ids(src_ws)

        results = self.gmail.users().messages().list(
            userId="me", q="is:unread -category:promotions -category:social", maxResults=max_results
        ).execute()
        messages = results.get("messages", [])

        ingested = []
        now = utc_now_iso()

        for msg_meta in messages:
            msg_id = msg_meta["id"]
            if msg_id in existing_ids:
                continue

            msg = self.gmail.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "unknown")
            subject = headers.get("Subject", "(بدون عنوان)")
            snippet = msg.get("snippet", "")
            thread_id = msg.get("threadId", msg_id)

            raw_text = f"{subject} — {snippet}"
            excerpt, sensitivity = sanitize_and_redact(raw_text)
            content_hash = compute_sha256(raw_text)
            rec_id = generate_ulid_like()

            row_payload = {
                "source": "gmail",
                "external_id": msg_id,
                "author": sender,
                "content_hash": content_hash,
            }
            checksum = compute_sha256(row_payload)

            # مطابق لترتيب الأعمدة A:W في pa_source_records
            row = [
                rec_id,                                             # A: id
                now,                                                # B: created_at
                now,                                                # C: updated_at
                1,                                                  # D: version
                "",                                                 # E: source_id
                1,                                                  # F: confidence
                "fact",                                             # G: epistemic
                sensitivity,                                        # H: sensitivity
                "not_granted",                                      # I: consent
                "review",                                           # J: retention
                "2027-11-26",                                       # K: review_at
                "",                                                 # L: deleted_at
                "tainted",                                          # M: taint (إلزامي لأي مدخل خارجي)
                checksum,                                           # N: checksum
                "gmail",                                            # O: source_type
                msg_id,                                             # P: external_id
                f"https://mail.google.com/mail/u/0/#inbox/{msg_id}",# Q: external_url
                thread_id,                                          # R: thread_id
                now,                                                # S: received_at
                sender,                                             # T: author
                content_hash,                                       # U: content_hash
                excerpt,                                            # V: excerpt
                now,                                                # W: fetched_at
            ]
            src_ws.append_row(row, value_input_option="RAW")
            ingested.append({"id": rec_id, "subject": subject, "author": sender, "sensitivity": sensitivity})

        return ingested

    def poll_calendar(self, hours_ahead: int = 48) -> List[Dict[str, Any]]:
        """فحص أحداث التقويم القادمة خلال 48 ساعة."""
        src_ws = self.sheet.worksheet("pa_source_records")
        existing_ids = self._get_existing_external_ids(src_ws)

        now_dt = datetime.now(timezone.utc)
        time_min = now_dt.isoformat()
        time_max = (now_dt + timedelta(hours=hours_ahead)).isoformat()

        events_res = self.calendar.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        ingested = []
        now = utc_now_iso()

        for ev in events_res.get("items", []):
            ev_id = ev["id"]
            if ev_id in existing_ids:
                continue

            summary = ev.get("summary", "(حدث بدون عنوان)")
            start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ""))
            organizer = ev.get("organizer", {}).get("email", "calendar")
            html_link = ev.get("htmlLink", "")

            excerpt, sensitivity = sanitize_and_redact(f"[{start}] {summary}")
            content_hash = compute_sha256(f"{ev_id}|{start}|{summary}")
            rec_id = generate_ulid_like()
            checksum = compute_sha256({"source": "calendar", "external_id": ev_id, "hash": content_hash})

            row = [
                rec_id, now, now, 1, "", 1, "fact", sensitivity,
                "not_granted", "review", "2027-11-26", "", "tainted", checksum,
                "calendar", ev_id, html_link, ev_id, start, organizer,
                content_hash, excerpt, now,
            ]
            src_ws.append_row(row, value_input_option="RAW")
            ingested.append({"id": rec_id, "summary": summary, "start": start})

        return ingested

    def extract_candidate_tasks(self, records: List[Dict[str, Any]]) -> int:
        """استخراج المهام المرشحة من السجلات الجديدة وكتابتها في pa_tasks."""
        if not records:
            return 0

        tasks_ws = self.sheet.worksheet("pa_tasks")
        existing_rows = tasks_ws.get_all_values()
        # العمود V (index 21) هو dedupe_key
        existing_dedupes = {r[21] for r in existing_rows[1:] if len(r) > 21 and r[21]}

        action_keywords = ("مطلوب", "عاجل", "تأكيد", "موعد", "فاتورة", "action required", "urgent", "invoice", "deadline")
        now = utc_now_iso()
        added_count = 0

        for rec in records:
            text_check = rec.get("subject", rec.get("summary", "")).lower()
            if not any(kw in text_check for kw in action_keywords):
                continue

            dedupe_key = compute_sha256({"source_id": rec["id"], "title": text_check})
            if dedupe_key in existing_dedupes:
                continue

            task_id = generate_ulid_like()
            title = f"مراجعة وارد: {rec.get('subject', rec.get('summary', ''))[:100]}"
            checksum = compute_sha256({"id": task_id, "title": title, "source_id": rec["id"]})

            # مطابق لترتيب الأعمدة A:V في pa_tasks
            row = [
                task_id,            # A: id
                now,                # B: created_at
                now,                # C: updated_at
                1,                  # D: version
                rec["id"],          # E: source_id
                0.85,               # F: confidence
                "inferred",         # G: epistemic
                "personal",         # H: sensitivity
                "not_granted",      # I: consent
                "review",           # J: retention
                "2027-11-26",       # K: review_at
                "",                 # L: deleted_at
                "clean",            # M: taint
                checksum,           # N: checksum
                title,              # O: title
                "open",             # P: status
                "",                 # Q: due_at
                "normal",           # R: priority
                "my-personal-agent",# S: project
                "",                 # T: done_at
                "",                 # U: blocked_by
                dedupe_key,         # V: dedupe_key
            ]
            tasks_ws.append_row(row, value_input_option="RAW")
            added_count += 1

        return added_count

    def run_cycle(self) -> Dict[str, Any]:
        """تشغيل دورة مراقبة واحدة كاملة وتسجيلها في سجل التدقيق."""
        gmail_items = self.poll_gmail()
        cal_items = self.poll_calendar()
        all_items = gmail_items + cal_items
        tasks_created = self.extract_candidate_tasks(all_items)

        summary = {
            "gmail_ingested": len(gmail_items),
            "calendar_ingested": len(cal_items),
            "tasks_created": tasks_created,
        }
        subjects = [item["id"] for item in all_items]

        self.append_audit_log(
            unit="watcher",
            action="proactive_poll_cycle",
            outcome="allowed",
            subjects=subjects,
            payload=summary,
        )
        return summary


def start_daemon(creds: Credentials) -> None:
    """حلقة التشغيل الدوري المستمرة (Daemon Loop)."""
    watcher = ProactiveWatcher(creds)
    while True:
        try:
            report = watcher.run_cycle()
            print(f"[{utc_now_iso()}] Cycle completed: {report}")
        except Exception as exc:
            print(f"[{utc_now_iso()}] Watcher error: {exc}")
        time.sleep(POLL_INTERVAL_SECONDS)
