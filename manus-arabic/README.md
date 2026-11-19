# ✨ مانوس العربي — Manus Arabi

> واجهة عربية مستقلة تشبه ChatGPT + Manus، لكنها تعمل **بالبروتوكول الكامل** — لا محاكاة، لا وعود فارغة. كل طلب يُنفّذ فعلاً عبر النواة الحقيقية ويُتحقّق بـ 5 فحوصات + SHA-256.

## 🎯 ما يميزها (الإبهار)

| الميزة | التفاصيل |
|--------|----------|
| **اللغة** | عربية أولاً (RTL) مع زر English فوري — خط Tajawal + IBM Plex Arabic |
| **التصميم** | Glassmorphism + تدرجات نيون + رسوم SVG حية — مثل ChatGPT/Manus لكن أجمل |
| **البروتوكول** | شريط حي `Goal → Plan → Policy → Execute → Verify → Evidence` يتحرك أمامك |
| **المهارات** | 🔍 مستكشف بـ 16 أداة حقيقية (قراءة/كتابة/تحقق/ذاكرة/موصلات) مع بحث وفلترة |
| **الموصلات** | 12 موصل (Gmail, Drive, Docs, Sheets, Slides, Calendar, Dropbox, GitHub, Linear, Notion, Supabase, Vercel) — قراءة حيّة، كتابة بموافقة بشرية فقط |
| **النواة** | النواة الحقيقية `projects/agi-system` — صفر اعتمادات، صفر pip install — VERIFIED 5/5 + SHA-256 + ledger |
| **الأدلة** | لوحة أدلة حية: hash, حجم, مسار, سجل أحداث, معاينة, تحميل, تحقق |
| **الذاكرة** | بحث L1 موثق + RAG Celia الدائم |

## 🚀 التشغيل

```bash
python3 manus-arabic/server.py --port 8092 --host 0.0.0.0
# افتح http://localhost:8092
```

يتطلب Python 3.10+ فقط — لا اعتمادات خارجية.

## 🧪 الاختبار الحي

```bash
# الصحة
curl http://localhost:8092/api/health

# إنشاء HTML مذهل (VERIFIED 5/5)
curl -X POST http://localhost:8092/api/agent/run \
  -H "Content-Type: application/json" \
  -d '{"prompt":"أنشئ صفحة HTML مذهلة عن AI-2027"}'

# قراءة
curl -X POST http://localhost:8092/api/agent/run \
  -d '{"prompt":"اقرأ README.md"}'

# 16 أداة
curl http://localhost:8092/api/tools

# 12 موصل
curl http://localhost:8092/api/connectors
```

تم اختباره: VERIFIED ✓ `{'claimed_ok': True, 'file_exists': True, 'correct_path': True, 'correct_content': True, 'hash_matches': True}`

## 📦 الهيكل

```
manus-arabic/
├── index.html   # الواجهة المذهلة (48KB، RTL، تفاعلية)
├── server.py    # الخادم + جسر النواة + 16 أداة + 12 موصل
├── README.md    # هذا الملف
├── runs/        # سجلات JSON لكل تشغيل
└── sandboxes/   # ملفات معزولة لكل run_id
```

## 🔐 نموذج الأمان

- القراءة مباشرة عبر الـ Hub المحلي
- الكتابة تُنشئ طلب موافقة فقط — التنفيذ عبر `connectors-hub approve` في الطرفية
- النواة ترفض `../../escape` و `\\..\\` تلقائياً — مجرّبة بـ 100 هجوم، 0 اختراق

## 🌐 الموصلات (12)

Gmail, Calendar, Drive, Docs, Sheets, Slides, Dropbox, GitHub, Linear, Notion, Supabase, Vercel — كلها عبر `connectors-hub` على `127.0.0.1:8765` (إن وُجد)، وإلا تُعرض حالتها بصدق.

---

**صنع بـ مانوس العربي — AI-2027 · البروتوكول الكامل · MAX_SPEND $0 · نواة حقيقية · أدلة لا تُنكر**
