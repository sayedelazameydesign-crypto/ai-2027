# C1 Spec — `telegram.sendMessage` (تحضير read-only · التنفيذ محظور حتى فتح بوابة النشر)

> مثيل رسمي لعقد [`CONNECTOR-CONTRACT.md`](CONNECTOR-CONTRACT.md) — تصميم فقط.

## ConnectorDefinition

```ts
{
  name: 'telegram.sendMessage',
  description: 'إرسال رسالة نصية إلى دردشة Telegram محددة عبر بوت المالك',
  capabilities: ['message.send'],
  risk: 'medium',
  approval: 'required',                      // كل إرسال يتوقف على موافقة بشرية
  auth: { kind: 'secret-ref', envKey: 'TELEGRAM_BOT_TOKEN' },
  inputSchema: z.object({
    chatId: z.string().regex(/^-?\d+$/),     // معرف رقمي فقط — لا usernames حرة
    text: z.string().min(1).max(4096),
  }),
  outputSchema: z.object({
    messageId: z.number(),
    chatId: z.string(),
    sentAt: z.number(),
  }),
  timeoutMs: 10_000,
  retry: { max: 1, retryOn: 'transient-only' },   // 429/5xx/network حصرًا
  idempotency: 'key-required',              // مفتاح مشتق من (runId, stepIndex)
  redact: (io) => io مع حذف text من الأثر إن وُسم حساسًا + عدم تسجيل token إطلاقًا,
}
```

**قيود صلبة:** الوجهة `api.telegram.org` ثابتة في الـ adapter — ليست مدخلًا.
النموذج لا يرى إلا الاسم والمخطط. `chatId` يُقيد لاحقًا بـ allowlist مالكية
(v1: أي chatId رقمي + موافقة إلزامية تعوض؛ v1.1: allowlist في الإعدادات).

## مصفوفة الاختبارات (الفئات الثماني الإلزامية)

| # | الفئة | حالات الحد الأدنى |
|---|---|---|
| 1 | Contract | التسجيل، ظهور بالـ manifest بمخرجاته، رفض مدخل خاطئ (chatId نصي، text فارغ/طويل) |
| 2 | Policy | risk=medium ⇒ approval إلزامية — لا مسار يتجاوزها |
| 3 | Approval | ربط (runId, step, name, planHash) + الرفض → cancelled + double-decide → 409 |
| 4 | Auth failure | `TELEGRAM_BOT_TOKEN` غائب ⇒ BLOCK قبل أي اتصال؛ token مرفوض (401 من Telegram) ⇒ فشل مقروء بلا تسريب |
| 5 | Timeout | تجاوز 10s ⇒ إجهاض + تصنيف transient |
| 6 | Retry | 429/5xx ⇒ محاولة واحدة إضافية بنفس idempotency key؛ 400 ⇒ لا retry |
| 7 | Idempotency | نفس المفتاح مرتين ⇒ إرسال واحد فعلي (mock يعد الاستدعاءات) |
| 8 | External mock | كل ما سبق ضد mock Telegram محلي — صفر شبكة حقيقية في CI |

## متطلبات مرافقة عند التنفيذ

- سر جديد: `TELEGRAM_BOT_TOKEN` (HF Secrets) — يوثق في الدليل، لا يفترض
- بطاقة الموافقة بالواجهة تعرض chatId + معاينة النص قبل القرار
- سجل الأدلة: `messageId` الفعلي في الأثر (بعد redact) كدليل تسليم

## شرط الفتح

```
PRODUCTION-READY (HF+Turso) = PASS  ⇒  يبدأ تنفيذ C1
BLOCKED                              ⇒  يبقى هذا الملف تصميمًا فقط
```
