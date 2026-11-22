<div dir="rtl">

# نظرة عامة — AI-2027 / CeliaOS (صفحة واحدة)

**ما هو النظام، لماذا بُني، ما الذي يعمل الآن، وكيف تجربه مجاناً.**

| البند | القيمة |
|---|---|
| **النظام** | AI-2027 / CeliaOS — منظومة وكلاء ومحركات محلية، مجانية في البداية |
| **القاعدة الحاكمة** | Zero-Cost First — كل تشغيل في مرحلة التأسيس مجاني 100% |
| **الجرد الكامل** | `docs/ENGINEERING-INVENTORY-2027.md` |
| **لوحة العرض** | `docs/engineering-inventory-2027.html` (تُفتح من الملف مباشرة) |
| **البيانات** | `docs/inventory-2027.json` |

---

## ما هو؟

منظومة هندسية موحدة لعام 2027 تجمع نواة وكلاء حتمية، وأسطول تنفيذ دائم، وبروتوكول Python بمُوجِّه نماذج بميزانية $0، وذاكرة مشروطة بالدليل، ومراقبة حتمية، ولوحة تحكم بفحص أمان — كلها تعمل **محلياً** وبصفر تكلفة، وكل قدرة فيها مصنّفة بدقة: ما نُفِّذ فعلاً (`PROVEN`)، وما هو موثّق فقط (`DECLARED`)، وما هو محاكاة (`SIMULATED`)، وما فشل (`FAILED`).

## لماذا بُني؟

لأن معظم الأنظمة تُسوَّق بلا دليل. هذا النظام بُني على قاعدة واحدة: **لا رقم بلا أمر نُفِّذ**. كل ادعاء له دليل قابل لإعادة الإنتاج، وكل غياب مُعلن صراحةً. النتيجة: منظومة يمكن الوثوق بها لأنها تُظهر حدودها بنفسها.

## ما الذي يعمل الآن؟ (PROVEN — مُقاس)

| القدرة | الدليل |
|---|---|
| نواة حتمية صفر الاعتماديات (`agi-system` + `live-agent`) | 139 اختباراً ناجحاً |
| نواة مشتركة (`kernel-2027`) — 10 بدائيات | 152 اختباراً ناجحاً |
| بروتوكول Python + مُوجِّه $0 (`python-2027`) | 832 اختباراً ناجحاً |
| أسطول وكلاء دائم (`fleet-2027`) | 285 اختباراً ناجحاً |
| ذاكرة مشروطة بالدليل + مراقبة حتمية | 72 اختباراً ناجحاً |
| فحص أمان red-team | **100 هجوم · 0 اختراق** |
| **الإجمالي** | **2,054 اختباراً ناجحاً · 0 فشل** |

**ما لا يعمل بعد:** 40 مشروعاً في الأرشيف موثّقة فقط (`DECLARED`)، و6 محاكاة (`SIMULATED`)، و1 فشل (`FAILED`)، والمسار الرابع (الوسائط/الطباعة) غير مُنفَّذ. التفصيل في الجرد §7.

## كيف تجربه مجاناً؟ (محلي · $0 · لا يمس `projects/`)

```bash
git clone https://github.com/sayedelazameydesign-crypto/ai-2027.git
cd ai-2027
# افتح docs/engineering-inventory-2027.html في المتصفح (لا خادم)
python3 command-hud/audit.py            # جرد الـ48 مشروعاً
python3 command-hud/redteam.py --markdown  # 100 هجوم
```

التفاصيل الكاملة: `docs/ZERO-COST-PLAYBOOK-2027.md` (§4 — مسار التجربة الآمن).

---

<div dir="ltr">

## Executive Summary (EN)

**AI-2027 / CeliaOS** is a local-first agent and engine ecosystem built under one rule: **no number without an executed command**. Every capability is labeled PROVEN (executed and passed), DECLARED (documented only), SIMULATED, FAILED, or BLOCKED.

**What works now (measured):** a zero-dependency deterministic kernel, a shared kernel of 10 primitives, a Python protocol with a $0 model router, a durable agent fleet, evidence-gated memory, deterministic monitoring, and a red-team harness — **2,054 tests passed, 0 failed; 100 attacks, 0 breaches**. The 48-project archive is mostly DECLARED (40), not operational; the media/pre-press track is not implemented.

**Try it free:** clone the public repo, open `docs/engineering-inventory-2027.html` in a browser, and re-run `python3 command-hud/audit.py` and `python3 command-hud/redteam.py --markdown` — all local, $0, read-only. Full details: `docs/ENGINEERING-INVENTORY-2027.md` and `docs/ZERO-COST-PLAYBOOK-2027.md`.

</div>

</div>
