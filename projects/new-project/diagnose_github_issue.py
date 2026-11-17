#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🔍 سكريبت تشخيص مشاكل رفع GitHub
GitHub Push Issue Diagnostic Script
"""

import os
import subprocess
import sys
from pathlib import Path

def run_command(cmd, show_output=True):
    """تشغيل أمر والحصول على النتيجة"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        if show_output and result.stdout:
            print(result.stdout)
        if result.stderr and show_output:
            print(f"⚠️  {result.stderr}", file=sys.stderr)
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        print(f"❌ خطأ في تشغيل الأمر: {e}")
        return False, "", str(e)

def main():
    print("\n" + "="*70)
    print("🔍 تشخيص مشاكل رفع GitHub - GitHub Push Issue Diagnostics")
    print("="*70 + "\n")
    
    project_path = Path.cwd()
    print(f"📍 مسار المشروع: {project_path}\n")
    
    # 1. التحقق من Git
    print("[1/8] ✓ التحقق من Git...")
    success, output, error = run_command("git --version")
    if not success:
        print("❌ Git غير مثبت!")
        print("📥 قم بتثبيته من: https://git-scm.com/download/win")
        return
    print(f"✅ {output.strip()}\n")
    
    # 2. التحقق من إعدادات Git
    print("[2/8] ✓ إعدادات Git...")
    success, output, error = run_command("git config --global user.email", show_output=False)
    email = output.strip()
    success, output, error = run_command("git config --global user.name", show_output=False)
    name = output.strip()
    print(f"👤 الاسم: {name}")
    print(f"📧 البريد: {email}\n")
    
    if not email or not name:
        print("⚠️  لم يتم تكوين Git بشكل صحيح!")
        print("🔧 قم بتشغيل هذه الأوامر:")
        print('   git config --global user.email "your-email@example.com"')
        print('   git config --global user.name "Your Name"\n')
    
    # 3. التحقق من Repository
    print("[3/8] ✓ التحقق من Git Repository...")
    if not os.path.exists(".git"):
        print("❌ لا يوجد repository محلي!")
        print("🔧 قم بتشغيل: git init\n")
        return
    print("✅ Repository موجود\n")
    
    # 4. حالة Git
    print("[4/8] ✓ حالة المشروع...")
    run_command("git status")
    print()
    
    # 5. التحقق من Remote
    print("[5/8] ✓ التحقق من Remote...")
    success, output, error = run_command("git remote -v", show_output=False)
    if not output.strip():
        print("❌ لا يوجد Remote مضاف!")
        print("🔧 قم بتشغيل:")
        print("   git remote add origin https://github.com/sayedelazameydesign-crypto/new-project.git\n")
        return
    print(output)
    
    # 6. التحقق من Branches
    print("[6/8] ✓ التحقق من Branches...")
    run_command("git branch -a")
    print()
    
    # 7. محاولة رفع مع تفاصيل
    print("[7/8] 🚀 محاولة الرفع...")
    print("⏳ جاري الرفع... (مع ظهور التفاصيل)\n")
    
    success, output, error = run_command(
        "git push -u origin main -v",
        show_output=True
    )
    
    if success:
        print("\n✅ ✅ ✅ تم الرفع بنجاح! ✅ ✅ ✅\n")
        print("[8/8] ✓ التحقق على GitHub...")
        print("🌐 اذهب إلى: https://github.com/sayedelazameydesign-crypto/new-project")
        print("📌 تحقق من أن الملفات موجودة على الفرع main\n")
    else:
        print("\n❌ فشل الرفع\n")
        print("⚠️  رسالة الخطأ:")
        if error:
            print(error)
        if output:
            print(output)
        
        print("\n" + "="*70)
        print("🔧 الحلول المحتملة:")
        print("="*70 + "\n")
        
        print("1️⃣  مشكلة بالمصادقة (Authentication):")
        print("   • قد تحتاج إلى GitHub Personal Access Token")
        print("   • اذهب إلى: https://github.com/settings/tokens")
        print("   • أنشئ token جديد باسم 'push_project'")
        print("   • استخدمه بدلاً من كلمة المرور\n")
        
        print("2️⃣  مشكلة في الفرع (Branch):")
        print("   حاول هذه الأوامر:")
        print("   $ git branch -M main  # تأكد من اسم الفرع")
        print("   $ git push -u origin main --force  # رفع بقوة\n")
        
        print("3️⃣  مشكلة في الاتصال:")
        print("   • تحقق من الإنترنت")
        print("   • حاول: ping google.com\n")
        
        print("4️⃣  مشكلة في حجم الملفات:")
        print("   • قد تكون ملفات النموذج (models/*.pt) كبيرة جداً")
        print("   • تأكد من وجود .gitignore\n")
        
        print("5️⃣  جرب هذا الأمر للحصول على معلومات أكثر:")
        print("   $ git push -u origin main 2>&1 | tee push_error.log\n")

if __name__ == "__main__":
    main()
    print("="*70)
    print("انتهى التشخيص")
    print("="*70 + "\n")
