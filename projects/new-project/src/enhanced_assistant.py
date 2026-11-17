"""
🤖 المساعد الذكي المتقدم - Enhanced Smart Assistant
==========================================================

إضافات جديدة:
1. قاعدة المعرفة الخارجية (Knowledge Base)
2. دعم اللغات المتعددة (Multilingual)
3. نظام التوصيات الذكي (Recommendations)
4. التعلم من التقييمات (Feedback Learning)
5. معالجة الملفات المتعددة (Document Processing)
"""

import torch
import torch.nn.functional as F
from typing import Dict, List, Optional, Tuple, Union, Any
from pathlib import Path
from PIL import Image
import json
import logging
import time
import numpy as np
from dataclasses import dataclass, field
from collections import defaultdict
import hashlib
import pickle

# استيراد المساعد الأساسي
from smart_assistant import (
    SmartAssistant, 
    TaskType, 
    AssistantResponse,
    ConversationMemory,
    IntentClassifier
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. قاعدة المعرفة الخارجية (Knowledge Base)
# ═══════════════════════════════════════════════════════════════════════════════

class KnowledgeBase:
    """
    قاعدة معرفة بسيطة للحقائق والمعلومات
    يمكن توسيعها لاستخدام FAISS أو Elasticsearch
    """
    
    def __init__(self, kb_path: Optional[str] = None):
        self.knowledge: Dict[str, List[Dict]] = defaultdict(list)
        self.categories = set()
        
        if kb_path and Path(kb_path).exists():
            self.load_from_file(kb_path)
        else:
            self._initialize_default_kb()
    
    def _initialize_default_kb(self):
        """تهيئة قاعدة معرفة افتراضية"""
        default_knowledge = {
            'general': [
                {'topic': 'AI', 'content': 'الذكاء الاصطناعي هو محاكاة الذكاء البشري بواسطة الآلات'},
                {'topic': 'ML', 'content': 'التعلم الآلي هو فرع من الذكاء الاصطناعي يركز على التعلم من البيانات'},
            ],
            'tech': [
                {'topic': 'Python', 'content': 'Python لغة برمجة عالية المستوى سهلة التعلم'},
                {'topic': 'PyTorch', 'content': 'PyTorch مكتبة للتعلم العميق مفتوحة المصدر'},
            ],
            'facts': [
                {'topic': 'Earth', 'content': 'الأرض هي الكوكب الثالث من الشمس'},
                {'topic': 'Water', 'content': 'الماء يغطي حوالي 71% من سطح الأرض'},
            ]
        }
        
        for category, items in default_knowledge.items():
            self.add_category(category)
            for item in items:
                self.add_knowledge(category, item['topic'], item['content'])
    
    def add_category(self, category: str):
        """إضافة فئة جديدة"""
        self.categories.add(category)
    
    def add_knowledge(self, category: str, topic: str, content: str, metadata: Dict = None):
        """إضافة معرفة جديدة"""
        if category not in self.categories:
            self.add_category(category)
        
        self.knowledge[category].append({
            'topic': topic,
            'content': content,
            'metadata': metadata or {},
            'timestamp': time.time()
        })
    
    def search(self, query: str, category: Optional[str] = None, top_k: int = 3) -> List[Dict]:
        """
        البحث في قاعدة المعرفة
        
        Args:
            query: استعلام البحث
            category: فئة محددة (اختياري)
            top_k: عدد النتائج
        
        Returns:
            قائمة بأفضل النتائج المطابقة
        """
        query_lower = query.lower()
        results = []
        
        # تحديد الفئات للبحث
        search_categories = [category] if category else list(self.knowledge.keys())
        
        for cat in search_categories:
            if cat not in self.knowledge:
                continue
            
            for item in self.knowledge[cat]:
                # حساب مدى التطابق (بسيط - يمكن تحسينه)
                score = 0
                
                # التطابق في الموضوع
                if query_lower in item['topic'].lower():
                    score += 3
                
                # التطابق في المحتوى
                if query_lower in item['content'].lower():
                    score += 2
                
                # التطابق في الكلمات
                query_words = query_lower.split()
                for word in query_words:
                    if len(word) > 2:
                        if word in item['topic'].lower():
                            score += 1
                        if word in item['content'].lower():
                            score += 0.5
                
                if score > 0:
                    results.append({
                        'category': cat,
                        'topic': item['topic'],
                        'content': item['content'],
                        'score': score,
                        'metadata': item.get('metadata', {})
                    })
        
        # ترتيب حسب النقاط
        results.sort(key=lambda x: x['score'], reverse=True)
        
        return results[:top_k]
    
    def save_to_file(self, filepath: str):
        """حفظ قاعدة المعرفة"""
        data = {
            'knowledge': dict(self.knowledge),
            'categories': list(self.categories)
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ تم حفظ قاعدة المعرفة: {filepath}")
    
    def load_from_file(self, filepath: str):
        """تحميل قاعدة المعرفة"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.knowledge = defaultdict(list, data['knowledge'])
        self.categories = set(data['categories'])
        
        logger.info(f"✓ تم تحميل قاعدة المعرفة: {filepath}")
    
    def get_stats(self) -> Dict:
        """إحصائيات قاعدة المعرفة"""
        total_items = sum(len(items) for items in self.knowledge.values())
        
        return {
            'total_categories': len(self.categories),
            'total_items': total_items,
            'categories': {cat: len(items) for cat, items in self.knowledge.items()}
        }

# ═══════════════════════════════════════════════════════════════════════════════
# 2. نظام التوصيات الذكي (Recommendation Engine)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class UserProfile:
    """ملف تعريف المستخدم"""
    user_id: str
    preferences: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict] = field(default_factory=list)
    interests: Dict[str, float] = field(default_factory=dict)  # {category: score}
    
    def update_interest(self, category: str, weight: float = 1.0):
        """تحديث الاهتمامات"""
        if category not in self.interests:
            self.interests[category] = 0
        self.interests[category] += weight
    
    def get_top_interests(self, top_k: int = 5) -> List[Tuple[str, float]]:
        """الحصول على أهم الاهتمامات"""
        sorted_interests = sorted(
            self.interests.items(), 
            key=lambda x: x[1], 
            reverse=True
        )
        return sorted_interests[:top_k]

class RecommendationEngine:
    """
    محرك توصيات ذكي يتعلم من تفضيلات المستخدم
    """
    
    def __init__(self, knowledge_base: KnowledgeBase):
        self.kb = knowledge_base
        self.user_profiles: Dict[str, UserProfile] = {}
    
    def get_or_create_profile(self, user_id: str) -> UserProfile:
        """الحصول على أو إنشاء ملف مستخدم"""
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = UserProfile(user_id=user_id)
        return self.user_profiles[user_id]
    
    def recommend(self, 
                 user_id: str,
                 query: Optional[str] = None,
                 context: Optional[str] = None,
                 top_k: int = 5) -> List[Dict]:
        """
        توليد توصيات للمستخدم
        
        Args:
            user_id: معرف المستخدم
            query: استعلام (اختياري)
            context: سياق إضافي (اختياري)
            top_k: عدد التوصيات
        
        Returns:
            قائمة التوصيات
        """
        profile = self.get_or_create_profile(user_id)
        recommendations = []
        
        # 1. توصيات بناءً على الاستعلام
        if query:
            query_results = self.kb.search(query, top_k=top_k)
            recommendations.extend(query_results)
        
        # 2. توصيات بناءً على الاهتمامات
        top_interests = profile.get_top_interests(top_k=3)
        for interest, score in top_interests:
            interest_results = self.kb.search(interest, top_k=2)
            for result in interest_results:
                result['reason'] = f"بناءً على اهتمامك بـ {interest}"
                result['score'] *= 0.8  # تقليل الوزن قليلاً
                recommendations.append(result)
        
        # 3. إزالة التكرارات
        seen = set()
        unique_recommendations = []
        for rec in recommendations:
            key = f"{rec['category']}_{rec['topic']}"
            if key not in seen:
                seen.add(key)
                unique_recommendations.append(rec)
        
        # 4. ترتيب وتحديد العدد
        unique_recommendations.sort(key=lambda x: x['score'], reverse=True)
        
        return unique_recommendations[:top_k]
    
    def record_interaction(self, 
                          user_id: str,
                          category: str,
                          topic: str,
                          rating: Optional[int] = None):
        """تسجيل تفاعل المستخدم"""
        profile = self.get_or_create_profile(user_id)
        
        # تحديث الاهتمامات
        weight = 1.0
        if rating:
            weight = rating / 5.0  # تقييم من 1-5
        
        profile.update_interest(category, weight)
        
        # تسجيل في التاريخ
        profile.history.append({
            'category': category,
            'topic': topic,
            'rating': rating,
            'timestamp': time.time()
        })

# ═══════════════════════════════════════════════════════════════════════════════
# 3. التعلم من التقييمات (Feedback Learning)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FeedbackEntry:
    """إدخال تقييم"""
    query: str
    image_hash: Optional[str]
    response: str
    predicted_class: int
    rating: int  # 1-5
    correction: Optional[str]
    timestamp: float
    metadata: Dict = field(default_factory=dict)

class FeedbackDatabase:
    """قاعدة بيانات التقييمات"""
    
    def __init__(self, db_path: str = "feedback.pkl"):
        self.db_path = db_path
        self.feedback: List[FeedbackEntry] = []
        
        if Path(db_path).exists():
            self.load()
    
    def add(self, feedback: FeedbackEntry):
        """إضافة تقييم"""
        self.feedback.append(feedback)
    
    def save(self):
        """حفظ قاعدة البيانات"""
        with open(self.db_path, 'wb') as f:
            pickle.dump(self.feedback, f)
        logger.info(f"✓ تم حفظ {len(self.feedback)} تقييم")
    
    def load(self):
        """تحميل قاعدة البيانات"""
        with open(self.db_path, 'rb') as f:
            self.feedback = pickle.load(f)
        logger.info(f"✓ تم تحميل {len(self.feedback)} تقييم")
    
    def get_stats(self) -> Dict:
        """إحصائيات التقييمات"""
        if not self.feedback:
            return {'total': 0, 'avg_rating': 0}
        
        ratings = [f.rating for f in self.feedback]
        
        return {
            'total': len(self.feedback),
            'avg_rating': np.mean(ratings),
            'rating_distribution': {
                i: ratings.count(i) for i in range(1, 6)
            },
            'corrections_provided': sum(1 for f in self.feedback if f.correction)
        }
    
    def get_low_rated(self, threshold: int = 3) -> List[FeedbackEntry]:
        """الحصول على التقييمات المنخفضة"""
        return [f for f in self.feedback if f.rating < threshold]

class FeedbackLearner:
    """
    نظام التعلم من تقييمات المستخدمين
    """
    
    def __init__(self, db_path: str = "feedback.pkl"):
        self.db = FeedbackDatabase(db_path)
    
    def collect_feedback(self,
                        query: str,
                        response: AssistantResponse,
                        rating: int,
                        correction: Optional[str] = None,
                        metadata: Dict = None):
        """
        جمع تقييم من المستخدم
        
        Args:
            query: السؤال الأصلي
            response: استجابة المساعد
            rating: التقييم (1-5)
            correction: تصحيح من المستخدم (اختياري)
            metadata: معلومات إضافية
        """
        # حساب hash للصورة إذا وجدت
        image_hash = None
        if response.input_image:
            image_hash = hashlib.md5(response.input_image.encode()).hexdigest()
        
        feedback = FeedbackEntry(
            query=query,
            image_hash=image_hash,
            response=response.prediction,
            predicted_class=0,  # سيتم تحديثه لاحقاً
            rating=rating,
            correction=correction,
            timestamp=time.time(),
            metadata=metadata or {}
        )
        
        self.db.add(feedback)
        self.db.save()
        
        logger.info(f"✓ تم تسجيل تقييم: {rating}/5")
        
        return feedback
    
    def analyze_feedback(self) -> Dict:
        """تحليل التقييمات"""
        stats = self.db.get_stats()
        low_rated = self.db.get_low_rated(threshold=3)
        
        analysis = {
            **stats,
            'needs_improvement': len(low_rated),
            'improvement_areas': self._identify_improvement_areas(low_rated)
        }
        
        return analysis
    
    def _identify_improvement_areas(self, low_rated: List[FeedbackEntry]) -> List[str]:
        """تحديد مجالات التحسين"""
        areas = []
        
        if len(low_rated) > 10:
            areas.append("الدقة العامة تحتاج تحسين")
        
        # تحليل الأنماط في التقييمات المنخفضة
        common_words = defaultdict(int)
        for feedback in low_rated:
            for word in feedback.query.split():
                if len(word) > 3:
                    common_words[word.lower()] += 1
        
        if common_words:
            top_words = sorted(common_words.items(), key=lambda x: x[1], reverse=True)[:3]
            areas.append(f"صعوبة في: {', '.join(w for w, _ in top_words)}")
        
        return areas
    
    def get_training_data(self, min_rating: int = 4) -> List[Dict]:
        """
        استخراج بيانات تدريب من التقييمات العالية
        
        Args:
            min_rating: الحد الأدنى للتقييم
        
        Returns:
            بيانات تدريب محتملة
        """
        high_quality = [
            f for f in self.db.feedback 
            if f.rating >= min_rating
        ]
        
        training_data = []
        for feedback in high_quality:
            training_data.append({
                'text': feedback.query,
                'image': feedback.image_hash,
                'label': feedback.correction if feedback.correction else feedback.response,
                'quality_score': feedback.rating / 5.0
            })
        
        return training_data

# ═══════════════════════════════════════════════════════════════════════════════
# 4. معالجة الملفات المتعددة (Document Processing)
# ═══════════════════════════════════════════════════════════════════════════════

class DocumentProcessor:
    """
    معالج مستندات يدعم أنواع ملفات متعددة
    """
    
    SUPPORTED_FORMATS = {
        '.txt': 'text',
        '.md': 'markdown',
        '.json': 'json',
        '.csv': 'csv',
        '.pdf': 'pdf',
        '.docx': 'docx',
        '.xlsx': 'excel'
    }
    
    def __init__(self):
        self.processors = {
            'text': self._process_text,
            'markdown': self._process_text,
            'json': self._process_json,
            'csv': self._process_csv,
        }
    
    def can_process(self, filepath: str) -> bool:
        """التحقق من إمكانية معالجة الملف"""
        ext = Path(filepath).suffix.lower()
        return ext in self.SUPPORTED_FORMATS
    
    def process(self, filepath: str) -> Dict[str, Any]:
        """
        معالجة ملف
        
        Returns:
            {
                'type': نوع الملف,
                'content': المحتوى المستخرج,
                'metadata': معلومات إضافية,
                'summary': ملخص (إذا أمكن)
            }
        """
        path = Path(filepath)
        
        if not path.exists():
            raise FileNotFoundError(f"الملف غير موجود: {filepath}")
        
        ext = path.suffix.lower()
        
        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(f"نوع ملف غير مدعوم: {ext}")
        
        file_type = self.SUPPORTED_FORMATS[ext]
        processor = self.processors.get(file_type)
        
        if not processor:
            return {
                'type': file_type,
                'content': f"نوع الملف {file_type} مدعوم لكن المعالج غير متوفر حالياً",
                'metadata': {'filepath': str(path)},
                'summary': None
            }
        
        try:
            result = processor(filepath)
            result['metadata']['filepath'] = str(path)
            result['metadata']['size'] = path.stat().st_size
            return result
        except Exception as e:
            logger.error(f"خطأ في معالجة {filepath}: {e}")
            return {
                'type': file_type,
                'content': f"خطأ في المعالجة: {str(e)}",
                'metadata': {'filepath': str(path), 'error': str(e)},
                'summary': None
            }
    
    def _process_text(self, filepath: str) -> Dict:
        """معالجة ملف نصي"""
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # ملخص بسيط
        lines = content.split('\n')
        summary = f"{len(lines)} سطر، {len(content)} حرف"
        
        return {
            'type': 'text',
            'content': content,
            'metadata': {
                'lines': len(lines),
                'characters': len(content),
                'words': len(content.split())
            },
            'summary': summary
        }
    
    def _process_json(self, filepath: str) -> Dict:
        """معالجة ملف JSON"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return {
            'type': 'json',
            'content': json.dumps(data, ensure_ascii=False, indent=2),
            'metadata': {
                'data_type': type(data).__name__,
                'size': len(str(data))
            },
            'summary': f"بيانات JSON من نوع {type(data).__name__}"
        }
    
    def _process_csv(self, filepath: str) -> Dict:
        """معالجة ملف CSV"""
        import csv
        
        rows = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        headers = list(rows[0].keys()) if rows else []
        
        return {
            'type': 'csv',
            'content': json.dumps(rows[:10], ensure_ascii=False, indent=2),  # أول 10 صفوف
            'metadata': {
                'rows': len(rows),
                'columns': headers,
                'total_columns': len(headers)
            },
            'summary': f"جدول CSV: {len(rows)} صف، {len(headers)} عمود"
        }

# ═══════════════════════════════════════════════════════════════════════════════
# 5. المساعد الذكي المحسّن (Enhanced Smart Assistant)
# ═══════════════════════════════════════════════════════════════════════════════

class EnhancedSmartAssistant(SmartAssistant):
    """
    المساعد الذكي مع جميع التحسينات الجديدة
    """
    
    def __init__(self,
                 model_path: str,
                 vocab_path: str,
                 config_path: Optional[str] = None,
                 task_configs: Optional[Dict] = None,
                 kb_path: Optional[str] = None,
                 enable_recommendations: bool = True,
                 enable_feedback: bool = True):
        """
        Args:
            model_path: مسار النموذج
            vocab_path: مسار القاموس
            config_path: مسار الإعدادات
            task_configs: إعدادات المهام
            kb_path: مسار قاعدة المعرفة
            enable_recommendations: تفعيل التوصيات
            enable_feedback: تفعيل نظام التقييمات
        """
        # تهيئة المساعد الأساسي
        super().__init__(model_path, vocab_path, config_path, task_configs)
        
        # قاعدة المعرفة
        self.kb = KnowledgeBase(kb_path)
        logger.info(f"✓ قاعدة المعرفة: {self.kb.get_stats()['total_items']} عنصر")
        
        # نظام التوصيات
        self.recommendations_enabled = enable_recommendations
        if enable_recommendations:
            self.recommender = RecommendationEngine(self.kb)
            logger.info("✓ نظام التوصيات مفعّل")
        
        # نظام التقييمات
        self.feedback_enabled = enable_feedback
        if enable_feedback:
            self.feedback_learner = FeedbackLearner()
            logger.info("✓ نظام التقييمات مفعّل")
        
        # معالج المستندات
        self.doc_processor = DocumentProcessor()
        logger.info("✓ معالج المستندات جاهز")
        
        # معرف المستخدم الافتراضي
        self.current_user_id = "default_user"
    
    def set_user(self, user_id: str):
        """تعيين المستخدم الحالي"""
        self.current_user_id = user_id
        logger.info(f"✓ تم تعيين المستخدم: {user_id}")
    
    def search_knowledge(self, query: str, top_k: int = 3) -> List[Dict]:
        """البحث في قاعدة المعرفة"""
        return self.kb.search(query, top_k=top_k)
    
    def get_recommendations(self, 
                          query: Optional[str] = None,
                          top_k: int = 5) -> List[Dict]:
        """
        الحصول على توصيات للمستخدم
        
        Args:
            query: استعلام (اختياري)
            top_k: عدد التوصيات
        
        Returns:
            قائمة التوصيات
        """
        if not self.recommendations_enabled:
            return []
        
        context = self.memory.get_context(n_previous=3)
        
        recommendations = self.recommender.recommend(
            user_id=self.current_user_id,
            query=query,
            context=context,
            top_k=top_k
        )
        
        return recommendations
    
    def process_with_knowledge(self,
                              text: Optional[str] = None,
                              image: Optional[Union[str, Image.Image]] = None,
                              task: Optional[TaskType] = None,
                              use_context: bool = True,
                              use_knowledge: bool = True) -> AssistantResponse:
        """
        معالجة مع استخدام قاعدة المعرفة
        
        Args:
            text: النص
            image: الصورة
            task: نوع المهمة
            use_context: استخدام السياق
            use_knowledge: استخدام قاعدة المعرفة
        
        Returns:
            استجابة محسّنة
        """
        # البحث في قاعدة المعرفة
        knowledge_context = ""
        if use_knowledge and text:
            kb_results = self.search_knowledge(text, top_k=2)
            if kb_results:
                knowledge_context = "معلومات إضافية:\n"
                for result in kb_results:
                    knowledge_context += f"- {result['content']}\n"
        
        # دمج السياق المعرفي
        enhanced_text = text
        if knowledge_context:
            enhanced_text = f"{text}\n\n{knowledge_context}"
        
        # المعالجة الأساسية
        response = self.process(
            text=enhanced_text,
            image=image,
            task=task,
            use_context=use_context
        )
        
        # إضافة المعلومات المعرفية للـ metadata
        if knowledge_context:
            response.metadata['knowledge_used'] = True
            response.metadata['knowledge_sources'] = len(kb_results)
        
        # تسجيل التفاعل للتوصيات
        if self.recommendations_enabled and task:
            self.recommender.record_interaction(
                user_id=self.current_user_id,
                category=task.value,
                topic=text[:50] if text else "image_only"
            )
        
        return response
    
    def add_feedback(self,
                    response: AssistantResponse,
                    rating: int,
                    correction: Optional[str] = None):
        """
        إضافة تقييم للاستجابة
        
        Args:
            response: الاستجابة الأصلية
            rating: التقييم (1-5)
            correction: تصحيح (اختياري)
        """
        if not self.feedback_enabled:
            logger.warning("نظام التقييمات غير مفعّل")
            return
        
        self.feedback_learner.collect_feedback(
            query=response.input_text or "",
            response=response,
            rating=rating,
            correction=correction,
            metadata={'user_id': self.current_user_id}
        )
        
        # تحديث توصيات المستخدم بناءً على التقييم
        if self.recommendations_enabled and rating >= 4:
            self.recommender.record_interaction(
                user_id=self.current_user_id,
                category=response.task,
                topic=response.prediction,
                rating=rating
            )
        
        logger.info(f"✓ تم تسجيل تقييم: {rating}/5")
    
    def process_document(self, filepath: str) -> Dict:
        """
        معالجة ملف
        
        Args:
            filepath: مسار الملف
        
        Returns:
            محتوى ومعلومات الملف
        """
        return self.doc_processor.process(filepath)
    
    def get_enhanced_stats(self) -> Dict:
        """إحصائيات محسّنة"""
        base_stats = self.get_stats()
        
        enhanced = {
            **base_stats,
            'knowledge_base': self.kb.get_stats(),
        }
        
        if self.feedback_enabled:
            enhanced['feedback'] = self.feedback_learner.db.get_stats()
        
        if self.recommendations_enabled:
            enhanced['user_profiles'] = len(self.recommender.user_profiles)
        
        return enhanced
    
    def save_knowledge_base(self, filepath: str):
        """حفظ قاعدة المعرفة"""
        self.kb.save_to_file(filepath)
    
    def add_knowledge(self, category: str, topic: str, content: str):
        """إضافة معرفة جديدة"""
        self.kb.add_knowledge(category, topic, content)
        logger.info(f"✓ تمت إضافة معرفة: {topic} في {category}")

# ═══════════════════════════════════════════════════════════════════════════════
# 6. أمثلة الاستخدام
# ═══════════════════════════════════════════════════════════════════════════════

def example_knowledge_base():
    """مثال: استخدام قاعدة المعرفة"""
    print("\n" + "="*60)
    print("مثال 1: قاعدة المعرفة")
    print("="*60 + "\n")
    
    # إنشاء قاعدة معرفة
    kb = KnowledgeBase()
    
    # إضافة معرفة جديدة
    kb.add_knowledge(
        category='programming',
        topic='Python',
        content='Python لغة برمجة عالية المستوى، سهلة التعلم، وتستخدم في الذكاء الاصطناعي'
    )
    
    kb.add_knowledge(
        category='programming',
        topic='JavaScript',
        content='JavaScript لغة برمجة للويب، تعمل في المتصفح والخادم'
    )
    
    # البحث
    results = kb.search("لغة برمجة")
    
    print("نتائج البحث عن 'لغة برمجة':")
    for i, result in enumerate(results, 1):
        print(f"\n{i}. {result['topic']} ({result['category']})")
        print(f"   {result['content']}")
        print(f"   النقاط: {result['score']}")
    
    # الإحصائيات
    stats = kb.get_stats()
    print(f"\n📊 الإحصائيات:")
    print(f"   الفئات: {stats['total_categories']}")
    print(f"   العناصر: {stats['total_items']}")

def example_recommendations():
    """مثال: نظام التوصيات"""
    print("\n" + "="*60)
    print("مثال 2: نظام التوصيات")
    print("="*60 + "\n")
    
    kb = KnowledgeBase()
    recommender = RecommendationEngine(kb)
    
    user_id = "user123"
    
    # تسجيل اهتمامات المستخدم
    print("تسجيل اهتمامات المستخدم...")
    recommender.record_interaction(user_id, 'tech', 'AI', rating=5)
    recommender.record_interaction(user_id, 'tech', 'Python', rating=4)
    recommender.record_interaction(user_id, 'general', 'ML', rating=5)
    
    # الحصول على توصيات
    recommendations = recommender.recommend(
        user_id=user_id,
        query="تعلم البرمجة",
        top_k=3
    )
    
    print("\n🎯 التوصيات:")
    for i, rec in enumerate(recommendations, 1):
        print(f"\n{i}. {rec['topic']} ({rec['category']})")
        print(f"   {rec['content']}")
        if 'reason' in rec:
            print(f"   السبب: {rec['reason']}")

def example_feedback():
    """مثال: نظام التقييمات"""
    print("\n" + "="*60)
    print("مثال 3: نظام التقييمات")
    print("="*60 + "\n")
    
    learner = FeedbackLearner()
    
    # إضافة تقييمات
    print("إضافة تقييمات...")
    
    from smart_assistant import AssistantResponse
    
    response1 = AssistantResponse(
        task="تحليل المشاعر",
        input_text="أحب هذا المنتج",
        input_image=None,
        prediction="إيجابي",
        confidence=0.95,
        probabilities={},
        explanation="تحليل جيد",
        processing_time=0.1,
        metadata={}
    )
    
    learner.collect_feedback(
        query="أحب هذا المنتج",
        response=response1,
        rating=5,
        correction=None
    )
    
    learner.collect_feedback(
        query="لست متأكداً",
        response=response1,
        rating=2,
        correction="يجب أن يكون محايد"
    )
    
    # تحليل التقييمات
    analysis = learner.analyze_feedback()
    
    print("\n📊 تحليل التقييمات:")
    print(f"   الإجمالي: {analysis['total']}")
    print(f"   متوسط التقييم: {analysis['avg_rating']:.2f}/5")
    print(f"   يحتاج تحسين: {analysis['needs_improvement']}")
    
    if analysis['improvement_areas']:
        print(f"   مجالات التحسين:")
        for area in analysis['improvement_areas']:
            print(f"     - {area}")

def example_document_processing():
    """مثال: معالجة المستندات"""
    print("\n" + "="*60)
    print("مثال 4: معالجة المستندات")
    print("="*60 + "\n")
    
    processor = DocumentProcessor()
    
    # إنشاء ملف تجريبي
    test_file = "test_document.txt"
    with open(test_file, 'w', encoding='utf-8') as f:
        f.write("هذا ملف تجريبي\n")
        f.write("يحتوي على عدة أسطر\n")
        f.write("لاختبار معالج المستندات\n")
    
    # معالجة الملف
    result = processor.process(test_file)
    
    print(f"📄 نوع الملف: {result['type']}")
    print(f"📝 المحتوى:\n{result['content']}")
    print(f"📊 الملخص: {result['summary']}")
    print(f"ℹ️  معلومات:")
    for key, value in result['metadata'].items():
        print(f"   {key}: {value}")
    
    # حذف الملف التجريبي
    Path(test_file).unlink()

if __name__ == "__main__":
    print("\n🚀 أمثلة المساعد الذكي المحسّن\n")
    
    # تشغيل الأمثلة
    example_knowledge_base()
    example_recommendations()
    example_feedback()
    example_document_processing()
    
    print("\n" + "="*60)
    print("✅ انتهت جميع الأمثلة!")
    print("="*60)
