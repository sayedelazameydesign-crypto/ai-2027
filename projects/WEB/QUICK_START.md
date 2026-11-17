# AI Providers System - Quick Start Guide

**© 2026 SAYED_ELAZAMEY - All Rights Reserved**  
**PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED**

---

## 🚀 تشغيل سريع (Quick Start)

### الطريقة الأولى: تشغيل مباشر
```bash
python simple_server.py
```

### الطريقة الثانية: ملف التشغيل
```bash
START_SIMPLE.bat
```

---

## 🌐 الوصول للنظام

بعد التشغيل، يمكنك الوصول للنظام عبر:

- **الواجهة الرئيسية**: http://localhost:8001
- **توثيق API**: http://localhost:8001/docs
- **فحص الصحة**: http://localhost:8001/v1/health

---

## ✨ المميزات المتاحة

### 🎯 الواجهة الرئيسية
- لوحة تحكم تفاعلية باللغة العربية
- إحصائيات النظام في الوقت الفعلي
- مراقبة صحة الموفرين

### 🤖 توليد الكود
- دعم 8+ لغات برمجة
- أمثلة تفاعلية للكود
- واجهة سهلة الاستخدام

### 📊 التحليلات
- إحصائيات الاستخدام
- تتبع التكلفة
- تحليل الأداء

### ⚙️ الإعدادات
- إدارة مفاتيح API
- تكوين النظام
- خيارات الأمان

---

## 🔧 الموفرين المدعومين

1. **OpenAI** - GPT-4, GPT-3.5-turbo
2. **Anthropic** - Claude-3 Sonnet, Haiku
3. **Google** - Gemini Pro, Vision
4. **DeepSeek** - DeepSeek Coder, Chat
5. **Groq** - Llama-3.1, Mixtral
6. **HuggingFace** - Meta-Llama, DialoGPT
7. **OpenRouter** - Multiple Models

---

## 🧪 اختبار النظام

### اختبار سريع عبر API:
```bash
curl -X POST "http://localhost:8001/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "اختبار النظام"}],
    "model": "gpt-4"
  }'
```

### اختبار عبر الواجهة:
1. افتح http://localhost:8001
2. انتقل لتبويب "توليد الكود"
3. اكتب "اختبار النظام" في المربع
4. اضغط "توليد الكود"

---

## 📋 أمثلة للاستخدام

### توليد كود Python:
```
اكتب دالة Python لحساب فيبوناتشي
```

### توليد كود JavaScript:
```
اكتب دالة JavaScript للتحقق من صحة البريد الإلكتروني
```

### إنشاء صفحة HTML:
```
اكتب صفحة HTML بسيطة باللغة العربية
```

---

## 🛠️ استكشاف الأخطاء

### المنفذ مستخدم:
إذا كان المنفذ 8001 مستخدماً، عدّل الرقم في `simple_server.py`:
```python
port=8002  # أو أي رقم آخر
```

### مشاكل التبعيات:
```bash
pip install fastapi uvicorn pydantic
```

### مشاكل الواجهة:
تأكد من وجود مجلد `static` مع الملفات:
- `index.html`
- `styles.css`
- `script.js`

---

## 📞 الدعم والتواصل

**المطور**: SAYED_ELAZAMEY  
**البريد الإلكتروني**: sayedelazameydesign@gmail.com  
**الهاتف**: 01126212452  

---

## 📄 الترخيص

هذا البرنامج محمي بحقوق الطبع والنشر. الاستخدام غير المصرح به محظور.

**© 2026 SAYED_ELAZAMEY - جميع الحقوق محفوظة**

---

## 🎉 استمتع بالاستخدام!

النظام جاهز للاستخدام الفوري مع جميع المميزات الأساسية.
للحصول على المميزات المتقدمة، يرجى التواصل للحصول على الترخيص الكامل.