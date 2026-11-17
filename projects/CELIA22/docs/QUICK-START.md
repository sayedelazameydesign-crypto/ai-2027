# 📘 دليل الاستخدام السريع - CELIA Fashion Design

## 🚀 البدء السريع (5 دقائق)

### على الخادم الجديد

```bash
# 1. تشغيل سكريبت الإعداد
wget https://raw.githubusercontent.com/YOUR_ORG/celia/main/infrastructure/scripts/setup.sh
chmod +x setup.sh
sudo ./setup.sh

# 2. استنساخ المشروع
cd /opt/celia
git clone https://github.com/YOUR_ORG/celia-fashion-design.git .

# 3. إعداد البيئة
cd infrastructure
cp .env.example .env
nano .env  # عدل القيم

# 4. النشر
docker-compose up -d

# 5. انتظر حتى تحميل النماذج (15 دقيقة أول مرة)
docker-compose logs -f ollama
```

## 📋 الأوامر الأساسية

### إدارة الخدمات

```bash
cd /opt/celia/infrastructure

# تشغيل جميع الخدمات
docker-compose up -d

# إيقاف جميع الخدمات
docker-compose down

# إعادة تشغيل خدمة معينة
docker-compose restart al-agent

# عرض الحالة
docker-compose ps

# عرض السجلات
docker-compose logs -f al-agent

# عرض استهلاك الموارد
docker stats
```

### تحديث النظام

```bash
cd /opt/celia

# سحب آخر التحديثات
git pull origin main

# إعادة بناء ونشر
cd infrastructure
docker-compose pull
docker-compose up -d --build

# تنظيف الصور القديمة
docker system prune -f
```

### النسخ الاحتياطي واستعادة البيانات

```bash
# إنشاء نسخة احتياطية
/opt/celia/infrastructure/scripts/backup.sh

# استعادة من نسخة احتياطية
cd /opt/celia/backups
tar xzf 20240101_120000.tar.gz

# استعادة قاعدة البيانات
gunzip 20240101_120000/database.sql.gz
docker exec -i postgres psql -U celia celia_db < 20240101_120000/database.sql
```

## 🧪 اختبار النظام

### اختبار AL-Agent API

```bash
# Health check
curl https://api.celia.fashion/health | jq

# محادثة بسيطة
curl -X POST https://api.celia.fashion/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "ما هي ألوان الموضة لهذا الموسم؟",
    "user_id": "test_user",
    "language": "ar"
  }' | jq
```

### اختبار Directus

```bash
# Health check
curl https://cms.celia.fashion/server/health

# تسجيل دخول
curl -X POST https://cms.celia.fashion/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@celia.fashion",
    "password": "YOUR_PASSWORD"
  }'
```

## 🔍 المراقبة واستكشاف الأخطاء

### التحقق من صحة الخدمات

```bash
# عرض حالة جميع الحاويات
docker-compose ps

# التحقق من استهلاك الموارد
docker stats --no-stream

# التحقق من المساحة المتاحة
df -h

# التحقق من الذاكرة
free -h
```

### عرض السجلات

```bash
# آخر 50 سطر من AL-Agent
docker-compose logs --tail=50 al-agent

# متابعة سجلات Ollama
docker-compose logs -f ollama

# سجلات جميع الخدمات
docker-compose logs --tail=100

# سجلات مع الطوابع الزمنية
docker-compose logs -t al-agent
```

### حل المشاكل الشائعة

#### المشكلة: Ollama بطيء جداً

```bash
# التحقق من استهلاك الذاكرة
docker stats ollama

# إعادة تشغيل مع تحديد الموارد
docker-compose restart ollama
```

#### المشكلة: قاعدة البيانات ممتلئة

```bash
# التحقق من حجم البيانات
docker exec postgres psql -U celia -d celia_db -c "
  SELECT pg_size_pretty(pg_database_size('celia_db'));"

# تنظيف السجلات القديمة (أكثر من 30 يوم)
docker exec postgres psql -U celia -d celia_db -c "
  DELETE FROM conversations 
  WHERE created_at < NOW() - INTERVAL '30 days';"
```

#### المشكلة: SSL لا يعمل

```bash
# التحقق من شهادات Traefik
docker-compose logs traefik | grep -i acme

# حذف الشهادات وإعادة التوليد
docker-compose exec traefik rm /letsencrypt/acme.json
docker-compose restart traefik
```

## 🔐 الأمان

### تغيير كلمات السر

```bash
# تحديث .env
nano /opt/celia/infrastructure/.env

# إعادة تشغيل الخدمات المتأثرة
docker-compose up -d --force-recreate directus n8n grafana
```

### مراجعة Logs الأمان

```bash
# Fail2ban status
sudo fail2ban-client status

# UFW status
sudo ufw status

# Audit logs
sudo journalctl -u celia.service --since "1 hour ago"
```

## 📊 المراقبة

### الوصول إلى Dashboards

- **Traefik**: https://traefik.celia.fashion/dashboard/
- **Grafana**: https://monitor.celia.fashion
- **Directus**: https://cms.celia.fashion
- **N8n**: https://n8n.celia.fashion

### مقاييس الأداء

```bash
# متوسط وقت الاستجابة
docker-compose exec postgres psql -U celia -d celia_db -c "
  SELECT AVG(latency_ms) FROM chat_logs 
  WHERE created_at > NOW() - INTERVAL '1 hour';"

# عدد الطلبات اليومية
docker-compose exec postgres psql -U celia -d celia_db -c "
  SELECT COUNT(*) FROM chat_logs 
  WHERE created_at > NOW() - INTERVAL '1 day';"
```

## 🔄 الصيانة الدورية

### يومياً

```bash
# التحقق من الصحة العامة
docker-compose ps
docker stats --no-stream
df -h
```

### أسبوعياً

```bash
# نسخ احتياطي
/opt/celia/infrastructure/scripts/backup.sh

# تنظيف السجلات القديمة
docker-compose logs --tail=0
```

### شهرياً

```bash
# تحديث النظام
cd /opt/celia
git pull origin main
docker-compose pull
docker-compose up -d --build

# تحديث Ubuntu
sudo apt update && sudo apt upgrade -y

# إعادة تشغيل
sudo reboot
```

## 📞 الحصول على المساعدة

### معلومات التشخيص

```bash
# جمع معلومات النظام للدعم
cat > /tmp/celia-diagnostic.txt <<EOF
Date: $(date)
Docker Version: $(docker --version)
Compose Version: $(docker-compose --version)
System: $(uname -a)

Services Status:
$(docker-compose ps)

Resource Usage:
$(docker stats --no-stream)

Disk Usage:
$(df -h)

Memory:
$(free -h)

Recent Errors:
$(docker-compose logs --tail=50 | grep -i error)
EOF

cat /tmp/celia-diagnostic.txt
```

### الإبلاغ عن مشكلة

1. قم بإنشاء ملف التشخيص أعلاه
2. افتح issue على GitHub
3. أرفق ملف التشخيص
4. وصف المشكلة بالتفصيل

---

**💡 نصيحة**: احفظ هذا الدليل في مكان آمن للرجوع إليه!
