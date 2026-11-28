# دليل التشغيل وقائمة التحقق للتفعيل الحي — WhatsApp Business Cloud API (`$0`)

يوثّق هذا الدليل خطوات الانتقال الآمن من البروفة المحلية المعزولة (`Hermetic`) إلى الاستقبال والإرسال الحي عبر **Meta WhatsApp Business Cloud API** (`src/connectors/whatsapp_cloud.py`) مقترناً ببوابات التنفيذ الأربع في `src/executors/governed_executor.py` وخط التعلّم التأملي `ReflectiveCognitivePipeline` تحت سقف تكلفة صفري (`MAX_SPEND = 0.0`).

---

## 1. قائمة التحقق قبل التفعيل الحي (Pre-Flight Checklist)

### 1.1 متغيرات البيئة على خادم الاستقبال فقط (لا تُرفع إلى Git أبداً)
يُحظر وضع أي سر داخل الكود أو ملفات الإعداد المتتبَّعة؛ تُضبط المتغيرات التالية حصراً في مخزن أسرار المنصة أو ملف `.env` المحلي المعزول:

| المتغير | الغرض | قواعد التحقق المغلقة (`Fail-Closed`) |
|---|---|---|
| `WHATSAPP_CLOUD_ACCESS_TOKEN` | رمز وصول Graph API لحساب واتساب للأعمال | غيابه يوقف `preflight()` محلياً بمرحلة `missing_access_credential` قبل استهلاك أي موافقة. |
| `WHATSAPP_CLOUD_PHONE_NUMBER_ID` | المعرّف الرقمي لرقم الأعمال في Meta (`6..25` رقماً) | يُفحص بالتعبير `^[1-9]\d{5,24}$`؛ أي قيمة مخالفة تُرفض بـ `invalid_phone_number_id`. |
| سر توقيع التطبيق (`App Secret`) | يُمرَّر كبايتات إلى `verify_hub_signature(raw_body, header, signing_bytes)` | يتحقق بزمن ثابت (`hmac.compare_digest`) من الترويسة `X-Hub-Signature-256: sha256=<hex>`. |
| رمز تحدي الاشتراك (`Verify Token`) | يُمرَّر إلى `verify_webhook_challenge(mode, provided, expected, challenge)` | يتحقق بزمن ثابت عند `hub.mode == "subscribe"` ويرفض أي `challenge` غير قياسي. |

---

### 1.2 إعداد نقطة نهاية Webhook في Meta for Developers
1. اربط مسار `GET /webhook` بدالة `verify_webhook_challenge(...)` وأعد نص `challenge` فقط عندما تكون النتيجة `ok == True` (`HTTP 200`)، وإلا أعد `HTTP 403` بلا أي تفاصيل داخلية.
2. اربط مسار `POST /webhook` بقراءة الجسم الخام (`raw_body: bytes` بحد أقصى `1 MiB`) وفحصه عبر `verify_hub_signature(...)` **قبل** فك ترميز JSON؛ عند فشل التوقيع أعد `HTTP 401` فوراً.
3. مرّر الحمولة المفحوصة إلى `parse_inbound_events(payload, seen_effect_keys=...)`؛ لا تُعالج سوى الأحداث ذات المرحلة `stage == "accepted_text"` (نصية، غير مكررة بـ `wamid`، وداخل نافذة الـ 24 ساعة من `messages[].timestamp`).

---

### 1.3 تفعيل صف التفويض في `pa_delegation_grants` (Memory P0)
يتطلب `GovernedExecutor` وجود صف تفويض نشط وغير ملغى في ورقة `pa_delegation_grants` (`A:W`) قبل السماح بتنفيذ الأداة `whatsapp_cloud.send_reply`:

| العمود | اسم الحقل | القيمة المطلوبة |
|---|---|---|
| `O` (الفهرس 14) | `scope` | `agent.write` |
| `P` (الفهرس 15) | `status` | `active` |
| `R` (الفهرس 17) | `allowed_actions` | `["whatsapp_cloud.send_reply"]` (أو قائمة JSON تتضمنها) |
| `S` (الفهرس 18) | `denied_actions` | `[]` (تأكد ألا تتضمن `whatsapp_cloud.send_reply` لأن المنع يغلب دائماً) |
| `T` (الفهرس 19) | `limits` | مثال: `{"max_per_day": 50}` (سقف يومي قابل للتحقق من سجل التدقيق) |
| `W` (الفهرس 22) | `revoked_at` | فارغ `""` |

---

## 2. تسلسل التشغيل المحكوم من الاستقبال حتى الإرسال (`$0`)

1. **الاستقبال ومنع التكرار:** استخراج `InboundWhatsAppEvent` مع `idempotency_key` المشتق عبر `kernel2027.effect_key`.
2. **التوليد والتحقق المستقل:** تمرير نص استفسار العميل إلى `ReflectiveCognitivePipeline.execute(...)` لاسترجاع الدروس المتحققة من `ReflectiveGraphMemory` وتوليد الرد والتحقق منه مستقلاً وتحديث عدّادات `RMM` ذرياً.
3. **إصدار طلب الموافقة:** حساب بصمة حمولة الرد `payload_hash = compute_sha256(payload)` وتسجيل صف موافقة `pending` في `pa_approvals` (`single_use = true` وتاريخ انتهاء `expires_at` صريح).
4. **الفحص المسبق قبل الاستهلاك (`Pre-Consume Guard`):** قبل استدعاء `GovernedExecutor.execute(...)` بعد موافقة المشغّل، شغّل وجوباً:
   ```python
   pre = whatsapp_cloud.preflight_reply_payload(payload)
   ```
   إذا عادت `pre["ok"] is False` (مثلاً `outside_24h_service_window` بسبب تأخر التصويت لأكثر من 24 ساعة من `messages[].timestamp`)، أوقف التنفيذ محلياً ولا تستهلك الموافقة ولا تحاول إرسال قالب مدفوع.
5. **التنفيذ والتدقيق المتسلسل:** عند نجاح الفحص المسبق، يستهلك `GovernedExecutor.execute(...)` الموافقة لمرة واحدة، وينفّذ `whatsapp_cloud.make_send_reply_handler(...)`، ويسجّل النتيجة في `pa_audit_logs_202611` بالسلسلة `prev_hash -> self_hash`.

---

## 3. مراقبة السجلات وأكواد التنبيه في `pa_audit_logs_202611`

تُصمَّم جميع مراحل الرفض بحيث لا تطبع أي سر أو ترويسة أو جسم استجابة خام؛ راقب الأكواد التالية في لوحة الرصد:

| المرحلة / السبب المسجّل | الدلالة التشغيلية | الإجراء المطلوب |
|---|---|---|
| `invalid_signature` / `malformed_signature` | فشل التحقق من `X-Hub-Signature-256` | تحقق من تطابق سر توقيع تطبيق Meta وعدم تعديل البروكسي للجسم الخام. |
| `outside_24h_service_window` | تجاوز `86,400` ثانية من `messages[].timestamp` | سلوك طبيعي لحماية سقف `$0`؛ قلّل زمن الاستجابة للموافقات المعلقة. |
| `duplicate_event` / `duplicate_outbound_reply` | إعادة تسليم من Meta أو محاولة إرسال مكررة لنفس المحتوى | محمي تلقائياً عبر `effect_key` و`sent_effect_keys` دون أثر خارجي. |
| `payload_hash_mismatch` | تغيّر أي حقل في الحمولة بعد إصدار الموافقة | تنبيه أمني (منع تلاعب بالحمولة) — أصدر موافقة جديدة للحمولة الصحيحة. |
| `approval_already_consumed` | محاولة إعادة استخدام موافقة أحادية مستهلكة | محمي تلقائياً؛ كل إرسال خارجي يتطلب موافقة مستقلة جديدة. |
| `limits_exceeded:max_per_day` | بلوغ سقف `max_per_day` في `pa_delegation_grants` | راجع حجم الرسائل اليومي قبل رفع السقف في العمود `T`. |

> **تصنيف التنبيهات الآلي (`whatsapp_cloud.classify_audit_stage`):** تصنّف الدالة كل سبب رفض إلى أربع فئات رصد: `security_tamper` (تنبيه أمني فوري عند فشل التوقيع أو عدم تطابق `payload_hash`)، و`idempotency_guard` (كبح تكرار طبيعي)، و`policy_budget_gate` (إنفاذ نافذة الـ 24 ساعة أو سقف `max_per_day` أو رفض التفويض)، و`protocol_or_transport` (تحذير شبكة أو إعدادات).

---

## 4. أمر البروفة المحلية قبل التشغيل الحي

للتحقق من سلامة المسار الكامل محلياً بصفر شبكة وصفر تكلفة شغّل من جذر المستودع:

```bash
python3 scripts/whatsapp_cloud_rehearsal.py --assert
```
