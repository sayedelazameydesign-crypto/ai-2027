<div dir="rtl">

# مساعد المحادثة العام للقناتين (WhatsApp · Telegram)

> **الحالة (2026-11-28):** الوحدة مُنظَّمة ومختبَرة (`python-2027/tests/test_channel_assistant.py`،
> **21 اختباراً** تمر بلا شبكة ولا أسرار). **لم تُوصَل بمتصِّل تشغيلي حيّ ولا نُشِرت** —
> الإيقاف متعمد قبل النشر أو إرسال رسائل حقيقية أو `git push`، ويلزم تصريح منفصل لهذه الخطوات.

## 1. الفكرة

نقطة استدعاء **مشتركة** واحدة للمساعد تخدم القناتين معاً: رسائل WhatsApp
الواردة ورسائل Telegram النصية تُسلَّم إلى
`ChannelAssistant.handle_inbound(...)`، التي تحفظ المحادثة، تُوجِّه الدور عبر
`FreeFirstRouter`، وترسل الرد عبر مرسل القناة المناسب. لا يوجد مساعدان
منفصلان ولا تكرار لمنطق التوجيه.

```
WhatsApp (webhook) ─┐
                    ├─▶ ChannelInboundMessage ─▶ ChannelAssistant.handle_inbound
Telegram (poller)  ─┘                              │
                                                   ├─ ConversationStore  (محادثة عامة، محدودة)
                                                   ├─ FreeFirstRouter.route(...)  ← المزود المجاني فقط
                                                   │        └─ BudgetGuard (MAX_SPEND = $0)
                                                   └─ ReplySender.send(...)  ← مقسّم إلى قطع ≤ 4096
```

## 2. الاعتماد الحصري على FreeFirstRouter / BudgetGuard

- يعتمد المساعد **حصرياً** على `FreeFirstRouter` و`BudgetGuard`. لا يبني المساعد
  أي مزودٍ مدفوع، ولا يتصل بأي خدمة مدفوعة.
- `build_assistant_router()` يستخدم `build_default_providers()`، وسلسلتها تضم
  فقط مزوّدات **`LOCAL`** (Ollama المحلي + الاحتِياطي الحتمي) ومزوّدات
  **`FREE_TIER`** مُصنَّفة صراحةً (القائمة المسموح بها في
  `budget.py::FREE_TIER_MODELS` + `openrouter/free`). مزوّدات `OMNIROUTERS` و`QWEN`
  **ليست** في السلسلة الافتراضية: كلاهما `UNKNOWN_COST`/`NON_RECLASSIFIABLE`
  ويُحجَز تحت `MAX_SPEND=0`.
- `BudgetGuard` (الافتراضي `AI2027_MAX_SPEND_USD=0`) يرفض كل نداء `PAID` أو
  `UNKNOWN_COST` **قبل** أن يغادر أي طلب الطرف. لذلك لا يمكن تجاوز سقف `$0`،
  ويكون `cost_usd` في كل رد `0.0`. المفتاح وحده **لا** يُثبت أن المزود مجاني
  (قاعدة المشروع الأساسية).
- defence-in-depth: إذا عاد التوجيه بنتيجة غير `LOCAL`/`FREE_TIER` (لا ينبغي
  أن يحدث)، يرفض المساعد الإرسال ويرجع `stage="paid_not_allowed"`.

## 3. الرد عند عدم توفر مزود مجاني

عند `AllProvidersFailedError` (لا مزود مجاني متوفر/مُعرَف) لا يُطرح أي استثناء
ولا يُستهلَك أي اعتماد: يُرسل المساعد **رداً حتمياً محدداً بـ`$0`** عربياً
(`ARABIC_NO_PROVIDER_FALLBACK`) عبر نفس المرسل، ويرجع
`stage="no_provider_available"`. هذا الرد يُنشأ محلياً ولا يمثل اتصالاً مدفوعاً.

## 4. دعم العربية

- `ARABIC_SYSTEM_PROMPT` هو توجيه النظام: يطلب الإجابة بالعربية الفصحى،
  ويمنع طلب المعلومات الحساسة، ويمنع أي اتصال مدفوع.
- يُدمج تاريخ المحادثة مع الرسالة الجديدة في نص واحد يُمرر إلى المزود.
- الرد الاحتياطي عربي. الاختبارات تثبت أن توجيه النظام عربي وأن نص
  المستخدم يُمرر.

## 5. حدود الإرسال (القطع)

كل من WhatsApp Cloud، WAAPI، وTelegram `sendMessage` ترفع سقف نص الرسالة إلى
**4096** حرفاً. الدالة `chunk_text(text, limit)` تقسّم أي رد أطول إلى قطع
كل منها ≤ الحد (تفضّل حدود الأسطر ثم الكلمات)، وكل مرسل يقطع قبل الإرسال.
يُحفَظ عدد القطع المرسلة في `AssistantReply.chunks_sent`.

## 6. منع تداخل polling مع مسار الموافقات

مسار الموافقات (`src/watchers/approval_bot.py`) هو **الحلقة الوحيدة** التي
تنطلق `getUpdates` وتملك `offset`. لذلك:

- المساعد **لا** يستدعي `getUpdates` أبداً. `TelegramReplySender` يرسل فقط
  عبر `sendMessage` (يُثبت باختبار أن أي عنوان يُطالَب به ينتهي بـ`/sendMessage`
  ولا يظهر `getUpdates`).
- `parse_telegram_update(update)` هو الجسر: يستخرج `message.text` فقط، ويُهمل
  `callback_query` (التي تخص الموافقات) والرسائل غير النصية والنصوص الفارغة.
  تستهلك حلقة الـpoller الحالية هذه الدالة لكل تحديث، فتُحافَظ على ملكية
  `offset` بواسطة بوابة الموافقات دون تداخل.
- يُثبت باختبار أن `callback_query` يُرجع `None` (لا يُوجَّه إلى المساعد).

## 7. المكوّنات (الموقع)

الوحدة: `python-2027/src/ai2027_protocol/channel_assistant.py`

| المكوّن | الدور |
|---|---|
| `ChannelAssistant` | نقطة الاستدعاء المشتركة: `handle_inbound` |
| `ChannelInboundMessage` | رسالة واردة موحَّدة (القناة، chat_id، sender، text، message_id) |
| `AssistantReply` | نتيجة منظمة (stage، المزود، التكلفة، snapshot المالي) |
| `ReplySender` (Protocol) | منفذ الإرسال الآني، يقطع النص |
| `TelegramReplySender` | `sendMessage` فقط، لا `getUpdates` |
| `WhatsAppReplySender` | يWrap `send_fn` متزامن (مثل `waapi.send_text` أو partial حول `whatsapp_cloud.send_reply`) |
| `MemoryConversationStore` | سجل محادثة محدود الدورات |
| `chunk_text` | تقسيم النص حسب حد القناة |
| `parse_telegram_update` | جسر استقبال Telegram |
| `build_assistant_router` | الـrouter الافتراضي Free-First |

### الربط بالمتصّلين الحاليين

- **Telegram:** في حلقة الـpoller الحالية (التي تملك `offset`)، لكل تحديث،
  إذا كان `parse_telegram_update(update)` يُرجع رسالة نصية، استدعِ
  `await assistant.handle_inbound(message)`. بطاقات/أصوات الموافقات تمر عبر
  `ApprovalGate.handle_callback` كما هي — لا تغيير.
- **WhatsApp:** يُمرَّر النص الوارد (بعد التحقق من التوقيع ونافذة الـ24 ساعة)
  إلى `assistant.handle_inbound(...)`. للإرسال، يُعطى `WhatsAppReplySender`
  دالة `send_fn(chat_id, text) -> dict` تغلّف المسار الحكومي الحالي
  (`whatsapp_cloud.send_reply` خلف `GovernedExecutor`، أو `waapi.send_text`).

## 8. إعدادات التشغيل (أسماء فقط — لا قيم)

تُضاف إلى `.env.example` (الجذر) و`python-2027/.env.example` و`docs/SECRETS.md`:

| المتغير | النوع | الاستعمال | الافتراضي |
|---|---|---|---|
| `AI2027_ASSISTANT_TELEGRAM_TOKEN` | **سرّ** | توكن بوت Telegram لـ`TelegramReplySender`. يرجع إلى `PA_APPROVAL_BOT_TOKEN` إن غاب | فارغ ⇒ لا إرسال (fail-closed) |
| `AI2027_ASSISTANT_TELEGRAM_CHAT_ID` | غير سرّ | محادثة الرد. يرجع إلى `PA_APPROVAL_CHAT_ID` إن غاب | فارغ ⇒ لا إرسال |
| `AI2027_ASSISTANT_MAX_HISTORY_TURNS` | غير سرّ | عدد الدورات المحتفظ بها في المحادثة (الافتراضي 8) | `8` |

> لا تُوضَع قيم الأسرار في أي ملف مُتابع. `.env` مُتجاهل من git.

## 9. الاختبارات

`python-2027/tests/test_channel_assistant.py` — كلها **hermetic** (لا شبكة، لا
أسرار، مزوّدون ومرسلون وهميون):

- اختيار المزود المجاني + تسجيل التكلفة الصفرية.
- حجب المزود المدفوع وعدم استدعائه أبداً.
- غياب المزود المجاني ⇒ الرد الحتمي `$0`.
- تكرار `message_id` ⇒ لا إعادة معالجة.
- تجميع الرد الطويل حسب حد القناة (Telegram + WhatsApp).
- عدم استدعاء `getUpdates` من مرسل Telegram.
- `callback_query` لا يُوجَّه إلى المساعد (لا تداخل polling).
- توجيه النظام العربي وتراكم تاريخ المحادثة.
- الـrouter الافتراضي لا يحتوي مزوداً مدفوعاً.

```bash
cd python-2027
PYTHONPATH=src .venv/bin/python -m pytest tests/test_channel_assistant.py -q
```

## 10. حدود الحالية

- المساعد وحدة منطقية مُختبَرة؛ **لم** يُوصَل بمتصِّل تشغيلي (لا loop Telegram
  ولا webhook WhatsApp ولا حلقّة موافقات) ولا نُشِر. إرسال رسائل حقيقية أو
  `git push` يتطلب تصريحاً منفصلاً.
- السجل داخل الذاكرة (`MemoryConversationStore`)؛ التخزين المستمر عبر
  `SessionStore`/قاعدة الرسائل يبقى قراراً تشغيلياً مستقبلياً.

</div>
