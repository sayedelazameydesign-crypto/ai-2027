# 🔧 دليل البرمجيين - Developer Guide

## نظرة عامة على الواجهات البرمجية (APIs)

### 1. Social Media Integration API

```python
from src.social_media_integration import (
    SocialMediaIntegrationManager,
    SocialMediaPost,
    SocialPlatform,
    SocialMediaCredentials
)

# إنشاء المدير
manager = SocialMediaIntegrationManager()

# تسجيل منصة
twitter_creds = SocialMediaCredentials(
    platform=SocialPlatform.TWITTER,
    access_token='your_token'
)
manager.register_platform(twitter_creds)

# إنشاء منشور
post = SocialMediaPost(
    content="محتوى المنشور",
    platform=SocialPlatform.TWITTER,
    hashtags=['AI', 'BitNet'],
    links=['https://example.com']
)

# النشر
response = manager.post_to_platform(post, SocialPlatform.TWITTER)
print(response.url)  # رابط المنشور

# الحصول على الإحصائيات
stats = manager.get_analytics()
```

### 2. GitHub Integration API

```python
from src.github_integration import (
    GitHubManager,
    GitHubConfig,
    GitHubFile,
    GitHubIssue,
    GitHubPullRequest
)

# إنشاء المدير
config = GitHubConfig(
    token='your_token',
    username='your_username',
    email='your_email@example.com',
    repo_name='my_repo'
)
github = GitHubManager(config)

# إنشاء ملف
file = GitHubFile(
    path='results/analysis.json',
    content='{"result": "data"}',
    message='Add analysis result'
)
result = github.create_file(file)

# إنشاء Issue
issue = GitHubIssue(
    title='Bug Report',
    body='Description of the bug'
)
issue_result = github.create_issue(issue)

# الحصول على الإحصائيات
stats = github.get_repo_stats()
```

### 3. Unified System API

```python
from src.unified_system import (
    UnifiedIntegrationSystem,
    UnifiedSystemConfig,
    SmartLinkingEngine
)

# إعداد التكوين
config = UnifiedSystemConfig(
    project_name='My AI Project',
    enable_smart_assistant=True,
    enable_social_media=True,
    enable_github=True
)

# إنشاء النظام
system = UnifiedIntegrationSystem(config)

# تهيئة المكونات
system.initialize_smart_assistant()
system.initialize_social_media()
system.initialize_github(github_config)

# معالجة وتشارك
result = system.process_and_share(
    query='Your question',
    share_to_platforms=[SocialPlatform.TWITTER],
    save_to_github=True
)

# الحصول على الحالة
status = system.get_system_status()

# إنشاء تقرير
system.generate_report('report.json')
```

---

## بنية البيانات

### SocialMediaPost

```python
@dataclass
class SocialMediaPost:
    content: str              # محتوى المنشور
    platform: SocialPlatform  # منصة التواصل
    title: Optional[str]      # العنوان (اختياري)
    images: List[str]         # قائمة الصور
    hashtags: List[str]       # الهاشتاجات
    mentions: List[str]       # الإشارات
    links: List[str]          # الروابط
    scheduled_time: Optional[datetime]  # وقت الجدولة
    metadata: Dict            # بيانات إضافية
```

### GitHubFile

```python
@dataclass
class GitHubFile:
    path: str                 # مسار الملف
    content: str              # محتوى الملف
    message: str              # رسالة الـ Commit
    branch: str = "main"      # الفرع
    metadata: Dict = field(default_factory=dict)
```

### UnifiedSystemConfig

```python
@dataclass
class UnifiedSystemConfig:
    project_name: str
    project_version: str
    author: str
    enable_smart_assistant: bool
    enable_social_media: bool
    enable_github: bool
    enable_learning: bool
    social_platforms: List[str]
    github_auto_sync: bool
```

---

## أمثلة متقدمة

### مثال 1: نشر آلي على كل المنصات

```python
from src.unified_system import UnifiedIntegrationSystem
from src.social_media_integration import SocialPlatform

def auto_publish(system, query):
    """نشر تلقائي على جميع المنصات"""
    result = system.process_and_share(
        query=query,
        share_to_platforms=[
            SocialPlatform.TWITTER,
            SocialPlatform.FACEBOOK,
            SocialPlatform.TELEGRAM
        ],
        save_to_github=True
    )
    
    # سجل النتائج
    for platform_result in result['social_shares']:
        if platform_result['success']:
            print(f"✓ {platform_result['platform']}: {platform_result['url']}")
        else:
            print(f"✗ {platform_result['platform']}: فشل")
    
    return result
```

### مثال 2: مزامنة مخصصة

```python
def custom_sync(system):
    """مزامنة مخصصة بقواعد محددة"""
    engine = SmartLinkingEngine(system)
    
    # قاعدة 1: نشر If confidence > 90%
    engine.add_link_rule(
        source='smart_assistant',
        target='social_media',
        condition=lambda r: r.get('confidence', 0) > 0.9,
        transformer=lambda r: SocialMediaPost(
            content=r.get('prediction'),
            platform=SocialPlatform.TWITTER,
            hashtags=['HighConfidence', 'AI']
        )
    )
    
    # قاعدة 2: حفظ If confidence > 95%
    engine.add_link_rule(
        source='smart_assistant',
        target='github',
        condition=lambda r: r.get('confidence', 0) > 0.95
    )
    
    return engine
```

### مثال 3: مراقبة الأداء

```python
def monitor_performance(system):
    """مراقبة أداء النظام"""
    status = system.get_system_status()
    
    print(f"""
    النظام: {status['system_name']}
    الإصدار: {status['version']}
    وقت التشغيل: {status['uptime']:.0f} ثانية
    
    المكونات:
    - المساعد الذكي: {'فعال' if status['components']['smart_assistant'] else 'معطل'}
    - وسائل التواصل: {'فعال' if status['components']['social_media'] else 'معطل'}
    - GitHub: {'فعال' if status['components']['github'] else 'معطل'}
    
    الإحصائيات:
    - عدد العمليات: {status['operations']}
    """)
    
    if status['social_media_stats']:
        stats = status['social_media_stats']
        print(f"معدل النجاح: {stats['success_rate']:.1f}%")
```

---

## معالجة الأخطاء

```python
from src.unified_system import UnifiedIntegrationSystem

def safe_process(system, query):
    """معالجة آمنة مع التعامل مع الأخطاء"""
    try:
        result = system.process_and_share(query=query)
        return result
    
    except ConnectionError:
        print("❌ خطأ في الاتصال بالمنصات")
        return None
    
    except ValueError as e:
        print(f"❌ خطأ في البيانات: {e}")
        return None
    
    except Exception as e:
        print(f"❌ خطأ غير متوقع: {e}")
        logger.error(f"Error: {e}", exc_info=True)
        return None
```

---

## التوسع والتخصيص

### إضافة منصة جديدة

```python
from src.social_media_integration import SocialMediaIntegrationManager
from src.social_media_integration import SocialPlatform

# 1. إضافة المنصة إلى Enum
class SocialPlatform(Enum):
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"  # جديد!

# 2. إنشاء معالج خاص بالمنصة
class InstagramHandler:
    def post_image(self, post, account_id):
        """نشر صورة على Instagram"""
        # التطبيق هنا
        pass

# 3. تحديث المدير
manager = SocialMediaIntegrationManager()
# سيتم معالجة الـ Instagram تلقائياً
```

### إضافة قاعدة ربط مخصصة

```python
from src.unified_system import SmartLinkingEngine

def add_custom_rule(engine):
    """إضافة قاعدة ربط مخصصة"""
    engine.add_link_rule(
        source='smart_assistant',
        target='database',  # قاعدة بيانات مخصصة
        condition=lambda r: True,  # دائماً
        transformer=lambda r: {
            'query': r.get('query'),
            'answer': r.get('prediction'),
            'confidence': r.get('confidence'),
            'timestamp': datetime.now()
        }
    )
```

---

## الاختبار

```python
def test_unified_system():
    """اختبار النظام الموحد"""
    config = UnifiedSystemConfig()
    system = UnifiedIntegrationSystem(config)
    
    # اختبار التهيئة
    assert system.initialize_smart_assistant() == True
    assert system.initialize_social_media() == True
    
    # اختبار المعالجة
    result = system.process_and_share(query='test')
    assert result['assistant_response'] is not None
    
    # اختبار الإحصائيات
    status = system.get_system_status()
    assert status['components']['smart_assistant'] == True
    
    print("✓ جميع الاختبارات نجحت!")
```

---

## الإحاجة والمزامنة

```python
def schedule_posts(manager, posts, interval_minutes=60):
    """جدولة المنشورات"""
    from datetime import datetime, timedelta
    
    current_time = datetime.now()
    
    for i, post in enumerate(posts):
        scheduled_time = current_time + timedelta(minutes=interval_minutes * i)
        post.scheduled_time = scheduled_time
        
        manager.schedule_post(post, post.platform)
    
    print(f"✓ تمت جدولة {len(posts)} منشورات")
```

---

## الإنتاج والنشر

```python
def deploy_system():
    """نشر النظام للإنتاج"""
    config = UnifiedSystemConfig(
        project_name='Ultimate BitNet Production',
        github_auto_sync=True,  # المزامنة التلقائية
        enable_learning=True    # التعلم المستمر
    )
    
    system = UnifiedIntegrationSystem(config)
    system.initialize_smart_assistant()
    system.initialize_social_media()
    
    # حفظ الإعدادات
    system.save_config('production_config.json')
    
    print("✓ تم نشر النظام للإنتاج!")
    return system
```

---

## معايير الأداء

```python
import time

def benchmark_system(system, num_queries=100):
    """قياس أداء النظام"""
    times = []
    
    for i in range(num_queries):
        start = time.time()
        system.process_and_share(query=f'test query {i}')
        elapsed = time.time() - start
        times.append(elapsed)
    
    avg_time = sum(times) / len(times)
    print(f"متوسط وقت المعالجة: {avg_time:.3f} ثانية")
    print(f"الحد الأقصى: {max(times):.3f} ثانية")
    print(f"الحد الأدنى: {min(times):.3f} ثانية")
```

---

**آخر تحديث**: فبراير 2026
**الإصدار**: 2.0.0
**الحالة**: ✅ جاهز للمطورين
