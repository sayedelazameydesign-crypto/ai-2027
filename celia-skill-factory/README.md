<div dir="rtl">

# CELIA Skill Factory — النواة المرجعية (المرحلة 1)

**ليست مصنعاً. هي الحلقة المفقودة: مرجع تنفيذي يثبت أن سجل الأحداث يمكن الوثوق به.**

| البند | القيمة |
|---|---|
| **الحالة** | `PROVEN` للنواة · **`BLOCKED`** للمصنع نفسه (المحرك غير موجود) |
| **التكلفة** | `$0` — مكتبة Python القياسية فقط |
| **الاعتماديات** | صفر |
| **النطاق** | سجل الأحداث · دورة المسودة · بوابة الإصدار · بصمات الأدلة |
| **خارج النطاق** | المحرك · Nimna · النواة · المهارات الأولية · أي تنفيذ |

---

## ما يفعله فعلاً

| الملف | الوظيفة | الاختبارات |
|---|---|---|
| `reference/canonical.py` | تسلسل قانوني — بايتات قابلة لإعادة الإنتاج | 9 |
| `reference/eventlog.py` | سلسلة هاش + مرساة مستقلة | 15 |
| `reference/drafts.py` | سجل مسودات منفصل تماماً | 13 |
| `reference/evidence.py` | بصمات الأدلة + بوابة الإصدار | 19 |
| **الإجمالي** | | **56 · 0 فشل** |

---

## تحقق بأمر واحد

```bash
python3 celia-skill-factory/verify.py
```

---

## المآخذ الأربعة التي أُغلقت

| المأخذ | الإغلاق | الدليل |
|---|---|---|
| **`P1`** — سجل بلا ترتيب تتابعي | `sequence` + `previous_event_sha256` + `event_sha256` | 8 اختبارات عبث فعلية |
| **`P1b`** — سجل المسودات غير محدد | `skills/drafts/{id}/events.jsonl` | 13 اختباراً |
| **`P2`** — `scripts/` غير تنفيذية | مبدأ عدم التنفيذ في الدستور §2 | — |
| **`P2b`** — لا بصمات للأدلة | بصمة لكل ملف + بصمة للحزمة | 6 اختبارات |

---

## الحد الصادق — لا يُخفى

**من يملك الكتابة على السجل والمرساة معاً يمكنه إعادة كتابة التاريخ باتساق.**

هذا **`BLOCKED`** صراحةً، ويوجد اختبار يوثّق الثغرة نفسها (`test_full_rewrite_with_anchor_is_undetected`) حتى لا تختفي من التصميم. الحل يحتاج مرساة خارجية موثوقة — خارج نطاق المرحلة 1.

**وأضعف نقطة:** `HUMAN_APPROVED` يتحقق من اسم غير فارغ، لا من توقيع. إثبات نسب لا هوية.

---

## خطأان كشفهما الاختبار، لا الوثيقة

1. **`_norm_path` كان يُطبّع قبل الفحص** — `lstrip("./")` حوّل `"../../etc/passwd"` إلى `"etc/passwd"` وقبل تسللاً. أُصلح: الفحص أولاً.
2. **`_load` كان يتحقق من الهاش دون التحقق من التسلسل** — سجل مُعاد ترتيبه كان يُفتح بلا اعتراض. أُصلح: `verify_chain()` عند التحميل.

</div>

---

<div dir="ltr">

## CELIA Skill Factory — Reference Core (Phase 1)

**Not a factory. The missing link: an executable reference proving the event log can be trusted.**

| Item | Value |
|---|---|
| **Status** | `PROVEN` for the core · **`BLOCKED`** for the factory (no engine) |
| **Cost** | `$0` — Python standard library only |
| **Dependencies** | zero |
| **Tests** | **56 · 0 failures** (`-W error::ResourceWarning`) |

### What exists

`canonical.py` — reproducible bytes (sorted keys, integers only, duplicate keys rejected, floats forbidden because their text form is not portable). `eventlog.py` — hash chain plus an independent anchor; detects edit, reorder, gap, mid-file deletion, duplicate line, tail truncation, rollback, and a *consistent* full rewrite. `drafts.py` — a separate draft log at `skills/drafts/{id}/events.jsonl` with a declared transition table; it never writes to the release log, and a test asserts that. `evidence.py` — per-file digests plus a bundle digest bound to the approval event, and a release gate that refuses release unless tests passed, evidence is pinned to what was approved, a human is named, nothing is `BLOCKED`, and the approval exists in the log.

### The honest limit

An actor with write access to both the log and the anchor can rewrite history consistently. This is labelled `BLOCKED`, and a test (`test_full_rewrite_with_anchor_is_undetected`) asserts the limitation so it cannot quietly disappear. Defeating it needs an external trusted anchor — out of Phase 1 scope. Also weak: `HUMAN_APPROVED` checks a non-empty name, not a signature; that is attribution, not authentication.

### Two bugs the tests found, not the document

1. `_norm_path` normalized before validating — `lstrip("./")` turned `"../../etc/passwd"` into `"etc/passwd"` and accepted a traversal.
2. `_load` verified hashes without verifying continuity — a reordered log opened cleanly.

### Not included — deliberately

Factory engine · Nimna · kernel · initial skills · CI · any execution path. Scripts inside a skill are inert text; no process is spawned from skill content.

</div>
