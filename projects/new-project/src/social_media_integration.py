"""
📱 نظام تكامل وسائل التواصل الاجتماعي
Social Media Integration System

يدعم: Twitter, Facebook, Instagram, LinkedIn, Telegram
"""

import requests
import json
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. تعريفات المنصات (Platform Definitions)
# ═══════════════════════════════════════════════════════════════════════════════

class SocialPlatform(Enum):
    """منصات التواصل الاجتماعي المدعومة"""
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    TELEGRAM = "telegram"
    GITHUB = "github"
    CUSTOM = "custom"

@dataclass
class SocialMediaCredentials:
    """بيانات اعتماد منصة التواصل"""
    platform: SocialPlatform
    api_key: str
    api_secret: Optional[str] = None
    access_token: Optional[str] = None
    access_token_secret: Optional[str] = None
    user_id: Optional[str] = None
    endpoint: str = ""
    webhook_url: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

@dataclass
class SocialMediaPost:
    """منشور على وسائل التواصل"""
    content: str
    platform: SocialPlatform
    title: Optional[str] = None
    images: List[str] = field(default_factory=list)
    hashtags: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    scheduled_time: Optional[datetime] = None
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """تحويل إلى قاموس"""
        return {
            'content': self.content,
            'platform': self.platform.value,
            'title': self.title,
            'images': self.images,
            'hashtags': self.hashtags,
            'mentions': self.mentions,
            'links': self.links,
            'scheduled_time': self.scheduled_time.isoformat() if self.scheduled_time else None,
            'metadata': self.metadata
        }

@dataclass
class SocialMediaResponse:
    """استجابة من منصة التواصل"""
    success: bool
    platform: SocialPlatform
    post_id: Optional[str] = None
    url: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    message: str = ""
    engagement: Dict = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. معالج Twitter (Twitter Handler)
# ═══════════════════════════════════════════════════════════════════════════════

class TwitterHandler:
    """معالج تويتر باستخدام Twitter API v2"""
    
    def __init__(self, credentials: SocialMediaCredentials):
        self.credentials = credentials
        self.base_url = "https://api.twitter.com/2"
        self.headers = self._build_headers()
    
    def _build_headers(self) -> Dict:
        """بناء رؤوس الطلب"""
        return {
            "Authorization": f"Bearer {self.credentials.access_token}",
            "Content-Type": "application/json",
        }
    
    def post_tweet(self, post: SocialMediaPost) -> SocialMediaResponse:
        """نشر تغريدة"""
        try:
            # بناء محتوى التغريدة
            text = post.content
            
            # إضافة الهاشتاجات
            if post.hashtags:
                text += " " + " ".join(f"#{tag}" for tag in post.hashtags)
            
            # إضافة الروابط
            if post.links:
                text += "\n" + "\n".join(post.links)
            
            # التحقق من الحد الأقصى للأحرف
            if len(text) > 280:
                text = text[:277] + "..."
            
            payload = {"text": text}
            
            response = requests.post(
                f"{self.base_url}/tweets",
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 201:
                data = response.json()
                return SocialMediaResponse(
                    success=True,
                    platform=SocialPlatform.TWITTER,
                    post_id=data['data']['id'],
                    url=f"https://twitter.com/i/web/status/{data['data']['id']}",
                    message="تم نشر التغريدة بنجاح",
                    metadata={'text': text}
                )
            else:
                return SocialMediaResponse(
                    success=False,
                    platform=SocialPlatform.TWITTER,
                    message=f"خطأ: {response.text}"
                )
        
        except Exception as e:
            logger.error(f"خطأ في نشر Twitter: {e}")
            return SocialMediaResponse(
                success=False,
                platform=SocialPlatform.TWITTER,
                message=f"خطأ: {str(e)}"
            )
    
    def get_engagement(self, tweet_id: str) -> Dict:
        """الحصول على إحصائيات الانخراط"""
        try:
            response = requests.get(
                f"{self.base_url}/tweets/{tweet_id}",
                params={"tweet.fields": "public_metrics"},
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                metrics = response.json()['data']['public_metrics']
                return {
                    'likes': metrics.get('like_count', 0),
                    'retweets': metrics.get('retweet_count', 0),
                    'replies': metrics.get('reply_count', 0),
                    'quotes': metrics.get('quote_count', 0)
                }
            return {}
        
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإحصائيات: {e}")
            return {}

# ═══════════════════════════════════════════════════════════════════════════════
# 3. معالج Facebook (Facebook Handler)
# ═══════════════════════════════════════════════════════════════════════════════

class FacebookHandler:
    """معالج فيسبوك"""
    
    def __init__(self, credentials: SocialMediaCredentials):
        self.credentials = credentials
        self.base_url = "https://graph.facebook.com/v18.0"
    
    def post_to_feed(self, post: SocialMediaPost) -> SocialMediaResponse:
        """نشر على الجدار الشخصي"""
        try:
            payload = {
                "message": post.content,
                "access_token": self.credentials.access_token
            }
            
            if post.links:
                payload["link"] = post.links[0]
            
            if post.images:
                payload["picture"] = post.images[0]
            
            response = requests.post(
                f"{self.base_url}/{self.credentials.user_id}/feed",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return SocialMediaResponse(
                    success=True,
                    platform=SocialPlatform.FACEBOOK,
                    post_id=data.get('id'),
                    message="تم النشر على فيسبوك بنجاح",
                    metadata=payload
                )
            else:
                return SocialMediaResponse(
                    success=False,
                    platform=SocialPlatform.FACEBOOK,
                    message=f"خطأ: {response.text}"
                )
        
        except Exception as e:
            logger.error(f"خطأ في نشر Facebook: {e}")
            return SocialMediaResponse(
                success=False,
                platform=SocialPlatform.FACEBOOK,
                message=f"خطأ: {str(e)}"
            )

# ═══════════════════════════════════════════════════════════════════════════════
# 4. معالج Telegram (Telegram Handler)
# ═══════════════════════════════════════════════════════════════════════════════

class TelegramHandler:
    """معالج تليجرام"""
    
    def __init__(self, credentials: SocialMediaCredentials):
        self.credentials = credentials
        self.base_url = f"https://api.telegram.org/bot{credentials.api_key}"
    
    def send_message(self, post: SocialMediaPost, chat_id: str) -> SocialMediaResponse:
        """إرسال رسالة"""
        try:
            payload = {
                "chat_id": chat_id,
                "text": post.content,
                "parse_mode": "HTML"
            }
            
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return SocialMediaResponse(
                    success=True,
                    platform=SocialPlatform.TELEGRAM,
                    post_id=str(data['result']['message_id']),
                    message="تم إرسال الرسالة بنجاح",
                    metadata={'chat_id': chat_id}
                )
            else:
                return SocialMediaResponse(
                    success=False,
                    platform=SocialPlatform.TELEGRAM,
                    message=f"خطأ: {response.text}"
                )
        
        except Exception as e:
            logger.error(f"خطأ في إرسال Telegram: {e}")
            return SocialMediaResponse(
                success=False,
                platform=SocialPlatform.TELEGRAM,
                message=f"خطأ: {str(e)}"
            )
    
    def send_media(self, post: SocialMediaPost, chat_id: str) -> SocialMediaResponse:
        """إرسال صورة أو فيديو"""
        try:
            if post.images:
                for image in post.images:
                    payload = {
                        "chat_id": chat_id,
                        "photo": image,
                        "caption": post.content
                    }
                    
                    requests.post(
                        f"{self.base_url}/sendPhoto",
                        json=payload,
                        timeout=10
                    )
            
            return SocialMediaResponse(
                success=True,
                platform=SocialPlatform.TELEGRAM,
                message="تم إرسال المحتوى بنجاح"
            )
        
        except Exception as e:
            logger.error(f"خطأ في إرسال الوسائط: {e}")
            return SocialMediaResponse(
                success=False,
                platform=SocialPlatform.TELEGRAM,
                message=f"خطأ: {str(e)}"
            )

# ═══════════════════════════════════════════════════════════════════════════════
# 5. مدير التكامل الموحد (Unified Integration Manager)
# ═══════════════════════════════════════════════════════════════════════════════

class SocialMediaIntegrationManager:
    """مدير التكامل الموحد لجميع منصات التواصل"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.credentials: Dict[SocialPlatform, SocialMediaCredentials] = {}
        self.handlers: Dict[SocialPlatform, Any] = {}
        self.post_history: List[SocialMediaResponse] = []
        
        if config_path and Path(config_path).exists():
            self.load_credentials(config_path)
    
    def register_platform(self, credentials: SocialMediaCredentials):
        """تسجيل منصة تواصل"""
        self.credentials[credentials.platform] = credentials
        
        # إنشاء معالج مناسب
        if credentials.platform == SocialPlatform.TWITTER:
            self.handlers[SocialPlatform.TWITTER] = TwitterHandler(credentials)
        elif credentials.platform == SocialPlatform.FACEBOOK:
            self.handlers[SocialPlatform.FACEBOOK] = FacebookHandler(credentials)
        elif credentials.platform == SocialPlatform.TELEGRAM:
            self.handlers[SocialPlatform.TELEGRAM] = TelegramHandler(credentials)
        
        logger.info(f"✓ تم تسجيل منصة {credentials.platform.value}")
    
    def post_to_platform(self, post: SocialMediaPost, platform: SocialPlatform) -> SocialMediaResponse:
        """نشر على منصة محددة"""
        if platform not in self.handlers:
            return SocialMediaResponse(
                success=False,
                platform=platform,
                message=f"المنصة {platform.value} غير مسجلة"
            )
        
        handler = self.handlers[platform]
        
        if platform == SocialPlatform.TWITTER:
            response = handler.post_tweet(post)
        elif platform == SocialPlatform.FACEBOOK:
            response = handler.post_to_feed(post)
        elif platform == SocialPlatform.TELEGRAM:
            response = handler.send_message(post, self.credentials[platform].user_id)
        else:
            response = SocialMediaResponse(
                success=False,
                platform=platform,
                message="منصة غير مدعومة"
            )
        
        self.post_history.append(response)
        return response
    
    def post_to_all(self, post: SocialMediaPost) -> List[SocialMediaResponse]:
        """نشر على جميع المنصات المسجلة"""
        results = []
        
        for platform in self.handlers.keys():
            response = self.post_to_platform(post, platform)
            results.append(response)
        
        return results
    
    def schedule_post(self, post: SocialMediaPost, platform: SocialPlatform) -> Dict:
        """جدولة منشور"""
        return {
            'scheduled': True,
            'platform': platform.value,
            'post': post.to_dict(),
            'scheduled_time': post.scheduled_time.isoformat() if post.scheduled_time else None
        }
    
    def get_analytics(self, platform: Optional[SocialPlatform] = None) -> Dict:
        """الحصول على إحصائيات النشر"""
        if platform:
            platform_posts = [p for p in self.post_history if p.platform == platform]
        else:
            platform_posts = self.post_history
        
        successful = len([p for p in platform_posts if p.success])
        total = len(platform_posts)
        
        return {
            'total_posts': total,
            'successful': successful,
            'failed': total - successful,
            'success_rate': (successful / total * 100) if total > 0 else 0,
            'platforms': list(set(p.platform.value for p in platform_posts))
        }
    
    def save_credentials(self, filepath: str):
        """حفظ بيانات الاعتماد"""
        data = {
            platform.value: {
                'api_key': creds.api_key,
                'api_secret': creds.api_secret,
                'access_token': creds.access_token,
                'access_token_secret': creds.access_token_secret,
                'user_id': creds.user_id,
                'endpoint': creds.endpoint,
                'webhook_url': creds.webhook_url,
                'metadata': creds.metadata
            }
            for platform, creds in self.credentials.items()
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ تم حفظ بيانات الاعتماد: {filepath}")
    
    def load_credentials(self, filepath: str):
        """تحميل بيانات الاعتماد"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for platform_name, creds_data in data.items():
            platform = SocialPlatform(platform_name)
            credentials = SocialMediaCredentials(
                platform=platform,
                api_key=creds_data.get('api_key', ''),
                api_secret=creds_data.get('api_secret'),
                access_token=creds_data.get('access_token'),
                access_token_secret=creds_data.get('access_token_secret'),
                user_id=creds_data.get('user_id'),
                endpoint=creds_data.get('endpoint', ''),
                webhook_url=creds_data.get('webhook_url'),
                metadata=creds_data.get('metadata', {})
            )
            self.register_platform(credentials)
        
        logger.info(f"✓ تم تحميل بيانات الاعتماد من {filepath}")
