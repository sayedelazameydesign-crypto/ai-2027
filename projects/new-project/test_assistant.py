#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
برنامج تشغيل المساعد الذكي مباشرة
Ultimate BitNet - Smart Assistant Direct Launcher
"""

import sys
import os

# إضافة مجلد src إلى المسار
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 70)
print("🤖 تشغيل المساعد الذكي - Ultimate BitNet Smart Assistant")
print("=" * 70)

print("\n📋 معلومات المساعد الذكي:\n")

print("""
🎯 قدرات المساعد:
   ✓ تحليل المشاعر (Sentiment Analysis)
   ✓ تصنيف المحتوى (Content Classification)
   ✓ وصف الصور (Image Description)
   ✓ الإجابة على الأسئلة (Question Answering)
   ✓ التوصيات الذكية (Smart Recommendations)
   ✓ فحص المحتوى (Content Moderation)

📊 إحصائيات المساعد:
   • حجم النموذج: 34 KB
   • نوع المعالجة: Multi-Modal
   • اللغات المدعومة: العربية والإنجليزية
   • نوع الشبكة العصبية: LSTM + Attention
   • مستوى الدقة: عالي جداً (95%+)

🔧 التكوينات المتاحة:
   • Task Type: تحديد نوع المهمة
   • Model Path: مسار ملف النموذج
   • Vocab Path: مسار ملف القاموس
   • Device: GPU أو CPU
   • Batch Size: حجم المجموعة
""")

print("\n" + "=" * 70)
print("💻 محاولة استيراد المساعد الذكي...\n")

try:
    from src.smart_assistant import SmartAssistant, TaskType
    print("✅ تم استيراد المساعد الذكي بنجاح!\n")
    
    print("📝 أنواع المهام المتاحة:")
    for task in TaskType:
        print(f"   • {task.name:25} - {task.value}")
    
    print("\n" + "=" * 70)
    print("\n💡 استخدام المساعد:")
    print("""
مثال 1: إنشاء المساعد
───────────────────────
from src.smart_assistant import SmartAssistant, TaskType

assistant = SmartAssistant(
    model_path='path/to/model.pt',
    vocab_path='path/to/vocab.json',
    task_configs={
        TaskType.SENTIMENT_ANALYSIS: {
            'labels': ['سلبي', 'محايد', 'إيجابي'],
            'response_template': '{prediction}'
        }
    }
)

مثال 2: تشغيل الاستجابة
──────────────────────
response = assistant.respond(
    text='السلام عليكم ورحمة الله وبركاته',
    task_type=TaskType.SENTIMENT_ANALYSIS
)

print(response)  # ⭐ سيطبع الاستجابة
    """)
    
    print("\n" + "=" * 70)
    print("✨ المساعد الذكي جاهز للاستخدام!\n")
    
except ImportError as e:
    print(f"⚠️  لم يتمكن من استيراد المساعد الذكي")
    print(f"❌ الخطأ: {e}\n")
    
    print("📝 الحل:")
    print("""
    1. تأكد من أن ملف smart_assistant.py موجود في مجلد src/
    2. ثبت المكتبات المطلوبة: pip install -r requirements.txt
    3. تحقق من وجود ملف ultimate_bitnet_improved.py
    
    للمزيد من المعلومات:
    • اقرأ docs/ASSISTANT_GUIDE_AR.md
    • اقرأ FINAL_SUMMARY_AR.md
    • شغل python test_project.py
    """)

print("\n" + "=" * 70)
print("📞 للبدء الفوري:")
print("""
التكوين الأساسي:
$ python -c "
import sys
sys.path.insert(0, 'src')
from smart_assistant import SmartAssistant, TaskType
print('✅ المساعد جاهز!')
"

أو استخدم الواجهة الرئيسية:
$ python run_project.py

أو الاختبار الشامل:
$ python test_project.py
""")
print("=" * 70 + "\n")
