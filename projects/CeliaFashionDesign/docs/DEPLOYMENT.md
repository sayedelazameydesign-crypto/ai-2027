# 🚀 دليل النشر الكامل لـ CELIA Fashion Design

## المتطلبات الأولية

### 1. Oracle Cloud Account
- قم بإنشاء حساب على Oracle Cloud (مجاني)
- رابط التسجيل: https://www.oracle.com/cloud/free/

### 2. Domain Name
- احصل على domain من Cloudflare أو أي مزود آخر
- مثال: `celia.fashion`

### 3. Cloudflare Account
- سجل domain في Cloudflare (مجاني)
- احصل على API Token

## المرحلة 1: إعداد Oracle Cloud VM (10 دقائق)

### خطوة 1.1: إنشاء Virtual Machine

1. سجل الدخول إلى Oracle Cloud Console
2. اذهب إلى: Compute > Instances > Create Instance
3. اختر الإعدادات التالية:
   - **Name**: `celia-production`
   - **Image**: `Ubuntu 24.04`
   - **Shape**: `VM.Standard.A1.Flex`
     - OCPU count: `4`
     - Memory (GB): `24`
   - **Boot Volume**: `200 GB`
   - **Networking**: 
     - Create new VCN
     - Assign public IP
   - **SSH Keys**: 
     - Upload your public key أو Generate new pair

4. انقر **Create**

### خطوة 1.2: فتح Ports في Security List

1. اذهب إلى: Networking > Virtual Cloud Networks
2. اختر VCN الخاص بك
3. اذهب إلى: Security Lists > Default Security List
4. أضف Ingress Rules:
   - **Port 22** (SSH): Source `0.0.0.0/0`
   - **Port 80** (HTTP): Source `0.0.0.0/0`
   - **Port 443** (HTTPS): Source `0.0.0.0/0`

### خطوة 1.3: الاتصال بالخادم

```bash
# استبدل YOUR_IP و your-key.pem بقيمك
ssh -i your-key.pem ubuntu@YOUR_ORACLE_IP

# تأكد من الاتصال
uname -a
# يجب أن ترى: Linux ... aarch64 (ARM)
```

## المرحلة 2: تشغيل سكريبت الإعداد (15 دقيقة)

### خطوة 2.1: تحميل السكريبت

```bash
# على الخادم
curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/celia/main/infrastructure/scripts/setup.sh -o setup.sh

# أو انسخ السكريبت يدوياً
nano setup.sh
# الصق محتوى السكريبت
```

### خطوة 2.2: تشغيل الإعداد

```bash
chmod +x setup.sh
sudo ./setup.sh
```

الانتظار حتى يكتمل السكريبت (10-15 دقيقة)

### خطوة 2.3: إعادة التشغيل

```bash
sudo reboot
```

انتظر دقيقة ثم أعد الاتصال:

```bash
ssh -i your-key.pem ubuntu@YOUR_ORACLE_IP
```

## المرحلة 3: نسخ ملفات المشروع (5 دقائق)

### خيار A: استنساخ من Git

```bash
cd /opt/celia
sudo git clone https://github.com/YOUR_ORG/celia-fashion-design.git .
sudo chown -R ubuntu:ubuntu /opt/celia
```

### خيار B: نسخ يدوي

من جهازك المحلي:

```bash
# افترض أن الملفات في ~/celia-fashion-design
cd ~/celia-fashion-design

# انسخ للخادم
scp -i your-key.pem -r * ubuntu@YOUR_ORACLE_IP:/opt/celia/
```

## المرحلة 4: إعداد البيئة (10 دقائق)

### خطوة 4.1: إنشاء ملف .env

```bash
cd /opt/celia/infrastructure
cp .env.example .env
nano .env
```

### خطوة 4.2: توليد الأسرار

```bash
# توليد مفاتيح عشوائية
for i in {1..5}; do openssl rand -base64 32; done
```

انسخ القيم وضعها في `.env`:

```bash
# مثال للقيم المطلوبة
DOMAIN=celia.fashion
DB_PASSWORD=<القيمة_المولدة_1>
DIRECTUS_KEY=<القيمة_المولدة_2>
DIRECTUS_SECRET=<القيمة_المولدة_3>
N8N_ENCRYPTION_KEY=<القيمة_المولدة_4>

# بيانات Admin
ADMIN_EMAIL=admin@celia.fashion
ADMIN_PASSWORD=<كلمة_سر_قوية>

# Cloudflare
CF_EMAIL=your-email@example.com
CF_API_TOKEN=<Cloudflare_API_Token>

# كلمات سر الخدمات
N8N_PASSWORD=<كلمة_سر_قوية>
GRAFANA_PASSWORD=<كلمة_سر_قوية>
```

### خطوة 4.3: إنشاء Traefik Basic Auth

```bash
# توليد hash لـ Traefik Dashboard
sudo apt-get install -y apache2-utils
htpasswd -nb admin your-password
# انسخ النتيجة وضعها في TRAEFIK_BASIC_AUTH
```

## المرحلة 5: إعداد DNS (5 دقائق)

في Cloudflare Dashboard:

1. اذهب إلى DNS Records
2. أضف السجلات التالية (استبدل YOUR_ORACLE_IP):

```
Type    Name            Content             Proxy Status
A       @               YOUR_ORACLE_IP      Proxied
A       *.celia.fashion YOUR_ORACLE_IP      DNS only
CNAME   api             celia.fashion       DNS only
CNAME   cms             celia.fashion       DNS only
CNAME   n8n             celia.fashion       DNS only
CNAME   monitor         celia.fashion       DNS only
```

## المرحلة 6: النشر التدريجي (30 دقيقة)

### Phase 1: البنية الأساسية (5 دقائق)

```bash
cd /opt/celia/infrastructure

# تشغيل الطبقة الأولى
docker-compose up -d postgres redis traefik

# التحقق
docker-compose ps
docker-compose logs -f postgres redis traefik

# انتظر حتى تكون healthy
docker-compose ps | grep healthy
```

### Phase 2: طبقة الذكاء الاصطناعي (15 دقيقة)

```bash
# تشغيل Ollama و Qdrant
docker-compose up -d ollama qdrant

# مهم: أول مرة سيحمل النماذج (10-15 دقيقة)
docker-compose logs -f ollama

# انتظر حتى ترى:
# "✓ phi3:mini pulled"
# "✓ nomic-embed-text pulled"
```

### Phase 3: AL-Agent (5 دقائق)

```bash
# بناء ونشر AL-Agent
docker-compose up -d --build al-agent

# التحقق من الصحة
docker-compose logs -f al-agent

# اختبار API
curl https://api.celia.fashion/health
```

### Phase 4: CMS والخدمات (5 دقائق)

```bash
# تشغيل Directus و N8n
docker-compose up -d directus n8n

# التحقق
docker-compose ps
```

### Phase 5: المراقبة (اختياري)

```bash
# تشغيل Grafana و Prometheus
docker-compose up -d grafana prometheus

# الوصول إلى Grafana
# https://monitor.celia.fashion
# User: admin
# Password: من .env
```

## المرحلة 7: التحقق النهائي (5 دقائق)

### اختبار جميع الخدمات

```bash
# AL-Agent API
curl https://api.celia.fashion/health

# Directus CMS
curl https://cms.celia.fashion/server/health

# N8n
curl https://n8n.celia.fashion/healthz

# Traefik Dashboard
# https://traefik.celia.fashion/dashboard/
```

### اختبار المحادثة

```bash
curl -X POST https://api.celia.fashion/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "مرحباً، ما هي أحدث صيحات الموضة؟",
    "user_id": "test_user_123",
    "language": "ar"
  }'
```

يجب أن ترى رد JSON مع response.

## المرحلة 8: إعداد النسخ الاحتياطي التلقائي

```bash
# جعل السكريبت قابل للتنفيذ
chmod +x /opt/celia/infrastructure/scripts/backup.sh

# إضافة cron job (يومياً الساعة 2 صباحاً)
sudo crontab -e

# أضف السطر التالي:
0 2 * * * /opt/celia/infrastructure/scripts/backup.sh
```

## استكشاف الأخطاء

### المشكلة: Ollama لا يحمل النماذج

```bash
# دخول للحاوية
docker exec -it ollama bash

# تحميل يدوي
ollama pull phi3:mini
ollama pull nomic-embed-text
```

### المشكلة: SSL لا يعمل

```bash
# التحقق من Traefik logs
docker-compose logs traefik | grep -i error

# التأكد من Cloudflare API Token
docker-compose exec traefik cat /etc/traefik/traefik.yml
```

### المشكلة: Out of Memory

```bash
# التحقق من استهلاك الذاكرة
free -h
docker stats

# إعادة تشغيل خدمة معينة
docker-compose restart ollama
```

### المشكلة: قاعدة البيانات لا تعمل

```bash
# دخول لـ Postgres
docker exec -it postgres psql -U celia -d celia_db

# التحقق من الجداول
\dt

# الخروج
\q
```

## الخطوات التالية

### 1. إعداد واجهة المستخدم
- نشر Next.js frontend على Vercel (مجاني)
- ربطها بـ API

### 2. تفعيل التكاملات الاجتماعية
- إعداد N8n workflows
- ربط Facebook/Google/GitHub

### 3. إعداد DAO
- نشر العقود الذكية
- إعداد Snapshot للتصويت

### 4. التحسينات
- إضافة Multi-Agent System
- تفعيل Long-term Memory
- إضافة Image Generation

## الصيانة الدورية

### يومياً
- التحقق من logs: `docker-compose logs --tail=50`
- مراقبة الموارد: `docker stats`

### أسبوعياً
- مراجعة Grafana dashboards
- التحقق من النسخ الاحتياطية

### شهرياً
- تحديث الصور: `docker-compose pull && docker-compose up -d`
- مراجعة أمان النظام

## الدعم

إذا واجهت مشاكل:
1. راجع logs: `docker-compose logs <service_name>`
2. افتح issue على GitHub
3. تواصل مع المجتمع

---

**تهانينا! 🎉 نظام CELIA الآن يعمل بالكامل!**
