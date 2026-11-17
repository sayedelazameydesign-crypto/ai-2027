# 🚀 نشر CELIA على Yunohost

## المتطلبات
- حساب Yunohost مثبت وجاهز
- صلاحيات Admin
- Domain مسجل

## الخطوة 1: الاتصال بـ Yunohost

```bash
# عبر SSH
ssh admin@your-yunohost-domain.com

# أو استخدم واجهة Yunohost الويب
# https://your-yunohost-domain.com/admin
```

## الخطوة 2: تثبيت Docker (إذا لم يكن مثبتاً)

```bash
# تحديث النظام
sudo apt update && sudo apt upgrade -y

# تثبيت Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# إضافة المستخدم الحالي لمجموعة docker
sudo usermod -aG docker $USER
newgrp docker

# التحقق من التثبيت
docker --version
docker-compose --version
```

## الخطوة 3: استنساخ المشروع

```bash
# إنشاء مجلد للتطبيقات
sudo mkdir -p /opt/celia
sudo chown -R $USER:$USER /opt/celia

# استنساخ المشروع
cd /opt/celia
git clone https://github.com/sayedelazameydesign-crypto/CELIA22.git .
```

## الخطوة 4: إعداد البيئة

```bash
cd /opt/celia/infrastructure

# نسخ ملف البيئة
cp .env.example .env

# تحرير الملف
nano .env
```

### القيم المهمة لـ Yunohost:

```env
# استبدل بـ domain الخاص بك
DOMAIN=celia.your-yunohost-domain.com

# قاعدة البيانات
DB_USER=celia
DB_PASSWORD=YOUR_STRONG_PASSWORD_HERE
DB_NAME=celia_db

# المفاتيح الأمنية (استخدم: openssl rand -base64 32)
DIRECTUS_KEY=YOUR_RANDOM_KEY_HERE
DIRECTUS_SECRET=YOUR_RANDOM_SECRET_HERE
N8N_ENCRYPTION_KEY=YOUR_RANDOM_KEY_HERE

# بيانات المسؤول
ADMIN_EMAIL=admin@your-yunohost-domain.com
ADMIN_PASSWORD=YOUR_STRONG_PASSWORD

# Cloudflare (اختياري - للـ SSL)
CF_EMAIL=your-email@example.com
CF_API_TOKEN=your-cloudflare-token
```

## الخطوة 5: تشغيل المشروع

```bash
cd /opt/celia/infrastructure

# تشغيل جميع الخدمات
docker-compose up -d

# انتظر 2-3 دقائق لتحميل النماذج
sleep 180

# التحقق من الحالة
docker-compose ps
```

## الخطوة 6: إعداد Nginx (Reverse Proxy)

Yunohost يستخدم Nginx. دعنا نعدّ subdomain:

```bash
# إنشاء ملف تكوين Nginx
sudo nano /etc/nginx/conf.d/celia.conf
```

أضف المحتوى التالي:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name celia.your-yunohost-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

ثم:

```bash
# اختبار التكوين
sudo nginx -t

# إعادة تشغيل Nginx
sudo systemctl restart nginx
```

## الخطوة 7: تفعيل SSL (Let's Encrypt)

```bash
# تثبيت Certbot
sudo apt install certbot python3-certbot-nginx -y

# الحصول على شهادة SSL
sudo certbot --nginx -d celia.your-yunohost-domain.com

# التجديد التلقائي
sudo systemctl enable certbot.timer
```

## الخطوة 8: التحقق من التشغيل

```bash
# عرض السجلات
docker-compose logs -f al-agent

# اختبار API
curl https://celia.your-yunohost-domain.com/health

# اختبار محادثة
curl -X POST https://celia.your-yunohost-domain.com/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "مرحباً",
    "user_id": "test",
    "language": "ar"
  }'
```

## الخطوة 9: إعداد النسخ الاحتياطي

```bash
# إنشاء سكريبت النسخ الاحتياطي
sudo nano /opt/celia/backup-cron.sh
```

أضف:

```bash
#!/bin/bash
cd /opt/celia/infrastructure
docker-compose exec -T postgres pg_dump -U celia celia_db | gzip > /opt/celia/backups/db_$(date +%Y%m%d_%H%M%S).sql.gz
```

ثم:

```bash
# جعل السكريبت قابل للتنفيذ
sudo chmod +x /opt/celia/backup-cron.sh

# إضافة cron job (نسخ احتياطي يومي)
sudo crontab -e

# أضف السطر التالي:
0 2 * * * /opt/celia/backup-cron.sh
```

## الخطوة 10: إدارة الخدمات

```bash
# عرض حالة جميع الخدمات
docker-compose ps

# إعادة تشغيل خدمة معينة
docker-compose restart al-agent

# عرض السجلات
docker-compose logs -f

# إيقاف جميع الخدمات
docker-compose down

# تشغيل جميع الخدمات
docker-compose up -d
```

## الخطوة 11: الوصول للخدمات

بعد التشغيل الناجح:

| الخدمة | الرابط |
|--------|--------|
| AL-Agent API | `https://celia.your-yunohost-domain.com` |
| Directus CMS | `https://celia.your-yunohost-domain.com:8055` |
| N8n | `https://celia.your-yunohost-domain.com:5678` |
| Grafana | `https://celia.your-yunohost-domain.com:3000` |

## استكشاف الأخطاء

### المشكلة: الخدمات لا تبدأ

```bash
# تحقق من السجلات
docker-compose logs

# تحقق من الموارد
docker stats

# أعد التشغيل
docker-compose restart
```

### المشكلة: SSL لا يعمل

```bash
# تحقق من شهادة Let's Encrypt
sudo certbot certificates

# جدد الشهادة يدوياً
sudo certbot renew --force-renewal
```

### المشكلة: قاعدة البيانات ممتلئة

```bash
# تحقق من الحجم
docker exec postgres du -sh /var/lib/postgresql/data

# نظف السجلات القديمة
docker exec postgres psql -U celia -d celia_db -c "DELETE FROM conversations WHERE created_at < NOW() - INTERVAL '30 days';"
```

## الصيانة الدورية

### يومياً
```bash
docker-compose ps
docker stats --no-stream
```

### أسبوعياً
```bash
# تحديث الصور
docker-compose pull
docker-compose up -d

# تنظيف الملفات القديمة
docker system prune -f
```

### شهرياً
```bash
# تحديث النظام
sudo apt update && sudo apt upgrade -y

# سحب آخر التحديثات من GitHub
cd /opt/celia
git pull origin main
docker-compose pull
docker-compose up -d --build
```

## الأمان

### تغيير كلمات السر

```bash
# تحديث .env
nano /opt/celia/infrastructure/.env

# إعادة تشغيل الخدمات
docker-compose up -d --force-recreate
```

### تفعيل Firewall

```bash
# السماح بـ SSH
sudo ufw allow 22/tcp

# السماح بـ HTTP و HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# تفعيل Firewall
sudo ufw enable
```

## الدعم والمساعدة

- 📖 التوثيق: `/opt/celia/docs/`
- 🐛 الأخطاء: `docker-compose logs`
- 💬 المجتمع: GitHub Issues

---

**تم! المشروع جاهز للعمل على Yunohost! 🎉**

