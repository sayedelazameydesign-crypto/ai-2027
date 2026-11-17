# ❓ الأسئلة الشائعة - CELIA Fashion Design

## 🌟 عام

### س1: ما هو CELIA Fashion Design؟
**ج**: CELIA هو نظام بيئي ذكي لتصميم الأزياء يجمع بين الذكاء الاصطناعي المحلي (Ollama/Phi-3)، قاعدة بيانات متجهة للذاكرة (Qdrant)، ونظام إدارة محتوى (Directus). يعمل بالكامل على موارد مجانية.

### س2: هل النظام فعلاً مجاني 100%؟
**ج**: نعم! البنية الأساسية تعمل على:
- Oracle Cloud Always Free Tier (4 ARM cores + 24GB RAM)
- نماذج AI محلية مفتوحة المصدر (Phi-3, Nomic Embed)
- جميع الأدوات open source

التكلفة الوحيدة اختيارية:
- Domain name (~$10/سنة)
- OpenRouter API (فقط إذا فعّلت Cloud Fallback)

### س3: ما الفرق بين CELIA والحلول التجارية (OpenAI, etc)؟
**ج**:

| الميزة | CELIA | الحلول التجارية |
|--------|-------|------------------|
| التكلفة | $0/شهر | $20-200/شهر |
| الخصوصية | بياناتك 100% | يحفظون بياناتك |
| التخصيص | تحكم كامل | محدود |
| Offline | يعمل بدون إنترنت | يتطلب اتصال دائم |
| القيود | موارد محدودة | غير محدود (بسعر) |

## 🔧 التثبيت والإعداد

### س4: ما المتطلبات التقنية؟
**ج**:
- **للخادم**: Oracle Cloud account (مجاني)
- **للمطور**: معرفة أساسية بـ Docker و Linux
- **للوقت**: 1-2 ساعة للنشر الكامل

### س5: هل يمكن تشغيله على خادم غير Oracle؟
**ج**: نعم! يعمل على أي خادم ARM أو x86:
- **ARM**: Raspberry Pi 4/5, AWS Graviton, Ampere
- **x86**: أي خادم Linux
- **Local**: جهازك المحلي (للتطوير)

فقط تأكد من الموارد:
- Min: 2 cores, 8GB RAM, 50GB storage
- Recommended: 4 cores, 16GB RAM, 100GB storage

### س6: ماذا لو لم أملك domain؟
**ج**: يمكنك استخدام:
1. **IP مباشر**: `http://YOUR_IP:PORT` (للتطوير فقط)
2. **Cloudflare Tunnel**: مجاني ويوفر HTTPS
3. **DuckDNS**: مجاني لـ subdomain

### س7: كيف أحصل على Cloudflare API Token؟
**ج**:
1. سجل دخول إلى Cloudflare Dashboard
2. My Profile > API Tokens > Create Token
3. اختر: "Edit zone DNS" template
4. Zone Resources: Include > Specific zone > [domain الخاص بك]
5. Create Token وانسخه

## 🤖 الذكاء الاصطناعي

### س8: ما مدى ذكاء النموذج المحلي (Phi-3)؟
**ج**: Phi-3 Mini (3.8B parameters):
- ✅ ممتاز لـ: محادثة عامة، نصائح موضة، تحليل ترندات
- ⚠️ محدود في: مهام معقدة جداً، لغات نادرة
- 🚀 السرعة: 20-50 token/sec على ARM

للمقارنة:
- **Phi-3 Mini**: ~GPT-3.5 quality, أسرع 10x
- **Claude Haiku** (cloud fallback): أذكى لكن بتكلفة

### س9: كيف أحسن دقة الإجابات؟
**ج**:
```bash
# 1. زيادة Context Depth (استرجاع ذكريات أكثر)
curl -X POST /chat -d '{"context_depth": 10}'

# 2. استخدام Cloud Fallback للمهام المعقدة
curl -X POST /chat -d '{"use_cloud": true}'

# 3. إضافة معرفة مخصصة
curl -X POST /embed -d '{
  "user_id": "system",
  "content": "معلومات عن ترندات الموضة 2024...",
  "type": "fashion_knowledge"
}'
```

### س10: هل يمكن استخدام نماذج أخرى؟
**ج**: نعم! عدل في `ai-core/src/main.py`:
```python
# استبدل phi3:mini بأي نموذج Ollama
"model": "llama3.1:8b"  # أو mistral, qwen, etc
```

النماذج المتاحة:
```bash
docker exec ollama ollama list
```

تحميل نموذج جديد:
```bash
docker exec ollama ollama pull llama3.1:8b
```

## 💾 البيانات والذاكرة

### س11: كم يحفظ النظام من المحادثات؟
**ج**: 
- **Vector DB (Qdrant)**: آخر 1000 محادثة/مستخدم (قابل للزيادة)
- **Postgres**: جميع المحادثات (unlimited)
- **Cache (Redis)**: 1 ساعة للردود المتكررة

### س12: كيف أحذف بيانات مستخدم (GDPR)؟
**ج**:
```bash
# API endpoint
curl -X DELETE https://api.celia.fashion/memory/USER_ID

# أو يدوياً
docker exec postgres psql -U celia -d celia_db -c "
  DELETE FROM chat_logs WHERE user_id = 'USER_ID';
  DELETE FROM social_accounts WHERE user_id = 'USER_ID';
"

docker exec qdrant curl -X POST http://localhost:6333/collections/conversations/points/delete \
  -d '{
    "filter": {
      "must": [{"key": "user_id", "match": {"value": "USER_ID"}}]
    }
  }'
```

### س13: هل البيانات مشفرة؟
**ج**:
- ✅ **In Transit**: TLS/SSL عبر Traefik
- ✅ **At Rest**: يمكن تفعيل LUKS encryption على Block Volume
- ✅ **Secrets**: Vault يشفر جميع API keys
- ⚠️ **Database**: Postgres غير مشفر افتراضياً (يمكن تفعيله)

لتفعيل database encryption:
```bash
# في docker-compose.yml
postgres:
  environment:
    POSTGRES_INITDB_ARGS: "--data-checksums"
  command: >
    postgres
    -c ssl=on
    -c ssl_cert_file=/etc/ssl/certs/server.crt
    -c ssl_key_file=/etc/ssl/private/server.key
```

## 🔒 الأمان

### س14: هل النظام آمن للاستخدام في الإنتاج؟
**ج**: نعم، مع احتياطات:
✅ **مطبق**:
- Firewall (UFW)
- Fail2ban (حماية من brute force)
- TLS/SSL certificates
- Docker network isolation
- Secrets في Vault

⚠️ **يجب تطبيقها**:
- تغيير جميع كلمات السر الافتراضية
- تفعيل 2FA لـ Directus/N8n
- تحديثات أمنية منتظمة
- Backup منتظم

### س15: كيف أحمي Traefik Dashboard؟
**ج**: افتراضياً محمي بـ Basic Auth. للأمان الإضافي:

```yaml
# في docker-compose.yml
traefik:
  labels:
    # IP Whitelist (فقط لعنوان IP محدد)
    - "traefik.http.middlewares.whitelist.ipwhitelist.sourcerange=YOUR_IP/32"
    - "traefik.http.routers.traefik.middlewares=auth,whitelist"
```

## 📊 الأداء

### س16: كم مستخدم يمكنه خدمة؟
**ج**: على Oracle Free Tier:
- **Concurrent Users**: 10-20 (مع Ollama)
- **Daily Users**: 100-500 (مع caching جيد)
- **Requests/minute**: 30-60 (بدون cloud fallback)

للتوسع:
- استخدم Multiple Oracle accounts (كل واحد free)
- Cloud fallback لـ peak times
- Caching aggressive

### س17: كيف أسرع الاستجابات؟
**ج**:
1. **Enable Redis Caching**:
```python
# في .env
ENABLE_CACHING=true
CACHE_TTL=3600  # 1 hour
```

2. **تقليل Context Depth**:
```json
{"context_depth": 3}  # بدلاً من 5
```

3. **Smaller Model** (للسرعة):
```bash
docker exec ollama ollama pull phi3:mini  # 3.8B
# بدلاً من
docker exec ollama ollama pull llama3.1:8b
```

4. **Prompt Optimization**:
```python
# أقصر = أسرع
"message": "نصيحة موضة سريعة"  # ✅
# بدلاً من
"message": "أعطني تحليلاً مفصلاً لكل ترند..."  # ❌
```

## 🛠️ الصيانة

### س18: كم مرة يجب عمل Backup؟
**ج**:
- **Critical Data**: يومياً (database, uploads)
- **Configs**: عند كل تغيير
- **Full System**: أسبوعياً

سكريبت تلقائي:
```bash
# Cron job يومياً الساعة 2 صباحاً
0 2 * * * /opt/celia/infrastructure/scripts/backup.sh
```

### س19: كيف أستعيد من Backup؟
**ج**:
```bash
# 1. إيقاف الخدمات
cd /opt/celia/infrastructure
docker-compose down

# 2. فك ضغط النسخة
cd /opt/celia/backups
tar xzf 20240101_120000.tar.gz

# 3. استعادة Database
gunzip 20240101_120000/database.sql.gz
docker-compose up -d postgres
sleep 10
docker exec -i postgres psql -U celia celia_db < 20240101_120000/database.sql

# 4. استعادة Qdrant
docker-compose up -d qdrant
docker cp 20240101_120000/qdrant_data/. qdrant:/qdrant/storage

# 5. استعادة Uploads
docker cp 20240101_120000/uploads/. directus:/directus/uploads

# 6. إعادة تشغيل
docker-compose up -d
```

### س20: كيف أحدث النظام؟
**ج**:
```bash
cd /opt/celia

# 1. Backup أولاً!
./infrastructure/scripts/backup.sh

# 2. سحب التحديثات
git pull origin main

# 3. إعادة بناء
cd infrastructure
docker-compose pull
docker-compose build --no-cache al-agent
docker-compose up -d --force-recreate

# 4. تنظيف
docker system prune -f
```

## 🌐 التكاملات

### س21: كيف أربط Facebook/Google؟
**ج**: عبر N8n workflows:
1. سجل دخول إلى https://n8n.celia.fashion
2. استورد workflow من `/infrastructure/n8n-workflows/`
3. أضف Credentials (OAuth tokens)
4. فعّل Workflow

### س22: هل يمكن ربط WhatsApp؟
**ج**: نعم، خياران:
1. **Twilio API** (مدفوع): رسمي لكن ~$0.005/رسالة
2. **whatsapp-web.js** (مجاني): غير رسمي، قد يتم حظره

أضف للـ docker-compose:
```yaml
whatsapp:
  image: pedroslopez/whatsapp-web.js
  volumes:
    - whatsapp_data:/app/.wwebjs_auth
```

## 🚨 استكشاف الأخطاء

### س23: Ollama يقول "Out of Memory"
**ج**:
```bash
# 1. تقليل Parallel Requests
OLLAMA_NUM_PARALLEL=1

# 2. استخدام نموذج أصغر
docker exec ollama ollama pull phi3:mini  # 3.8B بدلاً من 7B

# 3. إعادة تشغيل
docker-compose restart ollama

# 4. إضافة swap (إذا لم يكن موجود)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### س24: SSL Certificate لا يعمل
**ج**:
```bash
# 1. التحقق من DNS
nslookup celia.fashion

# 2. التحقق من Cloudflare API Token
docker-compose logs traefik | grep -i cloudflare

# 3. حذف الشهادات القديمة
docker-compose exec traefik rm /letsencrypt/acme.json
docker-compose restart traefik

# 4. الانتظار 5 دقائق ثم اختبر
curl -I https://celia.fashion
```

### س25: Database connection refused
**ج**:
```bash
# 1. التحقق من حالة Postgres
docker-compose ps postgres

# 2. عرض logs
docker-compose logs postgres

# 3. اختبار الاتصال
docker exec postgres psql -U celia -d celia_db -c "SELECT 1;"

# 4. إعادة إنشاء (احذر: يمسح البيانات!)
docker-compose down
docker volume rm infrastructure_postgres_data
docker-compose up -d postgres
```

## 💡 نصائح وحيل

### نصيحة 1: تسريع أول إعداد
```bash
# بدلاً من انتظار تحميل النماذج (15 دقيقة)
# حمّلها بالتوازي مع إعداد باقي النظام:

# Terminal 1
docker-compose up -d ollama
docker exec -it ollama bash
ollama pull phi3:mini &
ollama pull nomic-embed-text &

# Terminal 2
docker-compose up -d postgres redis qdrant
```

### نصيحة 2: توفير المساحة
```bash
# تنظيف أسبوعي تلقائي
cat > /etc/cron.weekly/docker-cleanup <<'EOF'
#!/bin/bash
docker system prune -f
docker volume prune -f
journalctl --vacuum-time=7d
EOF
chmod +x /etc/cron.weekly/docker-cleanup
```

### نصيحة 3: Dashboard سريع
```bash
# إنشاء alias للأوامر الشائعة
cat >> ~/.bashrc <<'EOF'
alias celia-status='cd /opt/celia/infrastructure && docker-compose ps'
alias celia-logs='cd /opt/celia/infrastructure && docker-compose logs -f'
alias celia-stats='docker stats --no-stream'
EOF
source ~/.bashrc
```

---

**هل لديك سؤال آخر؟** افتح issue على GitHub!
