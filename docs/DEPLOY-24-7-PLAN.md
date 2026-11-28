# خطة النشر التنفيذية 24/7 — AI-2027 (سقف الصرف $0)

> **تاريخ القياس:** 2026-11-28 · **الحزمة القابلة للتشغيل:** [`deployment/vm/`](../deployment/vm/) · **الاختبارات:** `python3 -m unittest scripts.test_vm_bundle`
>
> هذه الخطة تفصل بين ما **تم التحقق منه** في هذا المستودع وما **يحتاج تنفيذاً منك** (حسابات، أسرار، قرارات). لا تعتبر أي خدمة "تعمل 24/7" قبل أن تنجح بوابة القبول الخاصة بمرحلتها.

---

## 1. ما المقصود بـ "يعمل 24/7"

| الخدمة | المنفذ | مكشوفة للإنترنت؟ | لماذا |
|---|---|---|---|
| مستقبِل واتساب/YCloud (`/webhook`) | 8080 | **نعم** عبر Caddy (HTTPS) | YCloud يجب أن يصل إليه في أي لحظة |
| فحص الحياة (`/healthz`) | 8080 | **نعم** | لمراقب خارجي؛ لا يكشف أي بيانات |
| Agent Studio | 8790 | لا — عبر `ssh -L` فقط | لا يملك مصادقة مستخدمين |
| Live Agent | 8090 | لا — عبر `ssh -L` فقط | رموزه تشغيلية محلية |
| Command HUD | 8091 | لا — عبر `ssh -L` فقط | بلا مصادقة عن بُعد بالتصميم |

كل الخدمات الأربع تستمع على `127.0.0.1` فقط. **Caddy وحده** يستمع على 443 ويمرّر مسارين لا غير؛ أي مسار آخر يعيد 404.

```
 YCloud / UptimeRobot ──HTTPS──▶ Caddy :443 ──(/webhook, /healthz فقط)──▶ webhook 127.0.0.1:8080 ──▶ SQLite
                                     │ كل ما عداه → 404
 أنت (SSH) ──ssh -L──▶ Studio :8790 · Live Agent :8090 · HUD :8091   (127.0.0.1 فقط)
 systemd: Restart=always + مراقب صحة كل دقيقتين + نسخ احتياطي ليلي 03:30 UTC
```

---

## 2. الوضع الحالي المُقاس (لماذا لا يكفي ما هو موجود)

| البند | ما قيس | الأثر على 24/7 |
|---|---|---|
| Render (الخطة المجانية في `render.yaml`) | ينام بعد 15 دقيقة خمول، يستيقظ في ~دقيقة، 750 ساعة/شهر لكل workspace، **بلا قرص دائم** | قاعدة SQLite للمستقبِل تضيع مع كل إعادة تشغيل؛ خدمتان دائمتان = 1,488 ساعة > 750. **غير صالح للمستقبِل.** |
| GitHub Actions cron (Ember beat) | الجدول كل 3 ساعات؛ الفجوات الفعلية بين النبضات **6.8–13.8 ساعة** | الفحص نفسه ينجح 17/17، لكن البوابة تعلن `degraded` منذ 11 نبضة بسبب `beat_freshness`. **cron ليس SLA** ولا يصلح منصة تشغيل. |
| Approval bot (Telegram) | يفشل في كل دورة | السر `PA_APPROVAL_CHAT_ID` غير مضبوط؛ الـ workflow نفسه وجد المعرّف الصحيح لمحادثتك الخاصة: **`722947046`** |
| قواعد حماية `main` | 7 فحوصات مطلوبة لا تعمل أبداً + `update` + `required_linear_history` + `required_signatures` | كل دمج يحتاج تجاوز admin |
| إرسال الردود | `free_stack_runner.py` يحفظ الرسائل `pending_approval` ولا يرسل | الاستقبال 24/7 ممكن الآن؛ **الرد الآلي المعتمد 24/7 مرحلة منفصلة** (م9) |
| إعادة المحاولة في YCloud | 7 محاولات: 10ث → 30ث → 5د → 30د → 1س → 2س → 2س (~6 ساعات) ثم يتوقف نهائياً | توقف أطول من ~6 ساعات = رسائل مفقودة؛ لذلك المراقبة الخارجية إلزامية |

---

## 3. القرار المعماري: خادم افتراضي واحد مجاني دائم

| الخيار | دائم 24/7؟ | قرص دائم | التكلفة الفعلية | الحكم |
|---|---|---|---|---|
| **Oracle Cloud Always Free** (Ampere A1، Ubuntu 24.04) | نعم | 200 GB | **$0** (2 OCPU / 12 GB للحساب المجاني؛ 10 TB خروج/شهر) | **موصى به** |
| Google Cloud e2-micro | نعم | 30 GB | **~$3.65/شهر** لعنوان IPv4 الخارجي (غير مشمول بالمجاني) + 1 GB خروج فقط | ليس $0 |
| Render Free | لا (ينام) | لا | $0 | للعرض فقط، لا للمستقبِل |
| Cloudflare Workers + D1 | نعم | D1 | $0 | خيار مستقبلي: يتطلب إعادة كتابة المستقبِل بـ JavaScript |

**مخاطر Oracle التي يجب أن تقبلها صراحة (قرار مالك — م0):**
1. **استرداد الخوادم الخاملة:** Oracle قد يسترد خادم Always Free إذا بقيت **كل** مقاييس النسبة المئوية 95 (CPU والشبكة والذاكرة على A1) **أقل من 20% لمدة 7 أيام**. هذا الحمل خفيف بطبيعته، فالخطر **حقيقي**.
   - **خيار أ (موصى به إن قبلت ربط بطاقة):** ترقية الحساب إلى Pay-As-You-Go مع **تنبيه ميزانية $1**. الحسابات المدفوعة مستثناة من الاسترداد، وتبقى الفاتورة $0 ما دمت داخل حدود Always Free.
   - **خيار ب:** البقاء على المجاني وقبول الخطر، مع نسخ احتياطي **خارج الخادم** (م7) وإعادة بناء موثّقة خلال ~30 دقيقة.
   - **لا ننصح** بتوليد حمل مصطنع لتفادي القاعدة.
2. لا يوجد SLA، وقد يرفض Oracle التسجيل أو يعلّق الحسابات دون سبب. النسخ الاحتياطي خارج الخادم هو الضمان الوحيد.

---

## 4. المراحل التنفيذية

> **المسؤول:** 👤 = أنت (حسابات/أسرار/قرارات) · 🤖 = جاهز في المستودع · كل مرحلة لها **بوابة قبول**؛ لا تنتقل قبل نجاحها.

### م0 — قرارات المالك 👤 (15 دقيقة)
- [ ] اختيار المضيف: Oracle (موصى) أو غيره.
- [ ] قرار الاسترداد: PAYG + تنبيه ميزانية **أو** المجاني مع قبول الخطر.
- [ ] اسم النطاق: نطاق فرعي مجاني من [DuckDNS](https://www.duckdns.org) (مثل `ai2027-cela.duckdns.org`) أو نطاق تملكه.
- [ ] وجهة النسخ الاحتياطي خارج الخادم: Google Drive أو Dropbox (عبر rclone مشفّر).

### م1 — إصلاح الأعطال الحالية في GitHub 👤 (15 دقيقة)
1. **Approval bot:** Settings → Secrets and variables → Actions → New secret
   `PA_APPROVAL_CHAT_ID` = `722947046` ← ثم شغّل الـ workflow يدوياً. **القبول:** دورة `scheduled cycle` خضراء.
2. **قواعد الحماية:** نفّذ الأمر الموجود في محادثة 2026-11-28 (يحذف الفحوصات السبعة الوهمية + `update` + `required_linear_history`)، أو من الواجهة: Settings → Rules → **Protect main branch**. **القبول:** PR جديد يظهر `MERGEABLE` و`CLEAN` دون `--admin`.
3. **Ember beat:** بعد م6 يصبح المراقب الخارجي هو مصدر الحقيقة للتوفر. خيار لاحق: رفع `--tolerance-hours` في `ember.yml` ليعكس سلوك cron المقاس (فجوات حتى ~14 ساعة)، بدل إعلان `degraded` دائم.

### م2 — تجهيز الخادم على Oracle 👤 (45–90 دقيقة؛ قد يتكرر بسبب نفاد السعة)
1. أنشئ حساب Oracle Cloud واختر **منطقة رئيسية** قريبة (مثل `eu-frankfurt-1` أو `me-jeddah-1`). لا يمكن تغييرها لاحقاً، وموارد Always Free تُنشأ فيها فقط.
2. Compute → Instances → Create:
   - Image: **Canonical Ubuntu 24.04** · Shape: **VM.Standard.A1.Flex** بـ **1 OCPU / 6 GB** (يكفي ويترك هامشاً داخل 2/12).
   - Boot volume: 50 GB · أضف مفتاح SSH العام الخاص بك.
   - إذا ظهر `Out of host capacity`: جرّب Availability Domain آخر أو أعد المحاولة لاحقاً (أو PAYG من م0).
3. Networking → Reserved Public IPs: احجز IP **ثابتاً** واربطه بالخادم (مجاني).
4. VCN → Security List → Ingress Rules:
   - `0.0.0.0/0` TCP **80** و **443** (Let's Encrypt + YCloud).
   - TCP **22** من عنوانك فقط إن أمكن.

**القبول:** `ssh ubuntu@<IP>` يعمل.

### م3 — اسم نطاق مجاني 👤 (10 دقائق)
- DuckDNS: سجّل الدخول، أنشئ النطاق الفرعي، ضع فيه الـ IP المحجوز.
- **القبول:** `dig +short ai2027-cela.duckdns.org` يعيد الـ IP نفسه.

### م4 — التثبيت بأمر واحد 🤖 (10–15 دقيقة)
```bash
ssh ubuntu@<IP>
git clone https://github.com/sayedelazameydesign-crypto/ai-2027.git /tmp/ai2027-src
sudo bash /tmp/ai2027-src/deployment/vm/bootstrap.sh \
  --public-host ai2027-cela.duckdns.org \
  --acme-email you@example.com \
  --open-firewall
```
ما يفعله `bootstrap.sh` (آمن لإعادة التشغيل؛ وإعادة تشغيله **هي** طريقة التحديث):
1. يثبّت `python3` و`venv` و`git` و`sqlite3` و`curl` و`caddy`، وينشئ مستخدم النظام `ai2027` بلا صلاحيات ولا shell.
2. يسحب الكود إلى `/opt/ai2027` (مملوك لـ root؛ الخدمات تقرؤه فقط)، ويبني بيئة Studio من ملف القفل **بدون أدوات التطوير**.
3. **بوابة قبل أي إعادة تشغيل:** يشغّل `whatsapp_cloud_rehearsal --assert` و`free_stack_runner --self-check` واستيراد Studio والنواة **كمستخدم الخدمة**. إن فشلت، يعيد المراجعة السابقة ويخرج.
4. ينشئ ملفات البيئة في `/etc/ai2027` **مرة واحدة** (`root:root 0600`؛ لا يكتب فوق أسرارك أبداً)، ويولّد رموز Live Agent محلياً دون طباعتها.
5. يثبّت وحدات systemd المحصّنة (`ProtectSystem=strict` و`NoNewPrivileges` وغيرها)، ومراقب الصحة، والنسخ الاحتياطي، وCaddyfile بعد `caddy validate`.
6. يعيد تشغيل الخدمات ويفحص صحتها؛ إن فشلت المراجعة الجديدة يعود للسابقة تلقائياً.

**القبول (كلها مطلوبة):**
```bash
sudo /tmp/ai2027-src/deployment/vm/bootstrap.sh --check     # 4 × [PASS]
curl -s https://ai2027-cela.duckdns.org/healthz              # {"ok": true, "service": "free-stack-webhook"}
curl -s -o /dev/null -w '%{http_code}\n' -X POST -d '{}' https://ai2027-cela.duckdns.org/webhook   # 401
curl -s -o /dev/null -w '%{http_code}\n' https://ai2027-cela.duckdns.org/studio                   # 404
```

### م5 — الأسرار وربط YCloud 👤 (10 دقائق)
1. YCloud → Developers → Webhooks → **Add endpoint**:
   URL `https://ai2027-cela.duckdns.org/webhook` · الحدث `whatsapp.inbound_message.received` · انسخ **Signing secret**.
2. على الخادم:
   ```bash
   sudo nano /etc/ai2027/webhook.env        # YCLOUD_WEBHOOK_SECRET=<الصق هنا>
   sudo systemctl restart ai2027-webhook
   ```
   اترك `YCLOUD_API_KEY` و`YCLOUD_WHATSAPP_FROM` فارغين حتى م9؛ المستقبِل لا يرسل شيئاً.
3. أرسل رسالة واتساب إلى رقم الأعمال.

**القبول:** سجل التسليم في YCloud يظهر `200`، وأيضاً:
```bash
sudo sqlite3 /var/lib/ai2027/webhook/free_stack.db \
  "SELECT status, COUNT(*) FROM inbox_messages GROUP BY status;"   # pending_approval|1
```

### م6 — المراقبة والتنبيه 👤🤖 (15 دقيقة)
- 🤖 **داخل الخادم:** `systemd Restart=always` للأعطال، و`ai2027-healthcheck.timer` كل دقيقتين يعيد تشغيل الخدمة المعلّقة بعد فشلين متتاليين، وتحذير عند امتلاء القرص 85%.
  ```bash
  journalctl -t ai2027-health --since today          # قرارات المراقب
  journalctl -u 'ai2027-*' -n 100 --no-pager        # سجلات الخدمات
  systemctl list-timers 'ai2027-*'
  ```
- 👤 **خارج الخادم (إلزامي):** [UptimeRobot](https://uptimerobot.com) المجاني، فحص HTTP(s) كل 5 دقائق على `https://ai2027-cela.duckdns.org/healthz`، مع تنبيه بريد أو Telegram.
  هذا ما يضمن أن تعرف بأي انقطاع قبل أن تنفد محاولات YCloud (~6 ساعات).

**القبول:** أوقف الخدمة عمداً (`sudo systemctl stop ai2027-webhook`)، فيصلك التنبيه خلال ≤10 دقائق. ثم شغّلها وتأكد من وصول رسالة "عادت".

### م7 — النسخ الاحتياطي وتمرين الاسترجاع 👤🤖 (20–30 دقيقة)
- 🤖 `ai2027-backup.timer` يعمل يومياً 03:30 UTC (05:30 القاهرة):
  - قاعدة المستقبِل **أثناء التشغيل** عبر SQLite backup API + `integrity_check`.
  - Live Agent وHUD بلقطة متسقة عبر `runtime_snapshot.py` (إيقاف لثوانٍ ثم تشغيل)، وأرشيف لـ Studio.
  - ملف `SHA256SUMS` ومراجعة الكود، والاحتفاظ بآخر 14 يوماً في `/var/backups/ai2027`.
- 👤 **خارج الخادم:** `sudo apt install rclone && sudo rclone config --config /etc/ai2027/rclone.conf`، وأنشئ remote لـ Google Drive أو Dropbox ثم remote من نوع **crypt** فوقه، ثم ضع في `/etc/ai2027/backup.env`:
  `AI2027_BACKUP_RCLONE_REMOTE=ai2027-crypt:backups`

**القبول (تمرين استرجاع حقيقي):**
```bash
sudo systemctl start ai2027-backup && journalctl -t ai2027-backup -n 20 --no-pager
B=$(sudo ls -1 /var/backups/ai2027 | tail -1)
sudo sh -c "cd /var/backups/ai2027/$B && sha256sum -c SHA256SUMS"
sudo python3 /opt/ai2027/deployment/runtime_snapshot.py restore /var/backups/ai2027/$B/live-agent.tar.gz /tmp/restore-drill
```

### م8 — التحديث والرجوع 🤖 (دقائق)
```bash
sudo /opt/ai2027/deployment/vm/bootstrap.sh                 # آخر main (البوابة + رجوع تلقائي)
sudo /opt/ai2027/deployment/vm/bootstrap.sh --ref <sha|tag> # مراجعة محددة، أو رجوع يدوي لأي commit سابق
```
**ملاحظة:** الإصدار المنشور مثبّت على commit محدد (detached)، فلا يتغير شيء على الخادم دون أن تشغّل الأمر.

### م9 — (منفصلة · تحتاج قرار تصميم) الرد الآلي المعتمد 24/7
اليوم يستقبل النظام ويحفظ فقط. لإرسال الردود دون تدخل يدوي يلزم:
1. ربط `GovernedReplyBridge` بخادم التشغيل (`main()` لا يستخدمه حالياً).
2. قناة موافقة دائمة: Telegram approval bot (بعد إصلاح م1) و/أو Passkey worker (`worker/approval-worker`).
3. احترام نافذة 24 ساعة المجانية؛ ردود نصية فقط، فالقوالب المدفوعة مرفوضة بنيوياً.

هذه المرحلة تحتاج PR مستقلاً باختبارات، ولا تدخل في حزمة النشر الحالية.

---

## 5. أهداف الاستعادة والمخاطر المتبقية

| البند | القيمة | ملاحظة |
|---|---|---|
| **RPO** (أقصى فقد بيانات) | حتى 24 ساعة | الرسائل بين آخر نسخة وتعطل الخادم الكامل قد تضيع؛ YCloud يعيد المحاولة ~6 ساعات فقط |
| **RTO** (زمن العودة) | ~30 دقيقة | خادم جديد + `bootstrap.sh` + استرجاع آخر نسخة |
| نقطة فشل واحدة | خادم واحد | مقبول لسقف $0؛ الترقية لاحقاً: خادم ثانٍ أو استقبال على Cloudflare Workers |
| DuckDNS / Let's Encrypt | اعتماد خارجي مجاني | نطاق مملوك (~$10/سنة) يزيل اعتماد DuckDNS |

## 6. قائمة تحقق أسبوعية (5 دقائق)
- [ ] UptimeRobot: توفر ≥ 99% ولا تنبيهات غير مفسّرة.
- [ ] `sudo /opt/ai2027/deployment/vm/bootstrap.sh --check` ← 4 × PASS.
- [ ] `journalctl -t ai2027-backup --since -7d | grep complete` ← 7 أسطر، وتأكد من وصول النسخ خارج الخادم.
- [ ] `df -h /var/lib/ai2027` < 70%.
- [ ] YCloud delivery logs: لا إخفاقات متتالية.
- [ ] `sudo apt-get update && sudo apt-get -y upgrade` (أو فعّل `unattended-upgrades`).

---

## 7. ما تم التحقق منه في هذا الفرع (وما لم يُتحقق منه)

**تم التحقق (2026-11-28، داخل sandbox):**
- تشغيل الخدمات الأربع **بأوامر وبيئات وحدات systemd نفسها** على نسخة كود **للقراءة فقط**، مع حالة في مجلدات منفصلة:
  - كل الكتابات ذهبت إلى مجلدات الحالة فقط، ولا أخطاء صلاحيات.
  - Studio أكمل مهمة حقيقية (3 خطوات، ملفان ناتجان).
  - HUD نجح في اختبار 100 هجوم (0 اختراق).
  - Live Agent أنتج تشغيلاً `VERIFIED`. ملاحظة: الـ launcher الإنتاجي يبلّغ `cleanup: complete` لأنه يقلّم القرص، وهي حالة صالحة بالتصميم، بينما `runtime_smoke.py` يتوقع وضع الخادم المباشر `not_applicable`.
- مسارات النسخ الاحتياطي: النسخ أثناء التشغيل (المستقبِل ظل يجيب 200)، واللقطة، والاسترجاع بمجموعة ملفات مطابقة، وفحص `SHA256SUMS`.
- مولّد الرموز المضمّن في `bootstrap.sh`: ملفات `0600`، لا يطبع شيئاً، يرفض الكتابة فوق الموجود.
- `systemd-analyze verify`: لا أخطاء صياغة. `systemd-analyze security`: **3.2 OK** لكل خدمة.
- `shellcheck` نظيف بكل المستويات، و`bash -n` لكل السكربتات.
- `scripts/test_vm_bundle.py`: 16 اختباراً تعمل تلقائياً في CI. تم التأكد أنها تفشل عند ثلاثة تراجعات متعمدة: كشف Studio على `0.0.0.0`، وتمرير Caddy لـ Studio، ووضع سر في قالب البيئة.

**لم يُتحقق منه بعد (يتطلب خادماً حقيقياً — م4):** التشغيل كـ root عبر `apt` وsystemd فعلي، و`caddy validate`، وإصدار شهادة Let's Encrypt، وتسليم YCloud حقيقي.
