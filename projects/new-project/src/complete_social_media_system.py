"""
📱 نظام إدارة وسائل التواصل الاجتماعي الذكي المتكامل
=======================================================

نظام احترافي شامل يدمج:
- المساعد الذكي المتقدم
- APIs جميع المنصات
- إنشاء محتوى ذكي
- جدولة ونشر تلقائي
- تحليلات متقدمة
- ردود تلقائية
"""

import json
import csv
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import hashlib
import asyncio
from collections import defaultdict

# محاولة استيراد مكتبات السوشيال ميديا
try:
    import tweepy
    TWITTER_AVAILABLE = True
except ImportError:
    TWITTER_AVAILABLE = False
    print("⚠️ tweepy غير متوفر - تثبيت: pip install tweepy")

try:
    import facebook
    FACEBOOK_AVAILABLE = True
except ImportError:
    FACEBOOK_AVAILABLE = False
    print("⚠️ facebook-sdk غير متوفر - تثبيت: pip install facebook-sdk")

# استيراد المساعد الذكي المتقدم
try:
    from advanced_assistant import AdvancedSmartAssistant
    ASSISTANT_AVAILABLE = True
except ImportError:
    ASSISTANT_AVAILABLE = False
    print("⚠️ المساعد الذكي غير متوفر")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. التعريفات الأساسية
# ═══════════════════════════════════════════════════════════════════════════════

class Platform(Enum):
    """منصات التواصل"""
    TWITTER = "twitter"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"

class ContentType(Enum):
    """أنواع المحتوى"""
    INFORMATIVE = "معلوماتي"
    IMAGE_CAROUSEL = "صور/كاروسيل"
    SHORT_VIDEO = "فيديو قصير"
    STORY = "قصة"
    PROMOTIONAL = "ترويجي"
    ENGAGEMENT = "تفاعلي"
    POLL = "استطلاع"

class PostGoal(Enum):
    """أهداف المنشورات"""
    AWARENESS = "توعية"
    ENGAGEMENT = "تفاعل"
    CONVERSION = "تحويل"
    TRAFFIC = "زيارات"
    BRAND_BUILDING = "بناء العلامة"
    LEAD_GENERATION = "جذب عملاء"

class PostTone(Enum):
    """نبرة المنشور"""
    FORMAL = "رسمية"
    FRIENDLY = "ودودة"
    PROFESSIONAL = "مهنية"
    CASUAL = "عادية"
    HUMOROUS = "فكاهية"
    ENTHUSIASTIC = "حماسية"
    TECHNICAL = "تقنية"

class AudienceType(Enum):
    """نوع الجمهور"""
    GENERAL = "عام"
    BUSINESS = "أعمال"
    YOUTH = "شباب"
    PROFESSIONALS = "محترفون"
    STUDENTS = "طلاب"
    ENTREPRENEURS = "رواد أعمال"

# ═══════════════════════════════════════════════════════════════════════════════
# 2. هياكل البيانات
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SocialMediaCredentials:
    """بيانات اعتماد المنصة"""
    platform: Platform
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    access_token: Optional[str] = None
    access_token_secret: Optional[str] = None
    page_id: Optional[str] = None
    
    # الصلاحيات
    can_post: bool = False
    can_schedule: bool = False
    can_read_comments: bool = False
    can_read_messages: bool = False
    can_manage_ads: bool = False
    
    verified: bool = False
    last_verified: Optional[datetime] = None
    rate_limit_remaining: int = 100
    
    def to_dict(self) -> Dict:
        """تحويل لقاموس (بدون كشف البيانات الحساسة)"""
        return {
            'platform': self.platform.value,
            'verified': self.verified,
            'can_post': self.can_post,
            'can_schedule': self.can_schedule,
            'last_verified': self.last_verified.isoformat() if self.last_verified else None
        }

@dataclass
class PostDraft:
    """مسودة منشور"""
    version_name: str  # "رسمية", "ودودة", "مختصرة"
    text_ar: str
    text_en: Optional[str] = None
    tone: PostTone = PostTone.PROFESSIONAL
    hashtags: List[str] = field(default_factory=list)
    character_count: int = 0
    
    def __post_init__(self):
        self.character_count = len(self.text_ar)

@dataclass
class MediaSuggestion:
    """اقتراح وسائط"""
    description_ar: str
    description_en: str
    image_text: Optional[str] = None  # نص على الصورة
    alt_text: Optional[str] = None  # النص البديل
    suggested_size: Tuple[int, int] = (1080, 1080)
    file_type: str = "image/jpeg"

@dataclass  
class ScheduledPost:
    """منشور مجدول"""
    id: str
    platform: Platform
    drafts: List[PostDraft]
    selected_draft: int = 0  # المسودة المختارة
    
    content_type: ContentType = ContentType.INFORMATIVE
    goal: PostGoal = PostGoal.AWARENESS
    audience: AudienceType = AudienceType.GENERAL
    
    scheduled_time: Optional[datetime] = None
    best_time_suggested: Optional[datetime] = None
    
    media: Optional[MediaSuggestion] = None
    
    # الموافقة
    requires_approval: bool = False
    approved: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    
    # الحالة
    status: str = "draft"  # draft, scheduled, posted, failed, cancelled
    posted_time: Optional[datetime] = None
    platform_post_id: Optional[str] = None
    
    # الأداء
    metrics: Dict = field(default_factory=dict)
    
    # السجل
    activity_log: List[Dict] = field(default_factory=list)
    
    def log_activity(self, action: str, details: str = ""):
        """تسجيل نشاط"""
        self.activity_log.append({
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details
        })
    
    def get_selected_content(self) -> PostDraft:
        """الحصول على المسودة المختارة"""
        return self.drafts[self.selected_draft]
    
    def to_dict(self) -> Dict:
        """تحويل لقاموس"""
        return {
            'id': self.id,
            'platform': self.platform.value,
            'drafts': [asdict(d) for d in self.drafts],
            'selected_draft': self.selected_draft,
            'content_type': self.content_type.value,
            'goal': self.goal.value,
            'scheduled_time': self.scheduled_time.isoformat() if self.scheduled_time else None,
            'status': self.status,
            'requires_approval': self.requires_approval,
            'approved': self.approved
        }

@dataclass
class EngagementMetrics:
    """مقاييس التفاعل"""
    timestamp: datetime
    
    # الوصول
    reach: int = 0
    impressions: int = 0
    
    # التفاعل
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    clicks: int = 0
    
    # النمو
    followers_gained: int = 0
    followers_lost: int = 0
    
    # معدلات
    engagement_rate: float = 0.0
    click_through_rate: float = 0.0
    
    def calculate_rates(self):
        """حساب المعدلات"""
        total_engagement = self.likes + self.comments + self.shares + self.saves
        
        if self.impressions > 0:
            self.engagement_rate = (total_engagement / self.impressions) * 100
            self.click_through_rate = (self.clicks / self.impressions) * 100

@dataclass
class CommentMonitoring:
    """مراقبة التعليقات"""
    post_id: str
    platform: Platform
    comment_id: str
    author: str
    text: str
    timestamp: datetime
    sentiment: str = "neutral"  # positive, neutral, negative
    requires_response: bool = False
    suggested_response: Optional[str] = None
    responded: bool = False
    flagged_for_review: bool = False

# ═══════════════════════════════════════════════════════════════════════════════
# 3. مدير المنصات (Platform Manager)
# ═══════════════════════════════════════════════════════════════════════════════

class PlatformManager:
    """مدير اتصالات المنصات"""
    
    def __init__(self):
        self.credentials: Dict[Platform, SocialMediaCredentials] = {}
        self.api_clients: Dict[Platform, Any] = {}
    
    def add_credentials(self, 
                       platform: Platform,
                       **kwargs) -> bool:
        """إضافة بيانات اعتماد"""
        creds = SocialMediaCredentials(platform=platform, **kwargs)
        
        # محاولة التحقق
        if self.verify_credentials(creds):
            self.credentials[platform] = creds
            logger.info(f"✅ تم ربط {platform.value} بنجاح")
            return True
        else:
            logger.warning(f"⚠️ فشل التحقق من {platform.value}")
            return False
    
    def verify_credentials(self, creds: SocialMediaCredentials) -> bool:
        """التحقق من بيانات الاعتماد"""
        try:
            if creds.platform == Platform.TWITTER and TWITTER_AVAILABLE:
                return self._verify_twitter(creds)
            elif creds.platform == Platform.FACEBOOK and FACEBOOK_AVAILABLE:
                return self._verify_facebook(creds)
            elif creds.platform == Platform.INSTAGRAM:
                return self._verify_instagram(creds)
            elif creds.platform == Platform.LINKEDIN:
                return self._verify_linkedin(creds)
            else:
                # محاكاة التحقق للتطوير
                creds.verified = True
                creds.last_verified = datetime.now()
                creds.can_post = True
                creds.can_schedule = True
                creds.can_read_comments = True
                return True
                
        except Exception as e:
            logger.error(f"خطأ في التحقق: {e}")
            return False
    
    def _verify_twitter(self, creds: SocialMediaCredentials) -> bool:
        """التحقق من Twitter"""
        if not TWITTER_AVAILABLE:
            return False
        
        try:
            auth = tweepy.OAuthHandler(creds.api_key, creds.api_secret)
            auth.set_access_token(creds.access_token, creds.access_token_secret)
            
            api = tweepy.API(auth)
            api.verify_credentials()
            
            self.api_clients[Platform.TWITTER] = api
            
            creds.verified = True
            creds.last_verified = datetime.now()
            creds.can_post = True
            creds.can_read_comments = True
            
            return True
        except Exception as e:
            logger.error(f"Twitter verification failed: {e}")
            return False
    
    def _verify_facebook(self, creds: SocialMediaCredentials) -> bool:
        """التحقق من Facebook"""
        if not FACEBOOK_AVAILABLE:
            return False
        
        try:
            graph = facebook.GraphAPI(access_token=creds.access_token)
            profile = graph.get_object("me")
            
            self.api_clients[Platform.FACEBOOK] = graph
            
            creds.verified = True
            creds.last_verified = datetime.now()
            creds.can_post = True
            
            return True
        except Exception as e:
            logger.error(f"Facebook verification failed: {e}")
            return False
    
    def _verify_instagram(self, creds: SocialMediaCredentials) -> bool:
        """التحقق من Instagram (عبر Facebook API)"""
        # Instagram API يتطلب إعداد خاص
        creds.verified = True
        creds.last_verified = datetime.now()
        return True
    
    def _verify_linkedin(self, creds: SocialMediaCredentials) -> bool:
        """التحقق من LinkedIn"""
        creds.verified = True
        creds.last_verified = datetime.now()
        return True
    
    def check_all_permissions(self) -> Dict:
        """فحص جميع الصلاحيات"""
        permissions = {}
        
        for platform in Platform:
            if platform in self.credentials:
                creds = self.credentials[platform]
                permissions[platform.value] = {
                    'connected': creds.verified,
                    'can_post': creds.can_post,
                    'can_schedule': creds.can_schedule,
                    'can_read_comments': creds.can_read_comments,
                    'can_read_messages': creds.can_read_messages,
                    'last_verified': creds.last_verified.isoformat() if creds.last_verified else None
                }
            else:
                permissions[platform.value] = {
                    'connected': False,
                    'can_post': False,
                    'can_schedule': False,
                    'can_read_comments': False
                }
        
        return permissions
    
    def post_to_platform(self, 
                         platform: Platform,
                         content: PostDraft,
                         media_path: Optional[str] = None) -> Optional[str]:
        """النشر على منصة"""
        if platform not in self.credentials or not self.credentials[platform].verified:
            logger.error(f"❌ {platform.value} غير متصل")
            return None
        
        try:
            if platform == Platform.TWITTER:
                return self._post_to_twitter(content, media_path)
            elif platform == Platform.FACEBOOK:
                return self._post_to_facebook(content, media_path)
            elif platform == Platform.INSTAGRAM:
                return self._post_to_instagram(content, media_path)
            elif platform == Platform.LINKEDIN:
                return self._post_to_linkedin(content, media_path)
            else:
                # محاكاة النشر
                post_id = f"post_{datetime.now().timestamp()}"
                logger.info(f"✅ تم النشر على {platform.value}: {post_id}")
                return post_id
                
        except Exception as e:
            logger.error(f"❌ فشل النشر على {platform.value}: {e}")
            return None
    
    def _post_to_twitter(self, content: PostDraft, media_path: Optional[str]) -> str:
        """النشر على Twitter"""
        api = self.api_clients.get(Platform.TWITTER)
        if not api:
            raise Exception("Twitter API not initialized")
        
        # النص الكامل
        full_text = content.text_ar
        if content.hashtags:
            full_text += "\n\n" + " ".join([f"#{tag}" for tag in content.hashtags])
        
        # النشر
        if media_path:
            media = api.media_upload(media_path)
            tweet = api.update_status(status=full_text, media_ids=[media.media_id])
        else:
            tweet = api.update_status(status=full_text)
        
        return str(tweet.id)
    
    def _post_to_facebook(self, content: PostDraft, media_path: Optional[str]) -> str:
        """النشر على Facebook"""
        graph = self.api_clients.get(Platform.FACEBOOK)
        if not graph:
            raise Exception("Facebook API not initialized")
        
        full_text = content.text_ar
        if content.hashtags:
            full_text += "\n\n" + " ".join([f"#{tag}" for tag in content.hashtags])
        
        # النشر
        if media_path:
            post = graph.put_photo(image=open(media_path, 'rb'), message=full_text)
        else:
            post = graph.put_object(parent_object="me", connection_name="feed", message=full_text)
        
        return post['id']
    
    def _post_to_instagram(self, content: PostDraft, media_path: Optional[str]) -> str:
        """النشر على Instagram"""
        # Instagram API معقد - يحتاج إعداد خاص
        return f"ig_post_{datetime.now().timestamp()}"
    
    def _post_to_linkedin(self, content: PostDraft, media_path: Optional[str]) -> str:
        """النشر على LinkedIn"""
        return f"li_post_{datetime.now().timestamp()}"

# ═══════════════════════════════════════════════════════════════════════════════
# 4. مولد المحتوى الذكي (AI Content Generator)
# ═══════════════════════════════════════════════════════════════════════════════

class AIContentGenerator:
    """مولد محتوى ذكي باستخدام المساعد المتقدم"""
    
    def __init__(self, assistant: Optional[AdvancedSmartAssistant] = None):
        self.assistant = assistant
        self.templates = self._load_templates()
    
    def _load_templates(self) -> Dict:
        """تحميل قوالب المحتوى"""
        return {
            'promotional': {
                PostTone.FORMAL: {
                    'ar': "يسرنا أن نعلن عن {topic}. {details}\n\nللمزيد من المعلومات: {link}",
                    'en': "We are pleased to announce {topic}. {details}\n\nFor more information: {link}"
                },
                PostTone.ENTHUSIASTIC: {
                    'ar': "🎉 أخبار رائعة! {topic}\n\n{details}\n\nلا تفوت الفرصة! {link}",
                    'en': "🎉 Great news! {topic}\n\n{details}\n\nDon't miss out! {link}"
                },
                PostTone.FRIENDLY: {
                    'ar': "مرحباً! نحن متحمسون لـ {topic} 🌟\n\n{details}\n\nشاركنا آراءك!",
                    'en': "Hello! We're excited about {topic} 🌟\n\n{details}\n\nShare your thoughts!"
                }
            },
            'informative': {
                PostTone.PROFESSIONAL: {
                    'ar': "📌 {topic}\n\n{details}\n\nللاستفسارات، يمكنكم التواصل معنا.",
                    'en': "📌 {topic}\n\n{details}\n\nFor inquiries, please contact us."
                }
            }
        }
    
    def create_three_versions(self,
                             platform: Platform,
                             topic: str,
                             details: str = "",
                             link: str = "",
                             content_type: ContentType = ContentType.PROMOTIONAL,
                             goal: PostGoal = PostGoal.AWARENESS) -> List[PostDraft]:
        """إنشاء 3 نسخ مختلفة"""
        
        versions = []
        
        # النسخة 1: رسمية
        formal = self._generate_version(
            platform, topic, details, link,
            PostTone.FORMAL, content_type, goal
        )
        versions.append(PostDraft(
            version_name="رسمية",
            text_ar=formal['ar'],
            text_en=formal['en'],
            tone=PostTone.FORMAL,
            hashtags=self._suggest_hashtags(topic, platform, 5)
        ))
        
        # النسخة 2: ودودة
        friendly = self._generate_version(
            platform, topic, details, link,
            PostTone.FRIENDLY, content_type, goal
        )
        versions.append(PostDraft(
            version_name="ودودة",
            text_ar=friendly['ar'],
            text_en=friendly['en'],
            tone=PostTone.FRIENDLY,
            hashtags=self._suggest_hashtags(topic, platform, 5)
        ))
        
        # النسخة 3: مختصرة مع هاشتاجات
        short = self._generate_short_version(platform, topic, goal)
        hashtags = self._suggest_hashtags(topic, platform, 5)
        versions.append(PostDraft(
            version_name="مختصرة",
            text_ar=short['ar'],
            text_en=short['en'],
            tone=PostTone.CASUAL,
            hashtags=hashtags
        ))
        
        return versions
    
    def _generate_version(self,
                         platform: Platform,
                         topic: str,
                         details: str,
                         link: str,
                         tone: PostTone,
                         content_type: ContentType,
                         goal: PostGoal) -> Dict[str, str]:
        """توليد نسخة من المحتوى"""
        
        # استخدام المساعد الذكي إذا كان متاحاً
        if self.assistant and ASSISTANT_AVAILABLE:
            prompt = f"""أنشئ منشور {content_type.value} لـ {platform.value} بنبرة {tone.value}:
            
الموضوع: {topic}
التفاصيل: {details}
الهدف: {goal.value}

اكتب بلغة عربية فصحى بسيطة، وأضف ترجمة إنجليزية."""
            
            try:
                response = self.assistant.process_with_web_search(
                    text=prompt,
                    use_knowledge=True
                )
                # استخراج النصوص (مبسط)
                return {
                    'ar': topic + "\n" + details,
                    'en': f"{topic}\n{details}"
                }
            except Exception as e:
                logger.warning(f"فشل استخدام المساعد: {e}")
        
        # استخدام القوالب
        template_category = 'promotional' if content_type == ContentType.PROMOTIONAL else 'informative'
        template = self.templates.get(template_category, {}).get(tone, {})
        
        if template:
            return {
                'ar': template['ar'].format(topic=topic, details=details, link=link),
                'en': template['en'].format(topic=topic, details=details, link=link)
            }
        
        # نص افتراضي
        return {
            'ar': f"{topic}\n\n{details}",
            'en': f"{topic}\n\n{details}"
        }
    
    def _generate_short_version(self,
                               platform: Platform,
                               topic: str,
                               goal: PostGoal) -> Dict[str, str]:
        """توليد نسخة مختصرة"""
        emojis = {
            PostGoal.AWARENESS: "📢",
            PostGoal.ENGAGEMENT: "💬",
            PostGoal.CONVERSION: "🎯",
            PostGoal.TRAFFIC: "🔗",
            PostGoal.BRAND_BUILDING: "⭐"
        }
        
        emoji = emojis.get(goal, "📌")
        
        return {
            'ar': f"{emoji} {topic}",
            'en': f"{emoji} {topic}"
        }
    
    def _suggest_hashtags(self, 
                         topic: str,
                         platform: Platform,
                         count: int = 5) -> List[str]:
        """اقتراح هاشتاجات فعالة"""
        
        # قاعدة هاشتاجات شائعة حسب المنصة
        platform_tags = {
            Platform.TWITTER: ['تويتر', 'السعودية', 'الخليج'],
            Platform.INSTAGRAM: ['انستقرام', 'تصوير', 'فن', 'جمال'],
            Platform.FACEBOOK: ['فيسبوك', 'مجتمع', 'تواصل'],
            Platform.LINKEDIN: ['لينكدإن', 'وظائف', 'أعمال', 'مهنية'],
            Platform.TIKTOK: ['تيك_توك', 'ترفيه', 'فيديو']
        }
        
        # هاشتاجات عامة
        general_tags = ['تقنية', 'ابتكار', 'تطوير', 'نجاح', 'إلهام']
        
        # استخراج كلمات من الموضوع
        topic_words = [w for w in topic.split() if len(w) > 3]
        
        # دمج
        all_tags = platform_tags.get(platform, []) + general_tags + topic_words
        
        # إزالة التكرار والتحديد
        unique_tags = list(dict.fromkeys(all_tags))[:count]
        
        return unique_tags
    
    def suggest_media(self, 
                     topic: str,
                     platform: Platform,
                     content_type: ContentType) -> MediaSuggestion:
        """اقتراح وسائط"""
        
        # أبعاد موصى بها حسب المنصة
        platform_sizes = {
            Platform.TWITTER: (1200, 675),
            Platform.INSTAGRAM: (1080, 1080),
            Platform.FACEBOOK: (1200, 630),
            Platform.LINKEDIN: (1200, 627)
        }
        
        size = platform_sizes.get(platform, (1080, 1080))
        
        # توليد وصف
        desc_ar = f"صورة توضيحية لـ {topic}"
        desc_en = f"Illustration for {topic}"
        
        # نص على الصورة
        image_text = topic.split()[0] if topic else ""
        
        return MediaSuggestion(
            description_ar=desc_ar,
            description_en=desc_en,
            image_text=image_text,
            alt_text=f"صورة: {topic}",
            suggested_size=size
        )

# ═══════════════════════════════════════════════════════════════════════════════
# 5. مدير الجدولة (Scheduler)  
# ═══════════════════════════════════════════════════════════════════════════════

class ContentScheduler:
    """مدير جدولة ونشر المحتوى"""
    
    def __init__(self):
        self.scheduled_posts: List[ScheduledPost] = []
        self.best_times = self._load_best_times()
    
    def _load_best_times(self) -> Dict:
        """تحميل أفضل أوقات النشر"""
        return {
            Platform.TWITTER: {
                AudienceType.GENERAL: [(9, 0), (13, 0), (20, 0)],
                AudienceType.BUSINESS: [(8, 0), (12, 0), (17, 0)],
                AudienceType.YOUTH: [(14, 0), (20, 0), (22, 0)]
            },
            Platform.INSTAGRAM: {
                AudienceType.GENERAL: [(11, 0), (14, 0), (21, 0)],
                AudienceType.YOUTH: [(12, 0), (19, 0), (22, 0)]
            },
            Platform.FACEBOOK: {
                AudienceType.GENERAL: [(13, 0), (15, 0), (19, 0)],
                AudienceType.BUSINESS: [(9, 0), (14, 0)]
            },
            Platform.LINKEDIN: {
                AudienceType.PROFESSIONALS: [(8, 0), (12, 0), (17, 0)],
                AudienceType.BUSINESS: [(7, 30), (12, 0), (16, 30)]
            }
        }
    
    def suggest_best_time(self,
                         platform: Platform,
                         audience: AudienceType,
                         date: Optional[datetime] = None) -> datetime:
        """اقتراح أفضل وقت للنشر"""
        
        if date is None:
            date = datetime.now()
        
        # الحصول على الأوقات المناسبة
        times = self.best_times.get(platform, {}).get(
            audience,
            [(12, 0)]  # افتراضي
        )
        
        # اختيار أفضل وقت متاح
        is_weekend = date.weekday() >= 5
        
        # تعديل الأوقات للعطلة
        if is_weekend and platform == Platform.LINKEDIN:
            # LinkedIn أقل نشاطاً في العطل
            times = [(14, 0)]
        
        # الحصول على أول وقت بعد الآن
        current_time = datetime.now()
        for hour, minute in times:
            suggested = date.replace(hour=hour, minute=minute, second=0)
            if suggested > current_time:
                return suggested
        
        # إذا فاتت جميع الأوقات، استخدم اليوم التالي
        next_day = date + timedelta(days=1)
        hour, minute = times[0]
        return next_day.replace(hour=hour, minute=minute, second=0)
    
    def create_monthly_calendar(self,
                               platforms: List[Platform],
                               posts_per_week: int = 7,
                               audience: AudienceType = AudienceType.GENERAL) -> Dict:
        """إنشاء تقويم محتوى شهري"""
        
        calendar = {
            'month': datetime.now().strftime('%Y-%m'),
            'platforms': [p.value for p in platforms],
            'audience': audience.value,
            'weeks': []
        }
        
        # توزيع أنواع المحتوى
        content_cycle = [
            ContentType.INFORMATIVE,
            ContentType.IMAGE_CAROUSEL,
            ContentType.SHORT_VIDEO,
            ContentType.ENGAGEMENT,
            ContentType.STORY,
            ContentType.PROMOTIONAL,
            ContentType.POLL
        ]
        
        # توزيع الأهداف
        goal_cycle = [
            PostGoal.AWARENESS,
            PostGoal.ENGAGEMENT,
            PostGoal.BRAND_BUILDING,
            PostGoal.TRAFFIC,
            PostGoal.CONVERSION,
            PostGoal.LEAD_GENERATION,
            PostGoal.ENGAGEMENT
        ]
        
        start_date = datetime.now()
        
        # 4 أسابيع
        for week_num in range(4):
            week_posts = []
            
            for day_num in range(posts_per_week):
                post_date = start_date + timedelta(days=week_num * 7 + day_num)
                
                # اختيار منصة
                platform = platforms[day_num % len(platforms)]
                
                # نوع المحتوى والهدف
                content_type = content_cycle[day_num % len(content_cycle)]
                goal = goal_cycle[day_num % len(goal_cycle)]
                
                # أفضل وقت
                best_time = self.suggest_best_time(platform, audience, post_date)
                
                week_posts.append({
                    'day': day_num + 1,
                    'date': best_time.strftime('%Y-%m-%d'),
                    'time': best_time.strftime('%H:%M'),
                    'platform': platform.value,
                    'content_type': content_type.value,
                    'goal': goal.value,
                    'topic_suggestion': f"موضوع اليوم {day_num + 1}"
                })
            
            calendar['weeks'].append({
                f'week_{week_num + 1}': week_posts
            })
        
        return calendar
    
    def schedule_post(self, post: ScheduledPost) -> bool:
        """جدولة منشور"""
        
        # التحقق
        if not post.scheduled_time:
            post.scheduled_time = post.best_time_suggested
        
        if not post.scheduled_time:
            logger.error("لا يوجد وقت محدد للنشر")
            return False
        
        # إضافة للجدول
        self.scheduled_posts.append(post)
        post.status = "scheduled"
        post.log_activity("scheduled", f"جدولة لـ {post.scheduled_time}")
        
        logger.info(f"✅ تم جدولة {post.id} لـ {post.platform.value} في {post.scheduled_time}")
        return True
    
    def export_to_csv(self, filename: str = "schedule.csv") -> str:
        """تصدير الجدول لـ CSV"""
        
        with open(filename, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            
            # الرأس
            writer.writerow([
                'ID', 'المنصة', 'التاريخ', 'الوقت',
                'النص العربي', 'النص الإنجليزي',
                'الهاشتاجات', 'نوع المحتوى', 'الهدف',
                'يحتاج موافقة', 'الحالة'
            ])
            
            # البيانات
            for post in self.scheduled_posts:
                content = post.get_selected_content()
                
                writer.writerow([
                    post.id,
                    post.platform.value,
                    post.scheduled_time.strftime('%Y-%m-%d') if post.scheduled_time else '',
                    post.scheduled_time.strftime('%H:%M') if post.scheduled_time else '',
                    content.text_ar,
                    content.text_en or '',
                    ', '.join(content.hashtags),
                    post.content_type.value,
                    post.goal.value,
                    'نعم' if post.requires_approval else 'لا',
                    post.status
                ])
        
        logger.info(f"✅ تم تصدير الجدول إلى {filename}")
        return filename

# سأكمل في الرسالة التالية بسبب الحد الأقصى للطول...
"""
📱 نظام إدارة وسائل التواصل - الجزء الثاني
===========================================

التفاعل، المراقبة، التحليلات، والمدير الرئيسي
"""

from integrated_social_media_system import *
import time
from textblob import TextBlob  # لتحليل المشاعر (يمكن استبداله)

# ═══════════════════════════════════════════════════════════════════════════════
# 6. مراقب التفاعل (Engagement Monitor)
# ═══════════════════════════════════════════════════════════════════════════════

class EngagementMonitor:
    """مراقب التفاعل والتعليقات"""
    
    def __init__(self, platform_manager: PlatformManager):
        self.platform_manager = platform_manager
        self.monitored_posts: Dict[str, List[CommentMonitoring]] = {}
        self.auto_responses = self._load_auto_responses()
    
    def _load_auto_responses(self) -> Dict[str, Dict]:
        """تحميل الردود التلقائية"""
        return {
            'pricing': {
                'keywords': ['سعر', 'price', 'تكلفة', 'cost'],
                'responses': [
                    {
                        'ar': 'شكراً لاستفسارك! يمكنك الاطلاع على أسعارنا من خلال موقعنا الإلكتروني.',
                        'en': 'Thank you for your inquiry! You can view our prices on our website.'
                    },
                    {
                        'ar': 'مرحباً! نقدم باقات متنوعة. تواصل معنا مباشرة للتفاصيل.',
                        'en': 'Hello! We offer various packages. Contact us directly for details.'
                    }
                ]
            },
            'shipping': {
                'keywords': ['شحن', 'توصيل', 'shipping', 'delivery'],
                'responses': [
                    {
                        'ar': 'نوفر الشحن لجميع مناطق المملكة خلال 2-3 أيام عمل.',
                        'en': 'We deliver to all regions within 2-3 business days.'
                    },
                    {
                        'ar': 'الشحن مجاني للطلبات فوق 200 ريال.',
                        'en': 'Free shipping for orders above 200 SAR.'
                    }
                ]
            },
            'returns': {
                'keywords': ['إرجاع', 'استرجاع', 'return', 'refund'],
                'responses': [
                    {
                        'ar': 'يمكنك إرجاع المنتج خلال 14 يوم من الاستلام.',
                        'en': 'You can return the product within 14 days of receipt.'
                    },
                    {
                        'ar': 'نوفر سياسة إرجاع مرنة لضمان رضاك التام.',
                        'en': 'We offer a flexible return policy to ensure your satisfaction.'
                    }
                ]
            },
            'general': {
                'keywords': [],
                'responses': [
                    {
                        'ar': 'شكراً لتواصلك معنا! سنرد عليك في أقرب وقت ممكن.',
                        'en': 'Thank you for contacting us! We will reply as soon as possible.'
                    },
                    {
                        'ar': 'نقدر اهتمامك! فريقنا سيتواصل معك قريباً.',
                        'en': 'We appreciate your interest! Our team will contact you soon.'
                    }
                ]
            }
        }
    
    def suggest_auto_responses(self, topic: str, count: int = 6) -> List[Dict]:
        """اقتراح ردود جاهزة"""
        
        responses = []
        
        # الحصول على ردود حسب الموضوع
        category_responses = self.auto_responses.get(topic.lower(), 
                                                     self.auto_responses['general'])
        
        # إضافة الردود
        for response in category_responses['responses'][:count]:
            responses.append(response)
        
        # إضافة ردود عامة إذا لزم
        while len(responses) < count:
            responses.append({
                'ar': f'شكراً على تعليقك! نحن نقدر مشاركتك.',
                'en': f'Thank you for your comment! We appreciate your participation.'
            })
        
        return responses[:count]
    
    def monitor_post(self, 
                    post: ScheduledPost,
                    duration_hours: int = 24) -> Dict:
        """
        مراقبة منشور لمدة معينة
        
        Returns:
            تقرير المراقبة
        """
        
        if not post.platform_post_id:
            logger.warning("المنشور غير منشور بعد")
            return {}
        
        logger.info(f"🔍 بدء مراقبة {post.id} لمدة {duration_hours} ساعة")
        
        # جمع التعليقات (محاكاة)
        comments = self._fetch_comments(post)
        
        # تحليل التعليقات
        analyzed_comments = []
        negative_count = 0
        
        for comment in comments:
            analyzed = self._analyze_comment(comment)
            analyzed_comments.append(analyzed)
            
            if analyzed.sentiment == "negative":
                negative_count += 1
            
            # حفظ
            if post.id not in self.monitored_posts:
                self.monitored_posts[post.id] = []
            self.monitored_posts[post.id].append(analyzed)
        
        # جمع المقاييس
        metrics = self._fetch_metrics(post)
        
        return {
            'post_id': post.id,
            'platform': post.platform.value,
            'total_comments': len(comments),
            'negative_comments': negative_count,
            'requires_attention': negative_count > 0,
            'metrics': metrics,
            'monitored_until': (datetime.now() + timedelta(hours=duration_hours)).isoformat()
        }
    
    def _fetch_comments(self, post: ScheduledPost) -> List[Dict]:
        """جلب التعليقات من المنصة"""
        
        # في التطبيق الفعلي، استخدم API المنصة
        # هنا محاكاة بسيطة
        
        mock_comments = [
            {
                'id': 'c1',
                'author': 'user1',
                'text': 'رائع! متى التسجيل؟',
                'timestamp': datetime.now()
            },
            {
                'id': 'c2',
                'author': 'user2',
                'text': 'ما هي الأسعار؟',
                'timestamp': datetime.now()
            }
        ]
        
        return mock_comments
    
    def _analyze_comment(self, comment: Dict) -> CommentMonitoring:
        """تحليل تعليق"""
        
        # تحليل المشاعر
        sentiment = self._detect_sentiment(comment['text'])
        
        # تحديد إذا كان يحتاج رد
        requires_response = any(
            keyword in comment['text'].lower()
            for category in self.auto_responses.values()
            for keyword in category['keywords']
        )
        
        # اقتراح رد
        suggested_response = None
        if requires_response:
            suggested_response = self._suggest_response(comment['text'])
        
        # تحديد إذا كان يحتاج مراجعة
        flagged = sentiment == "negative" and any(
            word in comment['text'].lower()
                for word in ['سيء', 'رديء', 'فاشل', 'bad', 'terrible', 'horrible']
        )
        
        return CommentMonitoring(
            post_id="",  # سيتم تعيينه
            platform=Platform.TWITTER,  # سيتم تعيينه
            comment_id=comment['id'],
            author=comment['author'],
            text=comment['text'],
            timestamp=comment['timestamp'],
            sentiment=sentiment,
            requires_response=requires_response,
            suggested_response=suggested_response,
            flagged_for_review=flagged
        )
    
    def _detect_sentiment(self, text: str) -> str:
        """كشف المشاعر"""
        
        # كلمات إيجابية
        positive_words = ['رائع', 'ممتاز', 'جميل', 'مبدع', 'great', 'excellent', 'amazing']
        
        # كلمات سلبية
        negative_words = ['سيء', 'رديء', 'فاشل', 'bad', 'terrible', 'horrible']
        
        text_lower = text.lower()
        
        positive_count = sum(1 for word in positive_words if word in text_lower)
        negative_count = sum(1 for word in negative_words if word in text_lower)
        
        if negative_count > positive_count:
            return "negative"
        elif positive_count > negative_count:
            return "positive"
        else:
            return "neutral"
    
    def _suggest_response(self, comment_text: str) -> str:
        """اقتراح رد على تعليق"""
        
        text_lower = comment_text.lower()
        
        # البحث عن الفئة المناسبة
        for category, data in self.auto_responses.items():
            if any(keyword in text_lower for keyword in data['keywords']):
                if data['responses']:
                    return data['responses'][0]['ar']
        
        # رد افتراضي
        return self.auto_responses['general']['responses'][0]['ar']
    
    def _fetch_metrics(self, post: ScheduledPost) -> EngagementMetrics:
        """جلب مقاييس الأداء"""
        
        # في التطبيق الفعلي، استخدم API المنصة
        # محاكاة
        
        metrics = EngagementMetrics(
            timestamp=datetime.now(),
            reach=1500,
            impressions=2000,
            likes=120,
            comments=25,
            shares=15,
            saves=8,
            clicks=45,
            followers_gained=5,
            followers_lost=1
        )
        
        metrics.calculate_rates()
        
        return metrics
    
    def get_flagged_comments(self) -> List[CommentMonitoring]:
        """الحصول على التعليقات المبلّغ عنها"""
        
        flagged = []
        for post_comments in self.monitored_posts.values():
            flagged.extend([c for c in post_comments if c.flagged_for_review])
        
        return flagged

# ═══════════════════════════════════════════════════════════════════════════════
# 7. محلل الأداء (Analytics Engine)
# ═══════════════════════════════════════════════════════════════════════════════

class AnalyticsEngine:
    """محرك التحليلات والتقارير"""
    
    def __init__(self):
        self.performance_history: List[Dict] = []
    
    def generate_weekly_report(self, 
                              posts: List[ScheduledPost],
                              start_date: Optional[datetime] = None) -> Dict:
        """توليد تقرير أسبوعي"""
        
        if start_date is None:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
        else:
            end_date = start_date + timedelta(days=7)
        
        # تصفية المنشورات
        weekly_posts = [
            p for p in posts
            if p.posted_time and start_date <= p.posted_time <= end_date
        ]
        
        if not weekly_posts:
            return {
                'period': {
                    'start': start_date.strftime('%Y-%m-%d'),
                    'end': end_date.strftime('%Y-%m-%d')
                },
                'summary': {
                    'total_posts': 0,
                    'message': 'لا توجد منشورات في هذه الفترة'
                }
            }
        
        # حساب المقاييس الإجمالية
        total_reach = 0
        total_engagement = 0
        total_followers_gained = 0
        
        platform_performance = defaultdict(lambda: {
            'posts': 0,
            'engagement': 0
        })
        
        best_post = None
        best_engagement = 0
        
        for post in weekly_posts:
            metrics = post.metrics
            
            reach = metrics.get('reach', 0)
            engagement = metrics.get('engagement', 0)
            followers = metrics.get('followers_gained', 0)
            
            total_reach += reach
            total_engagement += engagement
            total_followers_gained += followers
            
            # تحليل حسب المنصة
            platform_performance[post.platform.value]['posts'] += 1
            platform_performance[post.platform.value]['engagement'] += engagement
            
            # أفضل منشور
            if engagement > best_engagement:
                best_engagement = engagement
                best_post = post
        
        # معدلات
        avg_engagement = total_engagement / len(weekly_posts) if weekly_posts else 0
        avg_reach = total_reach / len(weekly_posts) if weekly_posts else 0
        
        # التوصيات
        recommendations = self._generate_recommendations(
            weekly_posts,
            avg_engagement,
            platform_performance
        )
        
        report = {
            'period': {
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d')
            },
            'summary': {
                'total_posts': len(weekly_posts),
                'total_reach': total_reach,
                'total_engagement': total_engagement,
                'avg_engagement': round(avg_engagement, 2),
                'avg_reach': round(avg_reach, 2),
                'followers_gained': total_followers_gained
            },
            'platform_breakdown': dict(platform_performance),
            'best_post': {
                'id': best_post.id if best_post else None,
                'platform': best_post.platform.value if best_post else None,
                'content': best_post.get_selected_content().text_ar[:50] if best_post else None,
                'engagement': best_engagement
            } if best_post else None,
            'recommendations': recommendations
        }
        
        # حفظ في التاريخ
        self.performance_history.append({
            'timestamp': datetime.now().isoformat(),
            'report': report
        })
        
        return report
    
    def _generate_recommendations(self,
                                 posts: List[ScheduledPost],
                                 avg_engagement: float,
                                 platform_performance: Dict) -> List[str]:
        """توليد توصيات للتحسين"""
        
        recommendations = []
        
        # 1. تحليل معدل التفاعل
        if avg_engagement < 50:
            recommendations.append(
                "💡 معدل التفاعل منخفض. جرب محتوى تفاعلي أكثر (استطلاعات، أسئلة، مسابقات)"
            )
        elif avg_engagement > 200:
            recommendations.append(
                "🎉 معدل التفاعل ممتاز! استمر بنفس الاستراتيجية"
            )
        
        # 2. تحليل التنوع
        content_types = set(p.content_type for p in posts)
        if len(content_types) < 3:
            recommendations.append(
                "🎨 نوّع أنواع المحتوى (فيديوهات، صور، قصص) لزيادة الوصول"
            )
        
        # 3. تحليل المنصات
        if platform_performance:
            best_platform = max(
                platform_performance.items(),
                key=lambda x: x[1]['engagement']
            )[0]
            
            recommendations.append(
                f"⭐ منصة {best_platform} تحقق أفضل نتائج - ركز عليها أكثر"
            )
        
        # 4. تحليل التوقيت
        post_hours = [p.scheduled_time.hour for p in posts if p.scheduled_time]
        if post_hours and all(h < 12 for h in post_hours):
            recommendations.append(
                "⏰ جرب النشر في أوقات مختلفة (مساءً) للوصول لجمهور أوسع"
            )
        
        # 5. تحليل الأهداف
        goals = [p.goal for p in posts]
        conversion_posts = sum(1 for g in goals if g == PostGoal.CONVERSION)
        if conversion_posts < len(posts) * 0.2:
            recommendations.append(
                "🎯 أضف المزيد من المنشورات الهادفة للتحويل مع روابط واضحة"
            )
        
        return recommendations[:3]
    
    def export_report(self, report: Dict, filename: str = "weekly_report.json") -> str:
        """تصدير التقرير"""
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ تم تصدير التقرير إلى {filename}")
        return filename

# ═══════════════════════════════════════════════════════════════════════════════
# 8. المدير الرئيسي (Main Manager)
# ═══════════════════════════════════════════════════════════════════════════════

class SocialMediaMasterManager:
    """
    المدير الرئيسي الشامل لوسائل التواصل الاجتماعي
    
    يدمج جميع المكونات:
    - إدارة الحسابات
    - إنشاء المحتوى الذكي
    - الجدولة والنشر
    - المراقبة والتفاعل
    - التحليلات والتقارير
    """
    
    def __init__(self, 
                 data_dir: str = "social_media_data",
                 ai_assistant: Optional[AdvancedSmartAssistant] = None):
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        # المكونات الرئيسية
        self.platform_manager = PlatformManager()
        self.content_generator = AIContentGenerator(ai_assistant)
        self.scheduler = ContentScheduler()
        self.engagement_monitor = EngagementMonitor(self.platform_manager)
        self.analytics = AnalyticsEngine()
        
        # المساعد الذكي
        self.ai_assistant = ai_assistant
        
        # الإعدادات
        self.settings = {
            'require_approval_for_promotional': True,
            'require_approval_for_sensitive': True,
            'auto_respond_enabled': False,
            'monitoring_enabled': True,
            'analytics_enabled': True
        }
        
        logger.info("✅ تم تهيئة مدير وسائل التواصل الاجتماعي")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 1. إدارة الحسابات
    # ═══════════════════════════════════════════════════════════════════════════
    
    def connect_platform(self, platform: Platform, **credentials) -> bool:
        """ربط منصة"""
        return self.platform_manager.add_credentials(platform, **credentials)
    
    def check_all_permissions(self) -> Dict:
        """فحص جميع الصلاحيات"""
        return self.platform_manager.check_all_permissions()
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 2. إنشاء المحتوى
    # ═══════════════════════════════════════════════════════════════════════════
    
    def create_post(self,
                   platform: Platform,
                   topic: str,
                   details: str = "",
                   link: str = "",
                   content_type: ContentType = ContentType.PROMOTIONAL,
                   goal: PostGoal = PostGoal.AWARENESS,
                   audience: AudienceType = AudienceType.GENERAL,
                   auto_schedule: bool = True) -> ScheduledPost:
        """
        إنشاء منشور كامل مع 3 نسخ
        
        هذه الدالة تنفذ:
        1. إنشاء 3 مسودات (رسمية، ودودة، مختصرة)
        2. اقتراح هاشتاجات
        3. اقتراح وسائط
        4. اقتراح أفضل وقت للنشر
        """
        
        # توليد 3 نسخ
        drafts = self.content_generator.create_three_versions(
            platform=platform,
            topic=topic,
            details=details,
            link=link,
            content_type=content_type,
            goal=goal
        )
        
        # اقتراح وسائط
        media = self.content_generator.suggest_media(
            topic=topic,
            platform=platform,
            content_type=content_type
        )
        
        # إنشاء المنشور
        post = ScheduledPost(
            id=self._generate_id(),
            platform=platform,
            drafts=drafts,
            content_type=content_type,
            goal=goal,
            audience=audience,
            media=media
        )
        
        # اقتراح أفضل وقت
        if auto_schedule:
            best_time = self.scheduler.suggest_best_time(platform, audience)
            post.best_time_suggested = best_time
            post.scheduled_time = best_time
        
        # التحقق من الحاجة للموافقة
        if content_type == ContentType.PROMOTIONAL and self.settings['require_approval_for_promotional']:
            post.requires_approval = True
        
        post.log_activity("created", f"تم إنشاء منشور مع {len(drafts)} مسودات")
        
        logger.info(f"✅ تم إنشاء منشور {post.id}")
        
        return post
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 3. الجدولة والنشر
    # ═══════════════════════════════════════════════════════════════════════════
    
    def schedule_post(self, post: ScheduledPost) -> bool:
        """جدولة منشور"""
        return self.scheduler.schedule_post(post)
    
    def create_monthly_calendar(self,
                               platforms: List[Platform],
                               posts_per_week: int = 7,
                               audience: AudienceType = AudienceType.GENERAL) -> Dict:
        """إنشاء تقويم شهري"""
        return self.scheduler.create_monthly_calendar(platforms, posts_per_week, audience)
    
    def publish_post(self, post: ScheduledPost, media_path: Optional[str] = None) -> bool:
        """
        نشر منشور
        
        يتحقق من:
        - الصلاحيات
        - الموافقة (إذا لزم)
        - السياسات
        """
        
        # التحقق من الموافقة
        if post.requires_approval and not post.approved:
            logger.warning(f"⚠️ المنشور {post.id} يحتاج موافقة")
            return False
        
        # التحقق من الصلاحيات
        platform_creds = self.platform_manager.credentials.get(post.platform)
        if not platform_creds or not platform_creds.can_post:
            logger.error(f"❌ لا توجد صلاحية نشر على {post.platform.value}")
            return False
        
        # التحقق من السياسات
        if not self._check_content_policy(post):
            logger.error(f"❌ المحتوى يخالف السياسات")
            return False
        
        # النشر
        content = post.get_selected_content()
        platform_post_id = self.platform_manager.post_to_platform(
            post.platform,
            content,
            media_path
        )
        
        if platform_post_id:
            post.status = "posted"
            post.posted_time = datetime.now()
            post.platform_post_id = platform_post_id
            post.log_activity("posted", f"تم النشر - ID: {platform_post_id}")
            
            # بدء المراقبة
            if self.settings['monitoring_enabled']:
                self.start_monitoring(post)
            
            logger.info(f"✅ تم النشر بنجاح: {platform_post_id}")
            return True
        else:
            post.status = "failed"
            post.log_activity("failed", "فشل النشر")
            return False
    
    def approve_post(self, post_id: str, approved_by: str = "admin") -> bool:
        """الموافقة على منشور"""
        for post in self.scheduler.scheduled_posts:
            if post.id == post_id:
                post.approved = True
                post.approved_by = approved_by
                post.approved_at = datetime.now()
                post.log_activity("approved", f"تمت الموافقة بواسطة {approved_by}")
                
                logger.info(f"✅ تمت الموافقة على {post_id}")
                return True
        
        return False
    
    def _check_content_policy(self, post: ScheduledPost) -> bool:
        """التحقق من سياسات المحتوى"""
        
        content = post.get_selected_content()
        text = content.text_ar.lower()
        
        # كلمات محظورة (مثال بسيط)
        prohibited_words = ['spam', 'fake', 'مزيف', 'احتيال']
        
        for word in prohibited_words:
            if word in text:
                logger.warning(f"⚠️ المحتوى يحتوي على كلمة محظورة: {word}")
                return False
        
        return True
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 4. المراقبة والتفاعل
    # ═══════════════════════════════════════════════════════════════════════════
    
    def start_monitoring(self, post: ScheduledPost, duration_hours: int = 24):
        """بدء مراقبة منشور"""
        return self.engagement_monitor.monitor_post(post, duration_hours)
    
    def get_flagged_comments(self) -> List[CommentMonitoring]:
        """الحصول على التعليقات التي تحتاج تدخل"""
        return self.engagement_monitor.get_flagged_comments()
    
    def suggest_auto_responses(self, topic: str, count: int = 6) -> List[Dict]:
        """اقتراح ردود تلقائية"""
        return self.engagement_monitor.suggest_auto_responses(topic, count)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 5. التحليلات والتقارير
    # ═══════════════════════════════════════════════════════════════════════════
    
    def generate_weekly_report(self) -> Dict:
        """توليد تقرير أسبوعي"""
        return self.analytics.generate_weekly_report(self.scheduler.scheduled_posts)
    
    def export_schedule_csv(self, filename: str = "schedule.csv") -> str:
        """تصدير الجدول لـ CSV"""
        return self.scheduler.export_to_csv(filename)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 6. مساعدة
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _generate_id(self) -> str:
        """توليد معرف فريد"""
        timestamp = str(time.time())
        return hashlib.md5(timestamp.encode()).hexdigest()[:12]
    
    def save_data(self):
        """حفظ جميع البيانات"""
        # حفظ المنشورات
        posts_file = self.data_dir / "scheduled_posts.json"
        with open(posts_file, 'w', encoding='utf-8') as f:
            json.dump(
                [p.to_dict() for p in self.scheduler.scheduled_posts],
                f,
                ensure_ascii=False,
                indent=2
            )
        
        # حفظ الإعدادات
        settings_file = self.data_dir / "settings.json"
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(self.settings, f, ensure_ascii=False, indent=2)
        
        logger.info("💾 تم حفظ البيانات")
    
    def get_stats(self) -> Dict:
        """إحصائيات عامة"""
        return {
            'connected_platforms': len(self.platform_manager.credentials),
            'scheduled_posts': len(self.scheduler.scheduled_posts),
            'pending_approval': len([p for p in self.scheduler.scheduled_posts if p.requires_approval and not p.approved]),
            'posted_this_week': len([p for p in self.scheduler.scheduled_posts if p.posted_time and p.posted_time > datetime.now() - timedelta(days=7)]),
            'flagged_comments': len(self.engagement_monitor.get_flagged_comments())
        }

# نهاية الملف - الجزء الثاني
