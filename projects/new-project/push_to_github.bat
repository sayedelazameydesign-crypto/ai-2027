@echo off
REM 🚀 سكريبت سريع لرفع المشروع إلى GitHub
REM Quick Script to Push Project to GitHub

setlocal enabledelayedexpansion

REM تعيين الألوان
for /F %%A in ('copy /Z "%~f0" nul') do set "BS=%%A"

cls
echo.
echo ╔════════════════════════════════════════════════════════════════════╗
echo ║                                                                    ║
echo ║   🚀 سكريبت رفع المشروع إلى GitHub - GitHub Push Script        ║
echo ║                                                                    ║
echo ║   Ultimate BitNet Smart System v2.0.0                            ║
echo ║                                                                    ║
echo ╚════════════════════════════════════════════════════════════════════╝
echo.

echo الرابط: https://github.com/sayedelazameydesign-crypto/new-project
echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.

REM التحقق من Git
echo [1/10] التحقق من Git...
git --version >nul 2>&1
if errorlevel 1 (
    echo ✗ Git غير مثبت!
    echo   قم بتثبيته من: https://git-scm.com/download/win
    pause
    exit /b 1
)
echo ✓ Git مثبت بنجاح
echo.

REM تكوين Git
echo [2/10] تكوين Git...
set /p email="أدخل بريدك الإلكتروني: "
git config --global user.email "!email!"
echo ✓ تم تعيين البريد: !email!
echo.

set /p username="أدخل اسم مستخدمك: "
git config --global user.name "!username!"
echo ✓ تم تعيين الاسم: !username!
echo.

REM الانتقال إلى مجلد المشروع
echo [3/10] الانتقال إلى مجلد المشروع...
cd /d "%~dp0"
echo ✓ المجلد الحالي: %cd%
echo.

REM تهيئة Git
echo [4/10] تهيئة Git Repository...
if exist ".git" (
    echo ⚠ Repository موجود بالفعل!
    echo   سيتم تحديثه...
) else (
    git init
    echo ✓ تم إنشاء Git Repository
)
echo.

REM إضافة الملفات
echo [5/10] إضافة الملفات...
git add .
echo ✓ تم إضافة جميع الملفات
echo.

REM عرض الحالة
echo [6/10] عرض حالة المشروع...
git status
echo.

REM إنشاء Commit
echo [7/10] إنشاء Commit...
set commit_msg=🚀 Initial commit: Ultimate BitNet Smart System v2.0.0
git commit -m "!commit_msg!"
if errorlevel 1 (
    echo ⚠ لا يوجد تغييرات جديدة
) else (
    echo ✓ تم إنشاء Commit بنجاح
)
echo.

REM تسمية الفرع الرئيسي
echo [8/10] تسمية الفرع الرئيسي...
git branch -M main
echo ✓ تم تسمية الفرع بـ: main
echo.

REM إضافة Remote
echo [9/10] إضافة GitHub Remote...
git remote remove origin >nul 2>&1
git remote add origin https://github.com/sayedelazameydesign-crypto/new-project.git
echo ✓ تم إضافة Remote بنجاح
echo.

REM الرفع إلى GitHub
echo [10/10] رفع المشروع إلى GitHub...
echo ⏳ جاري الرفع... (قد يطلب منك كلمة المرور أو Token)
echo.
git push -u origin main
if errorlevel 1 (
    echo.
    echo ⚠ حدثت مشكلة في الرفع
    echo هل تريد محاولة رفع بقوة (--force)؟
    set /p force="أدخل yes أو no: "
    if /i "!force!"=="yes" (
        git push -u origin main --force
        echo ✓ تم الرفع بقوة
    )
) else (
    echo ✓ تم رفع المشروع بنجاح!
)
echo.

REM التحقق من النجاح
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
echo ✅ انتهى التشغيل!
echo.
echo 📍 اذهب إلى هذا الرابط للتحقق:
echo https://github.com/sayedelazameydesign-crypto/new-project
echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.

pause
