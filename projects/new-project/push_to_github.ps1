# 🚀 PowerShell سكريبت لرفع المشروع إلى GitHub
# Quick Script to Push Project to GitHub

Write-Host "`n╔════════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║                                                                    ║" -ForegroundColor Cyan
Write-Host "║   🚀 سكريبت رفع المشروع إلى GitHub - GitHub Push Script        ║" -ForegroundColor Cyan
Write-Host "║                                                                    ║" -ForegroundColor Cyan
Write-Host "║   Ultimate BitNet Smart System v2.0.0                            ║" -ForegroundColor Cyan
Write-Host "║                                                                    ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════════════╝`n" -ForegroundColor Cyan

Write-Host "الرابط: https://github.com/sayedelazameydesign-crypto/new-project" -ForegroundColor Green
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Gray
Write-Host ""

# الخطوة 1: التحقق من Git
Write-Host "[1/10] التحقق من Git..." -ForegroundColor Yellow
try {
    git --version 2>&1 | Out-Null
    Write-Host "✓ Git مثبت بنجاح" -ForegroundColor Green
} catch {
    Write-Host "✗ Git غير مثبت!" -ForegroundColor Red
    Write-Host "قم بتثبيته من: https://git-scm.com/download/win" -ForegroundColor Red
    Read-Host "اضغط Enter للخروج"
    exit 1
}
Write-Host ""

# الخطوة 2: تكوين Git
Write-Host "[2/10] تكوين Git..." -ForegroundColor Yellow
$email = Read-Host "أدخل بريدك الإلكتروني"
git config --global user.email $email
Write-Host "✓ تم تعيين البريد: $email" -ForegroundColor Green

$username = Read-Host "أدخل اسم مستخدمك"
git config --global user.name $username
Write-Host "✓ تم تعيين الاسم: $username" -ForegroundColor Green
Write-Host ""

# الخطوة 3: الانتقال إلى مجلد المشروع
Write-Host "[3/10] الانتقال إلى مجلد المشروع..." -ForegroundColor Yellow
$projectPath = Split-Path -Parent $MyInvocation.MyCommandPath
Set-Location $projectPath
Write-Host "✓ المجلد الحالي: $(Get-Location)" -ForegroundColor Green
Write-Host ""

# الخطوة 4: تهيئة Git
Write-Host "[4/10] تهيئة Git Repository..." -ForegroundColor Yellow
if (Test-Path ".git") {
    Write-Host "⚠ Repository موجود بالفعل! سيتم تحديثه..." -ForegroundColor Yellow
} else {
    git init
    Write-Host "✓ تم إنشاء Git Repository" -ForegroundColor Green
}
Write-Host ""

# الخطوة 5: إضافة الملفات
Write-Host "[5/10] إضافة الملفات..." -ForegroundColor Yellow
git add .
Write-Host "✓ تم إضافة جميع الملفات" -ForegroundColor Green
Write-Host ""

# الخطوة 6: عرض الحالة
Write-Host "[6/10] عرض حالة المشروع..." -ForegroundColor Yellow
git status
Write-Host ""

# الخطوة 7: إنشاء Commit
Write-Host "[7/10] إنشاء Commit..." -ForegroundColor Yellow
$commitMsg = "🚀 Initial commit: Ultimate BitNet Smart System v2.0.0"
git commit -m $commitMsg
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ تم إنشاء Commit بنجاح" -ForegroundColor Green
} else {
    Write-Host "⚠ لا يوجد تغييرات جديدة" -ForegroundColor Yellow
}
Write-Host ""

# الخطوة 8: تسمية الفرع الرئيسي
Write-Host "[8/10] تسمية الفرع الرئيسي..." -ForegroundColor Yellow
git branch -M main
Write-Host "✓ تم تسمية الفرع بـ: main" -ForegroundColor Green
Write-Host ""

# الخطوة 9: إضافة Remote
Write-Host "[9/10] إضافة GitHub Remote..." -ForegroundColor Yellow
git remote remove origin 2>&1 | Out-Null
git remote add origin https://github.com/sayedelazameydesign-crypto/new-project.git
Write-Host "✓ تم إضافة Remote بنجاح" -ForegroundColor Green
Write-Host ""

# الخطوة 10: الرفع إلى GitHub
Write-Host "[10/10] رفع المشروع إلى GitHub..." -ForegroundColor Yellow
Write-Host "⏳ جاري الرفع... (قد يطلب منك كلمة المرور أو Token)" -ForegroundColor Cyan
Write-Host ""

git push -u origin main

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "⚠ حدثت مشكلة في الرفع" -ForegroundColor Yellow
    $force = Read-Host "هل تريد محاولة رفع بقوة (--force)؟ (نعم/لا)"
    if ($force -eq "نعم" -or $force -eq "yes") {
        git push -u origin main --force
        Write-Host "✓ تم الرفع بقوة" -ForegroundColor Green
    }
} else {
    Write-Host "✓ تم رفع المشروع بنجاح!" -ForegroundColor Green
}
Write-Host ""

# النتيجة النهائية
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Gray
Write-Host ""
Write-Host "✅ انتهى التشغيل!" -ForegroundColor Green
Write-Host ""
Write-Host "📍 اذهب إلى هذا الرابط للتحقق:" -ForegroundColor Cyan
Write-Host "https://github.com/sayedelazameydesign-crypto/new-project" -ForegroundColor Green
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Gray
Write-Host ""

Read-Host "اضغط Enter للخروج"
