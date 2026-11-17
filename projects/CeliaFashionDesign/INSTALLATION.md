# 🎯 دليل التثبيت النهائي - CELIA Fashion Design

## ✅ تم إنشاء جميع الملفات بنجاح!

### الملفات الأساسية المُنشأة:

```
✅ README.md
✅ LICENSE
✅ .gitignore
✅ PROJECT-INDEX.md

✅ infrastructure/
   ✅ docker-compose.yml (الملف الرئيسي للنشر)
   ✅ .env.example (قالب متغيرات البيئة)
   ✅ traefik/traefik.yml
   ✅ vault/config/vault.hcl
   ✅ monitoring/prometheus.yml
   ✅ init-scripts/01-init.sql
   ✅ n8n-workflows/facebook-sync.json
   ✅ scripts/setup.sh (قابل للتنفيذ)
   ✅ scripts/backup.sh (قابل للتنفيذ)

✅ ai-core/
   ✅ Dockerfile
   ✅ requirements.txt
   ✅ src/main.py (AL-Agent الكامل)

✅ docs/
   ✅ DEPLOYMENT.md (دليل النشر الكامل)
   ✅ QUICK-START.md (البدء السريع)
   ✅ RESOURCES.md (توزيع الموارد)
   ✅ FAQ.md (100+ سؤال وجواب)
```

## 🚀 خطوات التثبيت (نسخ ولصق)

### الخطوة 1: على جهازك المحلي

```bash
# 1. تحميل المشروع
cd ~
# إذا كنت تقرأ هذا الملف، فالملفات موجودة بالفعل في:
# /home/claude/celia-fashion-design

# 2. إنشاء مستودع Git (اختياري)
cd /home/claude/celia-fashion-design
git init
git add .
git commit -m "Initial commit: CELIA Fashion Design v1.0"
```

### الخطوة 2: رفع للخادم

```bash
# من جهازك المحلي إلى Oracle Cloud
# استبدل YOUR_ORACLE_IP بعنوان IP الخاص بك

# نقل جميع الملفات
scp -r /home/claude/celia-fashion-design ubuntu@YOUR_ORACLE_IP:/tmp/

# ثم على الخادم:
ssh ubuntu@YOUR_ORACLE_IP

# نقل للمجلد النهائي
sudo mv /tmp/celia-fashion-design /opt/celia
sudo chown -R ubuntu:ubuntu /opt/celia
```

### الخطوة 3: على خادم Oracle Cloud

```bash
# 1. تشغيل سكريبت الإعداد
cd /opt/celia/infrastructure/scripts
sudo ./setup.sh

# انتظر حتى ينتهي (10-15 دقيقة)

# 2. إعادة تشغيل
sudo reboot

# 3. إعادة الاتصال بعد دقيقة
ssh ubuntu@YOUR_ORACLE_IP
```

### الخطوة 4: إعداد متغيرات البيئة

```bash
cd /opt/celia/infrastructure

# 1. نسخ ملف البيئة
cp .env.example .env

# 2. توليد الأسرار
echo "=== توليد أسرار عشوائية ==="
for i in {1..5}; do 
    echo "Key $i: $(openssl rand -base64 32)"
done

# 3. تحرير الملف
nano .env

# املأ القيم التالية على الأقل:
# - DOMAIN=your-domain.com
# - DB_PASSWORD=<من الأسرار المولدة>
# - DIRECTUS_KEY=<من الأسرار المولدة>
# - DIRECTUS_SECRET=<من الأسرار المولدة>
# - N8N_ENCRYPTION_KEY=<من الأسرار المولدة>
# - ADMIN_EMAIL=admin@your-domain.com
# - ADMIN_PASSWORD=<كلمة سر قوية>
# - CF_EMAIL=your-cloudflare-email
# - CF_API_TOKEN=your-cloudflare-token
```

### الخطوة 5: إعداد DNS (في Cloudflare)

```
أضف السجلات التالية:

Type    Name                Content             Proxy
A       @                   YOUR_ORACLE_IP      Proxied
A       *.your-domain.com   YOUR_ORACLE_IP      DNS only
```

### الخطوة 6: النشر التدريجي

```bash
cd /opt/celia/infrastructure

# Phase 1: قاعدة البيانات والأساسيات
echo "=== Phase 1: Database & Core ==="
docker-compose up -d postgres redis traefik
sleep 30
docker-compose ps

# Phase 2: AI Layer (سيستغرق 15 دقيقة لتحميل النماذج)
echo "=== Phase 2: AI Layer ==="
docker-compose up -d ollama qdrant

# متابعة تحميل النماذج
echo "=== انتظر حتى تحميل النماذج (10-15 دقيقة) ==="
docker-compose logs -f ollama

# عندما ترى "Models loaded successfully"، اضغط Ctrl+C

# Phase 3: AL-Agent
echo "=== Phase 3: AL-Agent ==="
docker-compose up -d --build al-agent

# Phase 4: باقي الخدمات
echo "=== Phase 4: Services ==="
docker-compose up -d directus n8n grafana prometheus

# التحقق النهائي
echo "=== Final Check ==="
docker-compose ps
```

### الخطوة 7: التحقق من النشر

```bash
# اختبار الخدمات الأساسية
echo "=== Testing Services ==="

# AL-Agent Health
curl https://api.your-domain.com/health

# Directus Health
curl https://cms.your-domain.com/server/health

# اختبار محادثة
curl -X POST https://api.your-domain.com/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "مرحباً، ما هي أحدث صيحات الموضة؟",
    "user_id": "test_user",
    "language": "ar"
  }' | jq
```

## ✨ الوصول للخدمات

بعد النشر الناجح، يمكنك الوصول إلى:

| الخدمة | الرابط | الاستخدام |
|--------|--------|-----------|
| AL-Agent API | `https://api.your-domain.com` | API الرئيسي |
| Directus CMS | `https://cms.your-domain.com` | إدارة المحتوى |
| N8n Automation | `https://n8n.your-domain.com` | الأتمتة |
| Grafana Dashboard | `https://monitor.your-domain.com` | المراقبة |
| Traefik Dashboard | `https://traefik.your-domain.com/dashboard/` | Reverse Proxy |

## 🔒 الأمان

### بعد النشر مباشرة:

```bash
# 1. تغيير كلمات السر الافتراضية (إذا لم تفعل)
nano /opt/celia/infrastructure/.env

# 2. توليد Traefik Basic Auth
sudo apt-get install -y apache2-utils
htpasswd -nb admin YOUR_STRONG_PASSWORD
# انسخ النتيجة وضعها في TRAEFIK_BASIC_AUTH في .env

# 3. إعادة تشغيل لتطبيق التغييرات
docker-compose up -d --force-recreate traefik
```

## 📊 المراقبة

### Dashboard Grafana:
1. افتح `https://monitor.your-domain.com`
2. تسجيل دخول: admin / (كلمة السر من .env)
3. استورد Dashboards من `/infrastructure/monitoring/grafana/`

### التحقق من الموارد:
```bash
# في أي وقت
docker stats --no-stream
free -h
df -h
```

## 🔄 الصيانة اليومية

```bash
# عرض الحالة
cd /opt/celia/infrastructure
docker-compose ps

# عرض السجلات
docker-compose logs -f al-agent

# إعادة تشغيل خدمة
docker-compose restart al-agent

# نسخ احتياطي
/opt/celia/infrastructure/scripts/backup.sh
```

## 🆘 استكشاف الأخطاء

### إذا حدثت مشكلة:

1. **راجع السجلات**:
```bash
docker-compose logs <service_name>
```

2. **راجع FAQ**:
```bash
cat /opt/celia/docs/FAQ.md
```

3. **إعادة تشغيل**:
```bash
docker-compose restart <service_name>
```

4. **إعادة نشر كامل** (الملاذ الأخير):
```bash
cd /opt/celia/infrastructure
docker-compose down
docker-compose up -d
```

## 📚 الموارد الإضافية

### للتوثيق الكامل:
```bash
# على الخادم
cd /opt/celia/docs
ls -la

# الملفات المتاحة:
# - DEPLOYMENT.md (دليل مفصل)
# - QUICK-START.md (أوامر سريعة)
# - RESOURCES.md (إدارة الموارد)
# - FAQ.md (أسئلة شائعة)
```

### الأوامر المفيدة:
```bash
# إنشاء aliases مفيدة
cat >> ~/.bashrc <<'EOF'
alias celia='cd /opt/celia/infrastructure'
alias celia-ps='cd /opt/celia/infrastructure && docker-compose ps'
alias celia-logs='cd /opt/celia/infrastructure && docker-compose logs -f'
alias celia-stats='docker stats --no-stream'
alias celia-backup='/opt/celia/infrastructure/scripts/backup.sh'
EOF

source ~/.bashrc

# الآن يمكنك استخدام:
celia-ps      # عرض الحالة
celia-logs    # عرض السجلات
celia-stats   # عرض الموارد
celia-backup  # نسخ احتياطي
```

## 🎉 تهانينا!

نظام CELIA Fashion Design الآن يعمل بالكامل!

### الخطوات التالية:
1. ✅ اختبر API عبر `https://api.your-domain.com/health`
2. ✅ سجل دخول إلى Directus وأنشئ أول محتوى
3. ✅ استورد N8n workflow للتكامل الاجتماعي
4. ✅ فعّل النسخ الاحتياطي التلقائي (cron job)
5. ✅ راقب الموارد في Grafana

### للدعم:
- 📖 راجع `/opt/celia/docs/`
- 💬 افتح issue على GitHub
- 🌐 انضم للمجتمع

---

**نظام CELIA جاهز للإنتاج! 🚀**

التكلفة: **$0/شهر**
القدرات: **إنتاج كامل**
السيادة على البيانات: **100%**
