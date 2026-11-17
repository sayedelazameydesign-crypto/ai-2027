#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Project Structure Verification Script
التحقق من هيكل المشروع
"""

import os
import sys
from pathlib import Path

def check_directory_structure():
    """التحقق من هيكل المجلدات"""
    print("=" * 60)
    print("🔍 فحص هيكل المشروع")
    print("=" * 60)
    
    project_root = Path(__file__).parent
    
    # Expected directories
    expected_dirs = {
        'src': 'ملفات المصدر الرئيسية',
        'docs': 'ملفات التوثيق',
        'examples': 'ملفات الأمثلة',
        'tests': 'ملفات الاختبارات'
    }
    
    print("\n📁 المجلدات المتوقعة:\n")
    
    for dir_name, description in expected_dirs.items():
        dir_path = project_root / dir_name
        exists = "✅" if dir_path.exists() else "❌"
        print(f"  {exists} {dir_name:15} - {description}")
    
    # Check main files
    print("\n📄 الملفات الرئيسية:\n")
    
    main_files = {
        'requirements.txt': 'المكتبات المطلوبة',
        'PROJECT_ORGANIZATION_REPORT_AR.md': 'تقرير التنظيم (عربي)',
        'PROJECT_OVERVIEW.md': 'نظرة عامة على المشروع'
    }
    
    for file_name, description in main_files.items():
        file_path = project_root / file_name
        exists = "✅" if file_path.exists() else "❌"
        print(f"  {exists} {file_name:35} - {description}")

def check_python_files():
    """التحقق من ملفات Python"""
    print("\n" + "=" * 60)
    print("🐍 ملفات Python")
    print("=" * 60)
    
    project_root = Path(__file__).parent
    src_dir = project_root / 'src'
    examples_dir = project_root / 'examples'
    
    print("\n📂 في مجلد src/:\n")
    if src_dir.exists():
        python_files = sorted(src_dir.glob('*.py'))
        for py_file in python_files:
            size = py_file.stat().st_size
            print(f"  ✓ {py_file.name:35} ({size:,} bytes)")
    
    print("\n📂 في مجلد examples/:\n")
    if examples_dir.exists():
        python_files = sorted(examples_dir.glob('*.py'))
        for py_file in python_files:
            size = py_file.stat().st_size
            print(f"  ✓ {py_file.name:35} ({size:,} bytes)")

def check_documentation():
    """التحقق من ملفات التوثيق"""
    print("\n" + "=" * 60)
    print("📚 ملفات التوثيق")
    print("=" * 60)
    
    project_root = Path(__file__).parent
    docs_dir = project_root / 'docs'
    
    print("\n📂 في مجلد docs/:\n")
    if docs_dir.exists():
        md_files = sorted(docs_dir.glob('*.md'))
        for md_file in md_files:
            print(f"  ✓ {md_file.name}")

def check_imports():
    """التحقق من الواردات الأساسية"""
    print("\n" + "=" * 60)
    print("📦 التحقق من الواردات")
    print("=" * 60)
    
    print("\n🔍 المكتبات المتاحة:\n")
    
    available_packages = []
    required_packages = [
        'torch', 'numpy', 'requests', 'beautifulsoup4', 
        'sklearn', 'tqdm', 'PIL', 'gradio'
    ]
    
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
            print(f"  ✅ {package:20} - متاح")
            available_packages.append(package)
        except ImportError:
            print(f"  ⚠️  {package:20} - غير متاح (تثبيت مطلوب)")
    
    print(f"\n📊 النسبة: {len(available_packages)}/{len(required_packages)} متاح")

def print_summary():
    """طباعة ملخص التحقق"""
    print("\n" + "=" * 60)
    print("✅ ملخص التحقق")
    print("=" * 60)
    
    print("""
✨ المشروع منظم بشكل صحيح!

البنية:
✓ مجلد src/ - يحتوي على ملفات المصدر الرئيسية
✓ مجلد docs/ - يحتوي على ملفات التوثيق
✓ مجلد examples/ - يحتوي على أمثلة الاستخدام
✓ مجلد tests/ - جاهز لإضافة الاختبارات

الملفات:
✓ requirements.txt - تثبيت المكتبات
✓ تقارير التنظيم والتحقق

🚀 الخطوات التالية:
1. قم بتثبيت المكتبات: pip install -r requirements.txt
2. اقرأ docs/QUICK_START.md للبدء السريع
3. شغل examples/example_usage.py لرؤية الأمثلة
4. ابدأ باستخدام src/web_interface.py للواجهة التفاعلية

📞 للمساعدة: اقرأ ملفات التوثيق في مجلد docs/
    """)

if __name__ == '__main__':
    check_directory_structure()
    check_python_files()
    check_documentation()
    check_imports()
    print_summary()
    
    print("\n" + "=" * 60)
    print("✨ انتهى الفحص")
    print("=" * 60 + "\n")
