# Connector Contract v1 — العقد الرسمي لطبقة الموصلات

> **حالة الوثيقة:** عقد معتمد للتصميم — لا يوجد أي adapter منفذ بعد.
> القاعدة الحاكمة: **يُحظر أي تنفيذ خارجي لا يمر عبر هذا العقد.**

## 1. الموقع المعماري

```
User / Trigger → Agent Loop → Plan → Policy/Risk → Approval
      → Connector Gateway → Connector Adapter → External Service
      → Evidence / Audit
```

الـ Gateway **امتداد لسجل الأدوات القائم** (`registry.ts`) لا نظام موازٍ:
نفس التحقق المزدوج بـ Zod، نفس بوابة الموافقة المربوطة بـ planHash،
نفس الأثر الدائم في `runs.trace`.

## 2. المحظور المؤسِّس (anti-SSRF)

```
❌ ممنوع نهائيًا: أداة/موصل يقبل URL حرًا من الخطة أو النموذج
❌ ممنوع: payload يحدد وجهته بنفسه
❌ ممنوع: أسرار inline في المدخلات أو المخططات
```

النموذج يرى **أسماء موصلات محددة القدرات فقط** (`telegram.sendMessage`)
— الوجهة والمصادقة يملكهما الـ adapter حصرًا من مراجع بيئة الخادم.
(استثناء قائم وموثق: `web.fetch` أداة **قراءة** GET/HEAD بمهلة وسقف —
تبقى خارج طبقة الموصلات ولا تُتخذ سابقةً للكتابة الخارجية.)

## 3. عقد التعريف — ConnectorDefinition

```ts
interface ConnectorDefinition<I, O> {
  name: `${string}.${string}`;            // "telegram.sendMessage"
  description: string;                     // للـ planner manifest
  capabilities: string[];                  // ["message.send"]
  risk: 'low' | 'medium' | 'high';
  approval: 'required' | 'auto';           // مشتق من السياسة، لا من النموذج
  auth: { kind: 'secret-ref'; envKey: string };  // مرجع فقط — القيمة في بيئة الخادم
  inputSchema: z.ZodType<I>;               // parse قبل التنفيذ
  outputSchema: z.ZodType<O>;              // parse بعد التنفيذ
  timeoutMs: number;                       // إلزامي — لا افتراضي مفتوح
  retry: { max: number; retryOn: 'transient-only' };  // 429/5xx/network فقط
  idempotency: 'natural' | 'key-required'; // key-required ⇒ claim قبل الأثر
  redact: (io: unknown) => unknown;        // قبل الكتابة في أي أثر
  execute(input: I, ctx: ConnectorCtx): Promise<O>;
}
```

**إلزامات الـ Gateway على كل استدعاء (fail-closed):**

| الضابط | السلوك |
|---|---|
| موصل غير مسجل | BLOCK (نفس سلوك الأدوات) |
| فشل parse دخل/خرج | step error حرفي في الأثر |
| `approval: required` | `awaiting_approval` مربوطة (runId, stepIndex, name, planHash) — نفس جدول approvals القائم |
| سر غير موجود في البيئة | BLOCK قبل أي اتصال — لا degraded mode صامت |
| تجاوز timeout | إجهاض + تصنيف transient |
| retry | transient فقط، ضمن `retry.max`، مع نفس idempotency key |
| الأثر | مدخلات/مخرجات **بعد redact** + الحالة + المحاولات في `runs.trace` |

## 4. تصنيف المخاطر → الموافقة

| Risk | أمثلة | الموافقة |
|---|---|---|
| low | قراءة حالة خارجية | auto |
| medium | إرسال رسالة (Telegram) | **required** — أول adapter يبدأ هنا عمدًا |
| high | كتابة DB خارجية / أموال / حذف | required + غير قابلة للتحويل auto مستقبلًا إلا بقرار حوكمة |

## 5. توسيع التحقق — الرقم 33 ليس عقدًا مقدسًا

إضافة أي adapter تستلزم **قبل الدمج**:

```
Suite القائم (يبقى أخضر)
+ contract tests للموصل (تسجيل، مخططات، manifest)
+ policy tests (risk → approval mapping)
+ approval tests (ربط + رفض + 409)
+ auth-failure tests (سر مفقود/مرفوض ⇒ BLOCK لا تسريب)
+ timeout/retry tests (transient يعاد، deterministic لا)
+ idempotency tests (نفس المفتاح لا ينفذ الأثر مرتين)
+ external mock tests (بلا شبكة حقيقية في CI)
```

مقياس القبول: **التغطية السلوكية للقدرة الجديدة**، لا ثبات العدد.

## 6. خارطة التنفيذ المعتمدة

| المرحلة | المحتوى | الشرط المسبق |
|---|---|---|
| C1 | Connector Gateway (العقد أعلاه داخل registry) + Telegram adapter (`telegram.sendMessage`) | إغلاق مسار النشر الجاري (F3 + Oracle) |
| C2 | Webhook adapter **بـ allowlist وجهات** يملكها المالك في الإعدادات — ليس URL من النموذج | C1 مستقر |
| C3 | DB / أتمتة (Supabase, Make, …) | حاجة مثبتة بالاستخدام |

**لماذا Telegram أولًا:** Bot API مجاني، يختبر السلسلة كاملة
(auth + approval + failure + audit) بأقل سطح خطر.

## 7. Provider Catalog

أرقام وحدود المزودين **لا تدخل هذه الوثيقة** — تعيش في
[`PROVIDER-CATALOG.md`](PROVIDER-CATALOG.md) القابل للتحديث دوريًا.
