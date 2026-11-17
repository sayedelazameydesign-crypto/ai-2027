# 📱 دليل نظام إدارة وسائل التواصل الاجتماعي الذكي

## 🎉 نظرة عامة

نظام احترافي شامل لإدارة جميع حسابات وسائل التواصل الاجتماعي من مكان واحد.

### ✨ المميزات الرئيسية

1. **ربط الحسابات** 📱
   - Twitter / X
   - Instagram
   - Facebook
   - LinkedIn
   - TikTok

2. **إنشاء محتوى ذكي** 🤖
   - 3 نسخ لكل منشور (رسمية، ودودة، مختصرة)
   - اقتراح هاشتاجات تلقائياً
   - اقتراح وسائط ونصوص للصور
   - دعم العربية والإنجليزية

3. **جدولة ونشر** 📅
   - تقويم محتوى شهري
   - اقتراح أفضل أوقات النشر
   - موافقة قبل النشر
   - تصدير لـ CSV

4. **مراقبة وتفاعل** 👀
   - مراقبة التعليقات 24 ساعة
   - كشف التعليقات السلبية
   - ردود تلقائية مقترحة
   - تنبيه للتفاعلات التي تحتاج تدخل

5. **تحليلات وتقارير** 📊
   - تقرير أسبوعي شامل
   - مقاييس الأداء (وصول، تفاعل، نمو)
   - توصيات للتحسين
   - تتبع الأداء حسب المنصة

---

## 🚀 البدء السريع

### 1. التثبيت

```python
# تثبيت المكتبات المطلوبة
pip install tweepy facebook-sdk requests beautifulsoup4 gradio

# للمساعد الذكي (اختياري - لتحسين المحتوى)
# راجع ملفات المساعد الذكي السابقة
```

### 2. الاستيراد

```python
from complete_social_media_system import (
    SocialMediaMasterManager,
    Platform,
    ContentType,
    PostGoal,
    PostTone,
    AudienceType
)

# إنشاء المدير
manager = SocialMediaMasterManager()
```

---

## 📖 أمثلة الاستخدام

### مثال 1: ربط الحسابات

```python
# ربط Twitter
manager.connect_platform(
    Platform.TWITTER,
    api_key="YOUR_API_KEY",
    api_secret="YOUR_API_SECRET",
    access_token="YOUR_ACCESS_TOKEN",
    access_token_secret="YOUR_ACCESS_TOKEN_SECRET"
)

# ربط Facebook
manager.connect_platform(
    Platform.FACEBOOK,
    access_token="YOUR_FB_ACCESS_TOKEN",
    page_id="YOUR_PAGE_ID"
)

# ربط Instagram (عبر Facebook)
manager.connect_platform(
    Platform.INSTAGRAM,
    access_token="YOUR_IG_ACCESS_TOKEN"
)

# ربط LinkedIn
manager.connect_platform(
    Platform.LINKEDIN,
    access_token="YOUR_LI_ACCESS_TOKEN"
)

# فحص الصلاحيات
permissions = manager.check_all_permissions()
print("الصلاحيات المتاحة:")
for platform, perms in permissions.items():
    print(f"\n{platform}:")
    print(f"  متصل: {perms['connected']}")
    print(f"  النشر: {perms['can_post']}")
    print(f"  الجدولة: {perms['can_schedule']}")
```

---

### مثال 2: إنشاء منشور ترويجي

```python
# الأمر: "اكتب منشور إنجليزي وعربي للترويج لورشة عمل 
#         يوم 10 مايو، 2-3 جمل، 5 هاشتاجات، نبرة حماسية ومهنية"

post = manager.create_post(
    platform=Platform.INSTAGRAM,
    topic="ورشة عمل الذكاء الاصطناعي",
    details="انضم إلينا يوم 10 مايو لورشة عمل شاملة حول الذكاء الاصطناعي والتعلم الآلي. سجّل الآن!",
    link="https://example.com/register",
    content_type=ContentType.PROMOTIONAL,
    goal=PostGoal.CONVERSION,
    audience=AudienceType.PROFESSIONALS
)

# عرض المسودات الثلاث
print("المسودات المتاحة:")
for i, draft in enumerate(post.drafts):
    print(f"\n{i+1}. {draft.version_name}:")
    print(f"   العربي: {draft.text_ar}")
    print(f"   الإنجليزي: {draft.text_en}")
    print(f"   الهاشتاجات: {', '.join(['#' + tag for tag in draft.hashtags])}")
    print(f"   عدد الأحرف: {draft.character_count}")

# عرض اقتراح الوسائط
print(f"\nاقتراح الصورة:")
print(f"  الوصف: {post.media.description_ar}")
print(f"  النص على الصورة: {post.media.image_text}")
print(f"  الأبعاد الموصى بها: {post.media.suggested_size}")

# عرض أفضل وقت للنشر
print(f"\nأفضل وقت للنشر: {post.best_time_suggested}")
```

**النتيجة:**

```
المسودات المتاحة:

1. رسمية:
   العربي: يسرنا أن نعلن عن ورشة عمل الذكاء الاصطناعي. 
            انضم إلينا يوم 10 مايو لورشة عمل شاملة حول 
            الذكاء الاصطناعي والتعلم الآلي. سجّل الآن!
            
            للمزيد: https://example.com/register
   
   الإنجليزي: We are pleased to announce AI Workshop.
               Join us on May 10th for comprehensive workshop...
   
   الهاشتاجات: #الذكاء_الاصطناعي #تقنية #ورشة_عمل #تعلم_آلي #AI
   عدد الأحرف: 185

2. ودودة:
   العربي: مرحباً! نحن متحمسون لـ ورشة عمل الذكاء الاصطناعي 🌟
            انضم إلينا يوم 10 مايو... شاركنا آراءك!
   ...

3. مختصرة:
   العربي: 🎯 ورشة عمل الذكاء الاصطناعي
   ...
```

---

### مثال 3: جدولة أسبوعية

```python
# الأمر: "أنشئ جدول نشر للأسبوع القادم: 
#         3 منشورات إنستغرام، منشور لينكدإن يوم الأربعاء، 
#         وتغريدة يومية"

from datetime import datetime, timedelta

# المنصات
platforms = [
    Platform.INSTAGRAM,  # 3 مرات في الأسبوع
    Platform.INSTAGRAM,
    Platform.INSTAGRAM,
    Platform.LINKEDIN,   # الأربعاء
    Platform.TWITTER,    # يومياً
    Platform.TWITTER,
    Platform.TWITTER
]

# مواضيع للأسبوع
topics = [
    "نصائح التسويق الرقمي",
    "قصة نجاح عميل",
    "منتج جديد",
    "مقال متخصص (LinkedIn)",
    "نصيحة سريعة",
    "سؤال تفاعلي",
    "عرض خاص"
]

# إنشاء المنشورات
posts = []
base_date = datetime.now() + timedelta(days=1)

for i, (platform, topic) in enumerate(zip(platforms, topics)):
    post_date = base_date + timedelta(days=i)
    
    # الأربعاء لـ LinkedIn
    if platform == Platform.LINKEDIN:
        while post_date.weekday() != 2:  # 2 = الأربعاء
            post_date += timedelta(days=1)
    
    post = manager.create_post(
        platform=platform,
        topic=topic,
        details=f"محتوى تفصيلي عن {topic}",
        content_type=ContentType.INFORMATIVE,
        goal=PostGoal.ENGAGEMENT
    )
    
    post.scheduled_time = post_date
    manager.schedule_post(post)
    posts.append(post)
    
    print(f"✅ جدولة: {topic} على {platform.value} - {post_date.strftime('%Y-%m-%d %H:%M')}")

# تصدير الجدول
csv_file = manager.export_schedule_csv("weekly_schedule.csv")
print(f"\n📄 تم تصدير الجدول: {csv_file}")
```

---

### مثال 4: الردود التلقائية

```python
# الأمر: "اقترح 6 ردود جاهزة على أسئلة متكررة 
#         حول الأسعار والشحن وسياسة الإرجاع"

# الأسعار
pricing_responses = manager.suggest_auto_responses("pricing", count=2)
print("ردود الأسعار:")
for i, resp in enumerate(pricing_responses, 1):
    print(f"{i}. {resp['ar']}")
    print(f"   {resp['en']}\n")

# الشحن
shipping_responses = manager.suggest_auto_responses("shipping", count=2)
print("\nردود الشحن:")
for i, resp in enumerate(shipping_responses, 1):
    print(f"{i}. {resp['ar']}")

# الإرجاع
returns_responses = manager.suggest_auto_responses("returns", count=2)
print("\nردود الإرجاع:")
for i, resp in enumerate(returns_responses, 1):
    print(f"{i}. {resp['ar']}")
```

**النتيجة:**

```
ردود الأسعار:
1. شكراً لاستفسارك! يمكنك الاطلاع على أسعارنا من خلال موقعنا الإلكتروني.
   Thank you for your inquiry! You can view our prices on our website.

2. مرحباً! نقدم باقات متنوعة. تواصل معنا مباشرة للتفاصيل.
   Hello! We offer various packages. Contact us directly for details.

ردود الشحن:
1. نوفر الشحن لجميع مناطق المملكة خلال 2-3 أيام عمل.
2. الشحن مجاني للطلبات فوق 200 ريال.

ردود الإرجاع:
1. يمكنك إرجاع المنتج خلال 14 يوم من الاستلام.
2. نوفر سياسة إرجاع مرنة لضمان رضاك التام.
```

---

### مثال 5: تقويم محتوى شهري

```python
# إنشاء تقويم شهري
calendar = manager.create_monthly_calendar(
    platforms=[Platform.TWITTER, Platform.INSTAGRAM, Platform.LINKEDIN],
    posts_per_week=7,
    audience=AudienceType.BUSINESS
)

print(f"📅 تقويم محتوى {calendar['month']}")
print(f"المنصات: {', '.join(calendar['platforms'])}")
print(f"الجمهور: {calendar['audience']}\n")

# عرض الأسبوع الأول
week_1 = calendar['weeks'][0]['week_1']
print("الأسبوع الأول:")
for day in week_1:
    print(f"  اليوم {day['day']}: {day['date']} {day['time']}")
    print(f"    المنصة: {day['platform']}")
    print(f"    النوع: {day['content_type']}")
    print(f"    الهدف: {day['goal']}\n")
```

---

### مثال 6: النشر مع الموافقة

```python
# إنشاء منشور ترويجي (يحتاج موافقة)
post = manager.create_post(
    platform=Platform.FACEBOOK,
    topic="عرض خاص - خصم 50%",
    details="عرض محدود لمدة 3 أيام فقط!",
    content_type=ContentType.PROMOTIONAL,
    goal=PostGoal.CONVERSION
)

# فحص الحاجة للموافقة
if post.requires_approval:
    print("⚠️ هذا المنشور يحتاج موافقتك الصريحة")
    print(f"المحتوى: {post.get_selected_content().text_ar}")
    
    # طلب الموافقة
    user_input = input("\nهل توافق على نشر هذا المحتوى؟ (نعم/لا): ")
    
    if user_input.lower() in ['نعم', 'yes', 'y']:
        # الموافقة
        manager.approve_post(post.id, approved_by="المدير")
        
        # الجدولة
        manager.schedule_post(post)
        
        # النشر (إذا كان الوقت مناسب)
        # manager.publish_post(post)
        
        print(f"✅ تمت الموافقة والجدولة")
    else:
        print("❌ تم إلغاء النشر")
else:
    # لا يحتاج موافقة، جدولة مباشرة
    manager.schedule_post(post)
```

---

### مثال 7: مراقبة التفاعل

```python
# بعد نشر منشور
published_post = posts[0]  # أول منشور من المثال 3

# بدء المراقبة
monitoring_report = manager.start_monitoring(
    published_post,
    duration_hours=24
)

print("📊 تقرير المراقبة:")
print(f"  إجمالي التعليقات: {monitoring_report['total_comments']}")
print(f"  تعليقات سلبية: {monitoring_report['negative_comments']}")
print(f"  يحتاج انتباه: {monitoring_report['requires_attention']}")

# الحصول على التعليقات المبلّغ عنها
flagged_comments = manager.get_flagged_comments()

if flagged_comments:
    print(f"\n⚠️ {len(flagged_comments)} تعليق يحتاج تدخلك:")
    for comment in flagged_comments:
        print(f"\n  👤 {comment.author}: {comment.text}")
        print(f"  المشاعر: {comment.sentiment}")
        if comment.suggested_response:
            print(f"  رد مقترح: {comment.suggested_response}")
```

---

### مثال 8: التقرير الأسبوعي

```python
# توليد تقرير أسبوعي
report = manager.generate_weekly_report()

print("=" * 60)
print(f"📊 تقرير الأداء الأسبوعي")
print(f"الفترة: {report['period']['start']} - {report['period']['end']}")
print("=" * 60)

print(f"\n📈 الملخص:")
print(f"  إجمالي المنشورات: {report['summary']['total_posts']}")
print(f"  الوصول الإجمالي: {report['summary']['total_reach']:,}")
print(f"  التفاعل الإجمالي: {report['summary']['total_engagement']:,}")
print(f"  معدل التفاعل: {report['summary']['avg_engagement']}%")
print(f"  نمو المتابعين: +{report['summary']['followers_gained']}")

print(f"\n📱 الأداء حسب المنصة:")
for platform, data in report['platform_breakdown'].items():
    print(f"  {platform}:")
    print(f"    المنشورات: {data['posts']}")
    print(f"    التفاعل: {data['engagement']}")

if report['best_post']:
    print(f"\n⭐ أفضل منشور:")
    print(f"  المنصة: {report['best_post']['platform']}")
    print(f"  المحتوى: {report['best_post']['content']}...")
    print(f"  التفاعل: {report['best_post']['engagement']}")

print(f"\n💡 توصيات للتحسين:")
for i, rec in enumerate(report['recommendations'], 1):
    print(f"  {i}. {rec}")
```

**نتيجة مثالية:**

```
============================================================
📊 تقرير الأداء الأسبوعي
الفترة: 2026-02-03 - 2026-02-10
============================================================

📈 الملخص:
  إجمالي المنشورات: 15
  الوصول الإجمالي: 25,000
  التفاعل الإجمالي: 2,150
  معدل التفاعل: 8.6%
  نمو المتابعين: +45

📱 الأداء حسب المنصة:
  instagram:
    المنشورات: 6
    التفاعل: 950
  twitter:
    المنشورات: 5
    التفاعل: 720
  linkedin:
    المنشورات: 4
    التفاعل: 480

⭐ أفضل منشور:
  المنصة: instagram
  المحتوى: ورشة عمل الذكاء الاصطناعي...
  التفاعل: 380

💡 توصيات للتحسين:
  1. ⭐ منصة instagram تحقق أفضل نتائج - ركز عليها أكثر
  2. 🎨 نوّع أنواع المحتوى (فيديوهات، صور، قصص) لزيادة الوصول
  3. 🎯 أضف المزيد من المنشورات الهادفة للتحويل مع روابط واضحة
```

---

## 🛡️ الأمان والخصوصية

### القيود المطبقة تلقائياً:

1. **موافقة قبل النشر:**
   ```python
   # تفعيل الموافقة للمنشورات الترويجية
   manager.settings['require_approval_for_promotional'] = True
   ```

2. **فحص السياسات:**
   - كلمات محظورة
   - محتوى حساس
   - بيانات شخصية

3. **سجل النشاط:**
   ```python
   # عرض سجل النشاط لمنشور
   for activity in post.activity_log:
       print(f"{activity['timestamp']}: {activity['action']} - {activity['details']}")
   ```

### حماية البيانات:

```python
# بيانات الاعتماد لا تُحفظ في النص الصريح
# يتم استخدام OAuth للربط الآمن
# كلمات المرور غير مطلوبة أو محفوظة
```

---

## 📊 الإحصائيات العامة

```python
stats = manager.get_stats()

print("📊 إحصائيات النظام:")
print(f"  المنصات المتصلة: {stats['connected_platforms']}")
print(f"  المنشورات المجدولة: {stats['scheduled_posts']}")
print(f"  تنتظر الموافقة: {stats['pending_approval']}")
print(f"  تم نشرها هذا الأسبوع: {stats['posted_this_week']}")
print(f"  تعليقات مبلّغ عنها: {stats['flagged_comments']}")
```

---

## 💾 الحفظ والتحميل

```python
# حفظ جميع البيانات
manager.save_data()

# يحفظ:
# - social_media_data/scheduled_posts.json
# - social_media_data/settings.json
# - social_media_data/performance_history.json
```

---

## 🎯 حالات استخدام متقدمة

### 1. حملة تسويقية متكاملة

```python
# حملة لمدة شهر
campaign_posts = []

for week in range(4):
    # أسبوع 1: توعية
    # أسبوع 2: تفاعل
    # أسبوع 3: بناء علامة
    # أسبوع 4: تحويل
    
    goal = [PostGoal.AWARENESS, PostGoal.ENGAGEMENT, 
            PostGoal.BRAND_BUILDING, PostGoal.CONVERSION][week]
    
    post = manager.create_post(
        platform=Platform.INSTAGRAM,
        topic=f"الأسبوع {week+1} من الحملة",
        goal=goal
    )
    
    campaign_posts.append(post)
```

### 2. إدارة أزمات

```python
# مراقبة مستمرة
while True:
    flagged = manager.get_flagged_comments()
    
    if flagged:
        # إشعار فوري
        send_notification("تعليقات سلبية تحتاج تدخل!")
        
        for comment in flagged:
            handle_crisis_comment(comment)
    
    time.sleep(300)  # كل 5 دقائق
```

---

## 🎉 الخلاصة

لديك الآن نظام احترافي كامل لإدارة وسائل التواصل الاجتماعي مع:

✅ ربط جميع المنصات الرئيسية  
✅ إنشاء محتوى ذكي بـ 3 نسخ  
✅ جدولة ونشر تلقائي  
✅ مراقبة وردود تلقائية  
✅ تحليلات وتقارير شاملة  
✅ أمان وخصوصية مضمونة  

**جاهز للاستخدام الفوري! 🚀**
