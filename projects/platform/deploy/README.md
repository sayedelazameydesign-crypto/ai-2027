# دليل النشر على Oracle Cloud Always Free — خطوة بخطوة

> الهدف: منصة حية 24/7 بصفر تكلفة. الوقت المتوقع: 30–45 دقيقة (معظمها انتظار Oracle).

## المرحلة 1 — إنشاء الحساب (مرة واحدة)

1. افتح **oracle.com/cloud/free** → Start for free
2. البيانات: بريد حقيقي + هاتفك. **الدولة: Egypt** متاحة
3. **البطاقة البنكية:** يطلبونها للتحقق فقط (حجز مؤقت ~$1 يُرد) — **لن يُخصم شيء**
   ما دمت داخل Always Free. أي بطاقة فيزا/ماستركارد تعمل (حتى المدفوعة مسبقًا غالبًا)
4. **اختيار الـ Home Region — قرار لا يُغيّر لاحقًا:** اختر منطقة قريبة فيها سعة A1
   عادةً: `Frankfurt (eu-frankfurt-1)` أو `Amsterdam` — الأفضل لمصر زمن استجابة

## المرحلة 2 — إنشاء الخادم (VM)

Menu → Compute → Instances → **Create Instance**:

| الإعداد | القيمة الإلزامية |
|---|---|
| Image | **Ubuntu 24.04** (Canonical) |
| Shape | **VM.Standard.A1.Flex** حصرًا ← هذا هو المجاني الكبير |
| OCPUs / RAM | **4 OCPU / 24 GB** (أقصى المجاني — خذه كله) |
| Boot volume | 100–200 GB (ضمن الـ 200 المجانية) |
| SSH keys | نزّل الـ private key واحفظه — هو بابك الوحيد للخادم |

> ⚠️ **خطأ "Out of capacity":** شائع في A1 — ليس رفضًا نهائيًا. أعد المحاولة
> في أوقات مختلفة (الصباح الباكر بتوقيت أوروبا أنجح)، أو جرّب Availability
> Domain آخر في نفس المنطقة. البعض ينجح من أول مرة والبعض بعد أيام.

## المرحلة 3 — فتح منافذ الويب في جدار Oracle (VCN)

Instance page → Virtual Cloud Network → Security Lists → Default → **Add Ingress Rules**:

```
Rule 1: Source 0.0.0.0/0 · TCP · Destination Port 80
Rule 2: Source 0.0.0.0/0 · TCP · Destination Port 443
```

(جدار النظام الداخلي iptables يفتحه السكربت تلقائيًا.)

## المرحلة 4 — الدخول والنشر

```bash
# من جهازك:
ssh -i /path/to/private-key.pem ubuntu@<PUBLIC_IP>

# على الخادم:
git clone https://github.com/sayedelazameydesign-crypto/platform.git
cd platform
bash deploy/oracle-setup.sh          # بدون دومين: يعمل على :3000
# أو مع دومين موجه للـ IP:
bash deploy/oracle-setup.sh yourdomain.com   # SSL تلقائي مجاني
```

ثم ضع مفتاح Groq:

```bash
nano /opt/platform/.env
# OPENAI_API_KEY=gsk_...
# OPENAI_MODEL=openai/gpt-oss-120b
# OPENAI_BASE_URL=https://api.groq.com/openai/v1
sudo systemctl restart platform
```

## المرحلة 5 — التحقق

```bash
curl http://localhost:3000/api/health     # {"ok":true}
journalctl -u platform -f                 # السجلات الحية
```

من متصفحك: `http://<PUBLIC_IP>:3000` أو `https://yourdomain.com`

## الدومين المجاني (اختياري حتى أول ربح)

- **DuckDNS** (duckdns.org): سابدومين مجاني `yourname.duckdns.org` يصلح لـ SSL
- أو IP مباشرة مؤقتًا — ثم دومين مدفوع (~150 جنيه/سنة) من أول إيراد

## مسار Cloudflare البديل (موصى به) — بلا فتح منافذ إطلاقًا

بدل فتح 80/443 في VCN + Caddy، استخدم **Cloudflare Tunnel** (اتصال خارج فقط):

```bash
# عرض فوري بلا حساب وبلا دومين (رابط HTTPS عشوائي مؤقت):
bash deploy/cloudflare-tunnel.sh quick

# إنتاج دائم (دومينك مضاف لحساب Cloudflare المجاني):
bash deploy/cloudflare-tunnel.sh setup app.yourdomain.com
```

| المقارنة | منافذ + Caddy | Cloudflare Tunnel |
|---|---|---|
| فتح منافذ VCN/iptables | مطلوب | **غير مطلوب** |
| إخفاء IP الخادم + درع DDoS | ✗ | ✅ مجانًا |
| SSL | Let's Encrypt | تلقائي على الحافة |
| يتطلب دومينًا | لا (يعمل بـ IP) | نعم لوضع الإنتاج (quick بلا دومين) |

**إضافات Cloudflare المجانية لاحقًا:** R2 (نسخ sqlite.db الاحتياطي — 10GB مجانًا)،
Turnstile (حماية التسجيل من البوتات عند إطلاق الخطة المجانية)، WAF/Rate-Limit
(حماية حصة Groq من الإساءة).


## نصائح تشغيل

| الموضوع | الإجراء |
|---|---|
| تحديث المنصة | `cd /opt/platform && git pull && npm ci && npm run build && sudo systemctl restart platform` |
| نسخ احتياطي للبيانات | `cp /opt/platform/sqlite.db ~/backup-$(date +%F).db` (كرونها أسبوعيًا) |
| استرداد Oracle للخامل | نادر مع استخدام فعلي؛ خذ Boot Volume Backup شهريًا احتياطًا |
| مفتاح Groq | دوّره من console.groq.com قبل الإعلان العام عن الخدمة |
