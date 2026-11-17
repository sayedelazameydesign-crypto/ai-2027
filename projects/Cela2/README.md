# دليل بناء وتطوير CeliaOS Browser Agent (2026)

## 1. الهيكل التنظيمي للمشروع
قم بإنشاء مجلد المشروع باسم CeliaBrowserAgent وداخله الملفات التالية:
```text
CeliaBrowserAgent/
├── app.py              # واجهة المستخدم (Streamlit)
├── orchestrator.py     # منطق الوكيل (LangGraph & Browser-use)
├── storage_manager.py  # أرشفة البيانات (Google Drive Integration)
├── requirements.txt    # قائمة المكتبات
└── packages.txt        # تعريفات النظام (للتشغيل السحابي)
```

## 2. خطوات التشغيل (من الصفر)
### الخطوة الأولى: التجهيز المحلي
1. افتحي Terminal على الكمبيوتر.
2. انتقلي للمجلد: `cd CeliaBrowserAgent`
3. ثبّتي المتطلبات: `pip install -r requirements.txt`
4. ثبّتي المتصفح: `playwright install chromium`

### الخطوة الثانية: الإطلاق (Deployment)
1. ارفعي المجلد بالكامل إلى مستودع جديد على **GitHub**.
2. توجهي إلى Streamlit Cloud.
3. اربطي حساب GitHub الخاص بك واختاري مستودع CeliaBrowserAgent.
4. في إعدادات التطبيق (Advanced Settings)، تأكدي من ضبط Environment Variables لوضع GOOGLE_API_KEY (لزيادة الأمان).
5. اضغطي **Deploy**.

**ملاحظة تقنية:** هذا النظام مصمم ليقوم بـ Auto-Scaling من خلال Streamlit Cloud، مما يعني أنكِ لن تحتاجي لفتح الكمبيوتر الشخصي لتشغيل الوكيل بعد الآن؛ سيكون متاحاً لكِ عبر رابط مباشر من أي مكان.
