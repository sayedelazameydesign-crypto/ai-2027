# 📊 توزيع الموارد - CELIA Fashion Design

## إجمالي الموارد المتاحة (Oracle Free Tier)
- **CPU**: 4 ARM Ampere cores (3.0 GHz)
- **RAM**: 24 GB DDR4
- **Storage**: 200 GB Block Storage
- **Network**: 480 Mbps

## توزيع الموارد حسب الخدمة

### المستوى 1: الخدمات الحرجة (Critical Services)

| الخدمة | CPU | RAM | Storage | الأولوية |
|--------|-----|-----|---------|----------|
| Ollama | 1.5 cores (37.5%) | 8 GB | 20 GB | Critical |
| Postgres | 0.5 cores (12.5%) | 4 GB | 50 GB | Critical |
| AL-Agent | 0.5 cores (12.5%) | 2 GB | - | Critical |
| **المجموع** | **2.5 cores** | **14 GB** | **70 GB** | - |

### المستوى 2: الخدمات الأساسية (Core Services)

| الخدمة | CPU | RAM | Storage | الأولوية |
|--------|-----|-----|---------|----------|
| Qdrant | 0.5 cores (12.5%) | 3 GB | 30 GB | High |
| Redis | 0.2 cores (5%) | 2 GB | 5 GB | High |
| Directus | 0.3 cores (7.5%) | 2 GB | 10 GB | High |
| Traefik | 0.2 cores (5%) | 512 MB | - | High |
| **المجموع** | **1.2 cores** | **7.5 GB** | **45 GB** | - |

### المستوى 3: الخدمات الاختيارية (Optional Services)

| الخدمة | CPU | RAM | Storage | الأولوية |
|--------|-----|-----|---------|----------|
| N8n | 0.2 cores (5%) | 2 GB | 20 GB | Medium |
| Grafana | 0.1 cores (2.5%) | 512 MB | 5 GB | Medium |
| Prometheus | 0.1 cores (2.5%) | 1 GB | 10 GB | Medium |
| Vault | 0.05 cores (1.25%) | 256 MB | 2 GB | Medium |
| Watchtower | 0.05 cores (1.25%) | 128 MB | - | Low |
| **المجموع** | **0.5 cores** | **~4 GB** | **37 GB** | - |

## الإجمالي الكلي

| المقياس | المستخدم | المتاح | النسبة |
|---------|----------|---------|--------|
| CPU | 4.2 cores | 4 cores | 105%* |
| RAM | 25.5 GB | 24 GB | 106%* |
| Storage | 152 GB | 200 GB | 76% |

*ملاحظة: القيم النظرية. في الواقع، ليست كل الخدمات تستهلك الحد الأقصى في نفس الوقت.

## استراتيجية التحسين

### 1. CPU Sharing
- الخدمات لا تستخدم 100% من CPU المخصص طوال الوقت
- Docker shares CPU dynamically بين الحاويات
- Peak usage متوقع: 70-80% من إجمالي CPU

### 2. Memory Management
- Swap file: 4 GB إضافية للطوارئ
- OOM Killer: يوقف الخدمات الأقل أهمية أولاً
- Memory overcommit: معظم الخدمات لا تستخدم الحد الأقصى

### 3. Storage Optimization
- Logs: محدودة بـ 10MB/file × 3 files
- Cleanup: تلقائي للصور القديمة
- Compression: قواعد البيانات والنسخ الاحتياطية

## خطة التوسع (Scale-Up Plan)

### عند الوصول إلى 80% استخدام:

#### الخيار 1: Vertical Scaling (على نفس الخادم)
```bash
# Oracle Cloud يسمح بزيادة الموارد:
# - CPU: حتى 4 cores (مجاني)
# - RAM: حتى 24 GB (مجاني)
# - Storage: حتى 200 GB (مجاني)
```

#### الخيار 2: Horizontal Scaling (خوادم متعددة)
```
Server 1 (Oracle Free):
├── Traefik (Load Balancer)
├── Postgres (Primary)
└── Redis (Primary)

Server 2 (Oracle Free):
├── Ollama
├── Qdrant
└── AL-Agent (Instance 1)

Server 3 (Oracle Free):
├── Ollama
├── Qdrant
└── AL-Agent (Instance 2)
```

#### الخيار 3: Hybrid (محلي + سحابي)
```
Oracle Cloud:
└── Postgres, Redis, Directus (البيانات)

Local/ARM Server:
├── Ollama (GPU accelerated)
└── Qdrant

Cloud (OpenRouter/Cloudflare):
└── Static files, CDN
```

## مراقبة الموارد

### الحدود التنبيهية (Alert Thresholds)

| المقياس | تحذير | حرج |
|---------|-------|------|
| CPU Usage | > 75% | > 90% |
| RAM Usage | > 80% | > 95% |
| Disk Usage | > 85% | > 95% |
| Swap Usage | > 50% | > 80% |

### أوامر المراقبة

```bash
# CPU و RAM في الوقت الفعلي
docker stats --no-stream

# التفاصيل الكاملة
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# استخدام القرص
df -h | grep -E 'Filesystem|/dev/sda1'

# استخدام Swap
free -h

# أكثر 10 حاويات استهلاكاً للذاكرة
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}" | sort -k 2 -h | tail -10
```

## إجراءات الطوارئ (Emergency Procedures)

### إذا نفدت الذاكرة (OOM):

```bash
# 1. إيقاف الخدمات غير الحرجة
docker-compose stop n8n grafana prometheus

# 2. إعادة تشغيل Ollama مع موارد أقل
docker-compose restart ollama

# 3. تنظيف Redis cache
docker exec redis redis-cli FLUSHDB

# 4. تفريغ المساحة
docker system prune -a -f
```

### إذا نفدت المساحة:

```bash
# 1. حذف logs القديمة
truncate -s 0 /var/lib/docker/containers/*/*-json.log

# 2. حذف الصور غير المستخدمة
docker image prune -a -f

# 3. حذف volumes غير المستخدمة
docker volume prune -f

# 4. ضغط قاعدة البيانات
docker exec postgres vacuumdb -U celia -d celia_db -f --analyze
```

## Best Practices

1. **لا تشغل كل الخدمات دفعة واحدة**: ابدأ بالحد الأدنى ثم أضف
2. **مراقبة مستمرة**: راقب الموارد يومياً خلال أول أسبوع
3. **نسخ احتياطي**: قبل أي تغيير كبير
4. **اختبار الحمل**: اختبر بعدد مستخدمين محدود أولاً
5. **توثيق**: سجل أي تغييرات في الموارد

---

**تذكر**: هذا نظام production-grade على بنية مجانية. التحسين المستمر ضروري!
