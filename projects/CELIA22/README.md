# 🎨 CELIA Fashion Design - AI-Powered Fashion Platform

## نظرة عامة
CELIA هو نظام بيئي رقمي متكامل يجمع بين الذكاء الاصطناعي المحلي، الحوكمة اللامركزية (DAO)، والشبكات الاجتماعية الفيدرالية لتصميم الأزياء.

## المكونات الرئيسية

### 1. طبقة البنية التحتية
- **Oracle Cloud Free Tier**: 4 ARM cores + 24GB RAM + 200GB Storage
- **Docker Compose Stack**: إدارة جميع الخدمات
- **Traefik**: Reverse proxy وإدارة الشهادات

### 2. طبقة الذكاء الاصطناعي
- **Ollama**: نماذج LLM محلية (Phi-3 Mini)
- **Qdrant**: قاعدة بيانات متجهة للذاكرة
- **OpenRouter**: احتياطي سحابي للمهام المعقدة

### 3. طبقة التطبيق
- **AL-Agent**: الوكيل الذكي الأساسي
- **Directus**: نظام إدارة المحتوى
- **N8n**: منصة الأتمتة
- **Vault**: إدارة الأسرار

### 4. طبقة المراقبة
- **Prometheus + Grafana**: مراقبة الأداء
- **Loki**: تجميع السجلات

## متطلبات النظام
- Oracle Cloud Account (Always Free Tier)
- Domain name مع Cloudflare
- Git
- SSH access

## التثبيت السريع

```bash
# 1. استنساخ المشروع
git clone https://github.com/your-org/celia-fashion-design.git
cd celia-fashion-design

# 2. تشغيل سكريبت الإعداد على Oracle Cloud
chmod +x infrastructure/scripts/setup.sh
sudo ./infrastructure/scripts/setup.sh

# 3. إعداد متغيرات البيئة
cp infrastructure/.env.example infrastructure/.env
nano infrastructure/.env

# 4. نشر النظام
cd infrastructure
docker-compose up -d
```

## الهيكل

```
celia-fashion-design/
├── infrastructure/          # Docker Compose والبنية التحتية
├── ai-core/                # AL-Agent الأساسي
├── agents/                 # Multi-Agent System
├── frontend/               # واجهة المستخدم
├── governance/             # DAO وGitOps
└── docs/                   # التوثيق
```

## التكلفة
**$0/شهر** - يعمل بالكامل على الموارد المجانية!

## الأمان
- TLS/SSL عبر Let's Encrypt
- Vault لتخزين الأسرار
- Authelia للتوثيق الموحد
- Fail2ban + CrowdSec للحماية

## الترخيص
MIT License

## المساهمة
المساهمات مرحب بها! يرجى قراءة دليل المساهمة أولاً.
