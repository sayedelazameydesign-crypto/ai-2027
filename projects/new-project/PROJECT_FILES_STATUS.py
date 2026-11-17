#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
📋 تقرير حالة الملفات - Project Files Status Report
Ultimate BitNet Smart System v2.0.0
"""

import os
from pathlib import Path
from datetime import datetime

def check_files():
    """فحص الملفات الموجودة والناقصة"""
    
    project_path = Path(".")
    
    # الملفات والمجلدات المتوقعة
    expected_structure = {
        "🔧 ملفات الإعدادات Core Configuration": [
            ".env",
            ".env.example",
            ".gitignore",
            "pyproject.toml",
            "pyrightconfig.json",
            "requirements.txt",
        ],
        
        "📁 المجلدات الرئيسية Main Directories": [
            "src/",
            "docs/",
            "examples/",
            "tests/",
            "models/",
            "knowledge_base/",
            "logs/",
            "cache/",
            "backup/",
            "static/",
        ],
        
        "🤖 ملفات المساعد الذكي Smart Assistant": [
            "src/smart_assistant.py",
            "src/enhanced_assistant.py",
            "src/advanced_assistant.py",
            "src/ultimate_bitnet_improved.py",
        ],
        
        "🔗 ملفات التكامل Integration Files": [
            "src/unified_system.py",
            "src/social_media_integration.py",
            "src/github_integration.py",
            "src/complete_social_media_system.py",
        ],
        
        "🖥️ واجهات الويب Web Interfaces": [
            "src/web_interface.py",
            "src/enhanced_web_interface.py",
            "src/advanced_web_interface.py",
        ],
        
        "📚 ملفات التوثيق Documentation": [
            "README.md",
            "docs/QUICK_START.md",
            "docs/ADVANCED_FEATURES_GUIDE.md",
            "docs/SOCIAL_MEDIA_SYSTEM_GUIDE.md",
        ],
        
        "🚀 ملفات التشغيل Runner Scripts": [
            "run_project.py",
            "run_unified_system.py",
            "push_to_github.bat",
            "push_to_github.ps1",
        ],
        
        "🧪 ملفات الاختبار Test Files": [
            "tests/__init__.py",
            "test_project.py",
            "test_assistant.py",
        ],
        
        "📊 ملفات التقارير Report Files": [
            "GITHUB_PUSH_GUIDE.py",
            "FINAL_PROJECT_REPORT.py",
            "PROJECT_FEATURES_DETAILED.py",
        ],
    }
    
    # البيانات
    print("\n" + "="*80)
    print("📋 تقرير حالة الملفات والمجلدات - PROJECT FILES STATUS REPORT")
    print("="*80 + "\n")
    
    print(f"🕐 التاريخ والوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📍 مسار المشروع: {project_path.absolute()}\n")
    
    total_expected = 0
    total_found = 0
    total_missing = 0
    
    results = {}
    
    for category, files in expected_structure.items():
        print(f"\n{'─'*80}")
        print(f"{category}")
        print(f"{'─'*80}")
        
        category_found = 0
        category_missing = 0
        missing_list = []
        
        for file_path in files:
            total_expected += 1
            full_path = project_path / file_path
            
            if full_path.exists():
                total_found += 1
                category_found += 1
                
                # الحصول على معلومات الملف
                if full_path.is_file():
                    size = full_path.stat().st_size
                    size_kb = size / 1024
                    size_str = f"{size_kb:.1f} KB" if size_kb > 0 else "فارغ"
                    print(f"  ✅ {file_path:40} | {size_str:>15}")
                else:
                    print(f"  ✅ {file_path:40} | [مجلد] [Directory]")
            else:
                total_missing += 1
                category_missing += 1
                missing_list.append(file_path)
                print(f"  ❌ {file_path:40} | ⚠️  MISSING")
        
        results[category] = {
            "found": category_found,
            "missing": category_missing,
            "missing_list": missing_list
        }
        
        print(f"\n  📊 النتيجة: {category_found} موجود، {category_missing} ناقص")
    
    # الملخص
    print("\n" + "="*80)
    print("📊 الملخص - SUMMARY")
    print("="*80 + "\n")
    
    print(f"✅ الملفات الموجودة: {total_found}/{total_expected}")
    print(f"❌ الملفات الناقصة: {total_missing}/{total_expected}")
    print(f"📈 نسبة الاكتمال: {(total_found/total_expected)*100:.1f}%\n")
    
    # قائمة الملفات الناقصة
    print("="*80)
    print("❌ قائمة الملفات الناقصة - MISSING FILES LIST")
    print("="*80 + "\n")
    
    if total_missing > 0:
        missing_count = 1
        for category, data in results.items():
            if data["missing_list"]:
                print(f"\n{category}:")
                for file_path in data["missing_list"]:
                    print(f"  {missing_count}. {file_path}")
                    missing_count += 1
        
        print(f"\n📌 الملاحظات:")
        print(f"  • إجمالي الملفات الناقصة: {total_missing} ملف")
        print(f"  • يجب إنشاء هذه الملفات لضمان سير المشروع بشكل صحيح")
    else:
        print("✅ جميع الملفات المتوقعة موجودة!")
    
    # التوصيات
    print("\n" + "="*80)
    print("🛠️  التوصيات - RECOMMENDATIONS")
    print("="*80 + "\n")
    
    if not (project_path / ".env").exists():
        print("1. ❌ ملف .env ناقص - CRITICAL")
        print("   █ يجب إنشاء ملف .env بمحتويات API keys والإعدادات")
        print("   █ استخدم: python create_env_file.py\n")
    
    if not (project_path / "models").exists():
        print("2. ⚠️  مجلد models ناقص")
        print("   █ يجب إنشاء: mkdir models")
        print("   █ ضع ملفات النموذج (*.pt) فيه\n")
    
    if not (project_path / "knowledge_base").exists():
        print("3. ⚠️  مجلد knowledge_base ناقص")
        print("   █ يجب إنشاء: mkdir knowledge_base")
        print("   █ ضع قاعدة البيانات والملفات المعرفية فيه\n")
    
    if not (project_path / "tests/__init__.py").exists() or not (project_path / "tests").is_dir():
        print("4. ⚠️  مجلد tests غير مكتمل")
        print("   █ يجب إنشاء: mkdir -p tests")
        print("   █ أضف __init__.py داخل المجلد\n")
    
    if not (project_path / "logs").exists():
        print("5. ⚠️  مجلد logs ناقص")
        print("   █ يجب إنشاء: mkdir logs")
        print("   █ سيتم حفظ السجلات هنا\n")
    
    if not (project_path / ".env.example").exists():
        print("6. ⚠️  ملف .env.example ناقص")
        print("   █ نسخة المثال من ملف .env")
        print("   █ يساعد المطورين الجدد في الفهم\n")
    
    # ملفات إضافية موجودة
    print("\n" + "="*80)
    print("📦 ملفات إضافية وجدت في المشروع - ADDITIONAL FILES FOUND")
    print("="*80 + "\n")
    
    # عد الملفات في المشروع
    all_files = list(project_path.rglob("*"))
    py_files = [f for f in all_files if f.suffix == ".py" and "pycache" not in str(f)]
    doc_files = [f for f in all_files if f.suffix in [".md", ".txt"]]
    
    print(f"📄 ملفات Python: {len(py_files)} ملف")
    print(f"📚 ملفات التوثيق: {len(doc_files)} ملف")
    print(f"📁 المجلدات: {len([d for d in all_files if d.is_dir()])}")
    print(f"📊 إجمالي الملفات: {len([f for f in all_files if f.is_file()])}")
    
    print("\n" + "="*80)
    print("✨ انتهى التقرير - REPORT COMPLETE")
    print("="*80 + "\n")

if __name__ == "__main__":
    check_files()
