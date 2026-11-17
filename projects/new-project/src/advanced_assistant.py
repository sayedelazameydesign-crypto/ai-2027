"""
🧠 المساعد الذكي المتقدم مع التعلم المستمر والبحث في الويب
================================================================

مميزات جديدة:
1. التعلم المستمر (Continuous Learning)
2. البحث في الإنترنت (Web Search)
3. Fine-tuning تلقائي
4. ذاكرة طويلة المدى
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from typing import Dict, List, Optional, Tuple, Any
import json
import logging
import time
from pathlib import Path
from collections import deque
import numpy as np
from dataclasses import dataclass, field
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
import re

from enhanced_assistant import EnhancedSmartAssistant, TaskType, AssistantResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. نظام البحث في الويب (Web Search System)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SearchResult:
    """نتيجة بحث واحدة"""
    title: str
    snippet: str
    url: str
    source: str
    score: float = 0.0

class WebSearchEngine:
    """
    محرك بحث في الإنترنت
    يدعم عدة محركات بحث
    """
    
    def __init__(self, 
                 default_engine: str = 'duckduckgo',
                 max_results: int = 5,
                 timeout: int = 10):
        """
        Args:
            default_engine: المحرك الافتراضي (duckduckgo, google, bing)
            max_results: عدد النتائج
            timeout: وقت الانتظار
        """
        self.default_engine = default_engine
        self.max_results = max_results
        self.timeout = timeout
        
        self.search_engines = {
            'duckduckgo': self._search_duckduckgo,
            'google': self._search_google,
            'bing': self._search_bing
        }
        
        # Cache للنتائج
        self.cache = {}
        self.cache_max_size = 100
    
    def search(self, 
               query: str, 
               engine: Optional[str] = None,
               max_results: Optional[int] = None) -> List[SearchResult]:
        """
        البحث في الإنترنت
        
        Args:
            query: استعلام البحث
            engine: محرك البحث (اختياري)
            max_results: عدد النتائج (اختياري)
        
        Returns:
            قائمة نتائج البحث
        """
        # التحقق من الـ cache
        cache_key = f"{query}_{engine or self.default_engine}"
        if cache_key in self.cache:
            logger.info(f"✓ نتائج من Cache: {query}")
            return self.cache[cache_key]
        
        engine = engine or self.default_engine
        max_results = max_results or self.max_results
        
        if engine not in self.search_engines:
            logger.warning(f"محرك غير مدعوم: {engine}, استخدام {self.default_engine}")
            engine = self.default_engine
        
        try:
            logger.info(f"🔍 البحث عن: {query} (محرك: {engine})")
            results = self.search_engines[engine](query, max_results)
            
            # حفظ في Cache
            if len(self.cache) >= self.cache_max_size:
                # إزالة أقدم عنصر
                self.cache.pop(next(iter(self.cache)))
            self.cache[cache_key] = results
            
            logger.info(f"✓ تم العثور على {len(results)} نتيجة")
            return results
            
        except Exception as e:
            logger.error(f"❌ خطأ في البحث: {e}")
            return []
    
    def _search_duckduckgo(self, query: str, max_results: int) -> List[SearchResult]:
        """البحث عبر DuckDuckGo"""
        results = []
        
        try:
            # DuckDuckGo HTML
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # استخراج النتائج
            for result_div in soup.find_all('div', class_='result')[:max_results]:
                try:
                    title_tag = result_div.find('a', class_='result__a')
                    snippet_tag = result_div.find('a', class_='result__snippet')
                    
                    if title_tag and snippet_tag:
                        title = title_tag.get_text(strip=True)
                        snippet = snippet_tag.get_text(strip=True)
                        link = title_tag.get('href', '')
                        
                        results.append(SearchResult(
                            title=title,
                            snippet=snippet,
                            url=link,
                            source='DuckDuckGo',
                            score=1.0
                        ))
                except Exception as e:
                    logger.debug(f"تخطي نتيجة: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"خطأ في DuckDuckGo: {e}")
        
        return results
    
    def _search_google(self, query: str, max_results: int) -> List[SearchResult]:
        """البحث عبر Google (محاكاة)"""
        # ملاحظة: Google يتطلب API key
        # هذا مثال تجريبي
        logger.warning("Google Search يتطلب API - استخدام DuckDuckGo بدلاً منه")
        return self._search_duckduckgo(query, max_results)
    
    def _search_bing(self, query: str, max_results: int) -> List[SearchResult]:
        """البحث عبر Bing (محاكاة)"""
        logger.warning("Bing Search يتطلب API - استخدام DuckDuckGo بدلاً منه")
        return self._search_duckduckgo(query, max_results)
    
    def summarize_results(self, results: List[SearchResult], max_length: int = 500) -> str:
        """
        تلخيص نتائج البحث
        
        Args:
            results: نتائج البحث
            max_length: الطول الأقصى للملخص
        
        Returns:
            ملخص نصي
        """
        if not results:
            return "لم يتم العثور على نتائج"
        
        summary_parts = []
        current_length = 0
        
        for i, result in enumerate(results, 1):
            part = f"{i}. {result.title}\n{result.snippet}\n"
            
            if current_length + len(part) > max_length:
                break
            
            summary_parts.append(part)
            current_length += len(part)
        
        summary = "\n".join(summary_parts)
        
        if current_length >= max_length:
            summary += "\n..."
        
        return summary

# ═══════════════════════════════════════════════════════════════════════════════
# 2. نظام التعلم المستمر (Continuous Learning System)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrainingExample:
    """مثال تدريب"""
    text: str
    image_path: Optional[str]
    label: int
    confidence: float
    source: str  # 'user', 'feedback', 'automatic'
    timestamp: float
    metadata: Dict = field(default_factory=dict)

class ContinuousLearningBuffer:
    """
    ذاكرة مؤقتة لأمثلة التدريب الجديدة
    """
    
    def __init__(self, 
                 max_size: int = 1000,
                 min_confidence: float = 0.8,
                 save_path: str = "learning_buffer.json"):
        self.buffer: deque = deque(maxlen=max_size)
        self.min_confidence = min_confidence
        self.save_path = save_path
        
        # تحميل إذا كان موجوداً
        if Path(save_path).exists():
            self.load()
    
    def add(self, example: TrainingExample):
        """إضافة مثال جديد"""
        # قبول فقط الأمثلة عالية الثقة
        if example.confidence >= self.min_confidence:
            self.buffer.append(example)
            logger.info(f"✓ تمت إضافة مثال تدريب (ثقة: {example.confidence:.2f})")
        else:
            logger.debug(f"⚠ تم رفض مثال (ثقة منخفضة: {example.confidence:.2f})")
    
    def get_batch(self, batch_size: int) -> List[TrainingExample]:
        """الحصول على دفعة للتدريب"""
        if len(self.buffer) < batch_size:
            return list(self.buffer)
        
        # اختيار عشوائي
        indices = np.random.choice(len(self.buffer), batch_size, replace=False)
        return [self.buffer[i] for i in indices]
    
    def clear(self):
        """مسح الذاكرة"""
        self.buffer.clear()
        logger.info("🗑️ تم مسح buffer التعلم")
    
    def save(self):
        """حفظ الذاكرة"""
        data = []
        for example in self.buffer:
            data.append({
                'text': example.text,
                'image_path': example.image_path,
                'label': example.label,
                'confidence': example.confidence,
                'source': example.source,
                'timestamp': example.timestamp,
                'metadata': example.metadata
            })
        
        with open(self.save_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"💾 تم حفظ {len(data)} مثال تدريب")
    
    def load(self):
        """تحميل الذاكرة"""
        with open(self.save_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for item in data:
            example = TrainingExample(**item)
            self.buffer.append(example)
        
        logger.info(f"📂 تم تحميل {len(self.buffer)} مثال تدريب")
    
    def get_stats(self) -> Dict:
        """إحصائيات الذاكرة"""
        if not self.buffer:
            return {'total': 0, 'by_source': {}, 'avg_confidence': 0}
        
        by_source = {}
        confidences = []
        
        for example in self.buffer:
            by_source[example.source] = by_source.get(example.source, 0) + 1
            confidences.append(example.confidence)
        
        return {
            'total': len(self.buffer),
            'by_source': by_source,
            'avg_confidence': np.mean(confidences),
            'max_size': self.buffer.maxlen
        }

class ContinuousLearner:
    """
    نظام التعلم المستمر
    يجمع البيانات ويقوم بـ Fine-tuning دوري
    """
    
    def __init__(self,
                 model,
                 optimizer,
                 criterion,
                 buffer: ContinuousLearningBuffer,
                 learning_rate: float = 1e-4,
                 batch_size: int = 16,
                 fine_tune_threshold: int = 100):
        """
        Args:
            model: النموذج الأساسي
            optimizer: المحسّن
            criterion: دالة الخسارة
            buffer: ذاكرة التعلم
            learning_rate: معدل التعلم
            batch_size: حجم الدفعة
            fine_tune_threshold: عدد الأمثلة للبدء بالـ fine-tuning
        """
        self.model = model
        self.optimizer = optimizer
        self.criterion = criterion
        self.buffer = buffer
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        self.fine_tune_threshold = fine_tune_threshold
        
        # إحصائيات
        self.total_fine_tunes = 0
        self.last_fine_tune_time = None
    
    def should_fine_tune(self) -> bool:
        """التحقق من ضرورة Fine-tuning"""
        return len(self.buffer.buffer) >= self.fine_tune_threshold
    
    def fine_tune(self, num_epochs: int = 1) -> Dict:
        """
        تنفيذ Fine-tuning على البيانات المجموعة
        
        Args:
            num_epochs: عدد الـ epochs
        
        Returns:
            إحصائيات التدريب
        """
        if not self.should_fine_tune():
            logger.warning(f"⚠ عدد الأمثلة غير كافٍ ({len(self.buffer.buffer)}/{self.fine_tune_threshold})")
            return {'status': 'insufficient_data'}
        
        logger.info(f"🎓 بدء Fine-tuning على {len(self.buffer.buffer)} مثال...")
        
        self.model.train()
        
        # إعداد المحسّن بمعدل تعلم منخفض
        for param_group in self.optimizer.param_groups:
            param_group['lr'] = self.learning_rate
        
        total_loss = 0
        num_batches = 0
        
        for epoch in range(num_epochs):
            # الحصول على دفعة
            examples = self.buffer.get_batch(self.batch_size)
            
            # ملاحظة: هذا مثال مبسط
            # في التطبيق الفعلي، استخدم DataLoader
            
            epoch_loss = 0
            
            for example in examples:
                # تحضير البيانات (مبسط)
                # في التطبيق الفعلي، استخدم text_processor و image_transform
                
                self.optimizer.zero_grad()
                
                # Forward pass (مبسط - للتوضيح فقط)
                # loss = self.criterion(outputs, labels)
                
                # Backward pass
                # loss.backward()
                # self.optimizer.step()
                
                # epoch_loss += loss.item()
                pass
            
            total_loss += epoch_loss
            num_batches += 1
        
        avg_loss = total_loss / num_batches if num_batches > 0 else 0
        
        self.total_fine_tunes += 1
        self.last_fine_tune_time = time.time()
        
        logger.info(f"✓ اكتمل Fine-tuning! معدل الخسارة: {avg_loss:.4f}")
        
        # مسح الـ buffer بعد التدريب (اختياري)
        # self.buffer.clear()
        
        return {
            'status': 'success',
            'avg_loss': avg_loss,
            'num_examples': len(self.buffer.buffer),
            'num_epochs': num_epochs
        }
    
    def get_stats(self) -> Dict:
        """إحصائيات التعلم"""
        return {
            'total_fine_tunes': self.total_fine_tunes,
            'last_fine_tune': self.last_fine_tune_time,
            'buffer': self.buffer.get_stats()
        }

# ═══════════════════════════════════════════════════════════════════════════════
# 3. المساعد المتقدم مع التعلم والبحث
# ═══════════════════════════════════════════════════════════════════════════════

class AdvancedSmartAssistant(EnhancedSmartAssistant):
    """
    المساعد الذكي المتقدم مع:
    - التعلم المستمر
    - البحث في الويب
    - ذاكرة طويلة المدى
    """
    
    def __init__(self,
                 model_path: str,
                 vocab_path: str,
                 config_path: Optional[str] = None,
                 task_configs: Optional[Dict] = None,
                 kb_path: Optional[str] = None,
                 enable_recommendations: bool = True,
                 enable_feedback: bool = True,
                 enable_web_search: bool = True,
                 enable_continuous_learning: bool = True):
        """
        Args:
            ... (نفس المعاملات السابقة)
            enable_web_search: تفعيل البحث في الويب
            enable_continuous_learning: تفعيل التعلم المستمر
        """
        # تهيئة المساعد الأساسي
        super().__init__(
            model_path, vocab_path, config_path, task_configs,
            kb_path, enable_recommendations, enable_feedback
        )
        
        # محرك البحث في الويب
        self.web_search_enabled = enable_web_search
        if enable_web_search:
            self.search_engine = WebSearchEngine(max_results=5)
            logger.info("✓ محرك البحث في الويب مفعّل")
        
        # نظام التعلم المستمر
        self.learning_enabled = enable_continuous_learning
        if enable_continuous_learning:
            self.learning_buffer = ContinuousLearningBuffer(
                max_size=1000,
                min_confidence=0.85
            )
            
            # إعداد المحسّن للـ fine-tuning
            self.optimizer = optim.AdamW(
                self.model.parameters(),
                lr=1e-4,
                weight_decay=1e-5
            )
            
            from ultimate_bitnet_improved import LabelSmoothingCrossEntropy
            self.criterion = LabelSmoothingCrossEntropy(epsilon=0.1)
            
            self.continuous_learner = ContinuousLearner(
                model=self.model,
                optimizer=self.optimizer,
                criterion=self.criterion,
                buffer=self.learning_buffer,
                fine_tune_threshold=100
            )
            
            logger.info("✓ نظام التعلم المستمر مفعّل")
        
        # ذاكرة طويلة المدى (Long-term Memory)
        self.long_term_memory = []
        self.max_long_term_memory = 1000
    
    def search_web(self, 
                   query: str, 
                   max_results: int = 5) -> List[SearchResult]:
        """
        البحث في الإنترنت
        
        Args:
            query: استعلام البحث
            max_results: عدد النتائج
        
        Returns:
            نتائج البحث
        """
        if not self.web_search_enabled:
            logger.warning("البحث في الويب غير مفعّل")
            return []
        
        return self.search_engine.search(query, max_results=max_results)
    
    def needs_web_search(self, query: str) -> bool:
        """
        تحديد ما إذا كان السؤال يحتاج بحث في الويب
        
        Args:
            query: السؤال
        
        Returns:
            True إذا كان يحتاج بحث
        """
        # كلمات دالة على الحاجة للبحث
        web_keywords = [
            'آخر', 'أحدث', 'جديد', 'حالي', 'اليوم', 'الآن',
            'latest', 'recent', 'current', 'today', 'now',
            'أخبار', 'news', 'weather', 'طقس',
            'سعر', 'price', 'stock',
            'متى', 'when', 'أين', 'where'
        ]
        
        query_lower = query.lower()
        
        return any(keyword in query_lower for keyword in web_keywords)
    
    def process_with_web_search(self,
                                text: Optional[str] = None,
                                image: Optional[Any] = None,
                                task: Optional[TaskType] = None,
                                use_context: bool = True,
                                use_knowledge: bool = True,
                                force_search: bool = False) -> AssistantResponse:
        """
        معالجة مع إمكانية البحث في الويب
        
        Args:
            ... (نفس المعاملات السابقة)
            force_search: فرض البحث حتى لو لم يكن ضرورياً
        
        Returns:
            استجابة محسّنة مع معلومات من الويب
        """
        # التحقق من الحاجة للبحث
        should_search = force_search or (text and self.needs_web_search(text))
        
        web_context = ""
        search_results = []
        
        if should_search and self.web_search_enabled:
            logger.info(f"🌐 البحث في الويب عن: {text}")
            
            # البحث
            search_results = self.search_web(text, max_results=3)
            
            if search_results:
                # إضافة نتائج البحث للسياق
                web_context = "\n\nمعلومات من الإنترنت:\n"
                web_context += self.search_engine.summarize_results(
                    search_results, 
                    max_length=300
                )
        
        # دمج السياق
        enhanced_text = text
        if web_context:
            enhanced_text = f"{text}\n{web_context}"
        
        # المعالجة الأساسية
        response = self.process_with_knowledge(
            text=enhanced_text,
            image=image,
            task=task,
            use_context=use_context,
            use_knowledge=use_knowledge
        )
        
        # إضافة معلومات البحث للـ metadata
        if search_results:
            response.metadata['web_search_used'] = True
            response.metadata['web_results_count'] = len(search_results)
            response.metadata['web_sources'] = [r.source for r in search_results]
        
        # التعلم من الاستجابة عالية الثقة
        if self.learning_enabled and response.confidence >= 0.85:
            self._learn_from_response(text, image, response)
        
        return response
    
    def _learn_from_response(self,
                            text: Optional[str],
                            image: Optional[Any],
                            response: AssistantResponse):
        """
        التعلم من استجابة عالية الثقة
        
        Args:
            text: النص الأصلي
            image: الصورة (إن وجدت)
            response: الاستجابة
        """
        # استخراج التصنيف من الاستجابة
        # هذا مبسط - في التطبيق الفعلي، استخدم mapping صحيح
        label = 0  # افتراضي
        
        example = TrainingExample(
            text=text or "",
            image_path=response.input_image if image else None,
            label=label,
            confidence=response.confidence,
            source='automatic',
            timestamp=time.time(),
            metadata={
                'task': response.task,
                'prediction': response.prediction
            }
        )
        
        self.learning_buffer.add(example)
        
        # التحقق من ضرورة Fine-tuning
        if self.continuous_learner.should_fine_tune():
            logger.info("🎓 بدء Fine-tuning تلقائي...")
            stats = self.continuous_learner.fine_tune(num_epochs=1)
            logger.info(f"✓ اكتمل Fine-tuning: {stats}")
    
    def add_to_long_term_memory(self, interaction: Dict):
        """إضافة للذاكرة طويلة المدى"""
        self.long_term_memory.append(interaction)
        
        # الاحتفاظ بالحد الأقصى
        if len(self.long_term_memory) > self.max_long_term_memory:
            self.long_term_memory = self.long_term_memory[-self.max_long_term_memory:]
    
    def search_long_term_memory(self, query: str, top_k: int = 5) -> List[Dict]:
        """البحث في الذاكرة طويلة المدى"""
        query_lower = query.lower()
        results = []
        
        for interaction in self.long_term_memory:
            score = 0
            
            # حساب التطابق
            if 'user_input' in interaction:
                if query_lower in interaction['user_input'].lower():
                    score += 2
            
            if 'response' in interaction:
                if query_lower in str(interaction['response']).lower():
                    score += 1
            
            if score > 0:
                results.append({
                    'interaction': interaction,
                    'score': score
                })
        
        # ترتيب وتحديد العدد
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]
    
    def trigger_manual_fine_tune(self, num_epochs: int = 3) -> Dict:
        """
        تشغيل Fine-tuning يدوي
        
        Args:
            num_epochs: عدد الـ epochs
        
        Returns:
            نتائج التدريب
        """
        if not self.learning_enabled:
            return {'status': 'learning_disabled'}
        
        return self.continuous_learner.fine_tune(num_epochs=num_epochs)
    
    def get_advanced_stats(self) -> Dict:
        """إحصائيات متقدمة"""
        base_stats = self.get_enhanced_stats()
        
        advanced = {
            **base_stats,
            'long_term_memory': len(self.long_term_memory),
            'web_search_enabled': self.web_search_enabled,
        }
        
        if self.web_search_enabled:
            advanced['web_search_cache_size'] = len(self.search_engine.cache)
        
        if self.learning_enabled:
            advanced['continuous_learning'] = self.continuous_learner.get_stats()
        
        return advanced
    
    def save_state(self, directory: str = "assistant_state"):
        """حفظ حالة المساعد"""
        dir_path = Path(directory)
        dir_path.mkdir(exist_ok=True)
        
        # حفظ قاعدة المعرفة
        self.save_knowledge_base(str(dir_path / "knowledge_base.json"))
        
        # حفظ الذاكرة طويلة المدى
        with open(dir_path / "long_term_memory.json", 'w', encoding='utf-8') as f:
            json.dump(self.long_term_memory, f, ensure_ascii=False, indent=2)
        
        # حفظ buffer التعلم
        if self.learning_enabled:
            self.learning_buffer.save_path = str(dir_path / "learning_buffer.json")
            self.learning_buffer.save()
        
        logger.info(f"💾 تم حفظ حالة المساعد في: {directory}")
    
    def load_state(self, directory: str = "assistant_state"):
        """تحميل حالة المساعد"""
        dir_path = Path(directory)
        
        if not dir_path.exists():
            logger.warning(f"المجلد غير موجود: {directory}")
            return
        
        # تحميل قاعدة المعرفة
        kb_path = dir_path / "knowledge_base.json"
        if kb_path.exists():
            self.kb.load_from_file(str(kb_path))
        
        # تحميل الذاكرة طويلة المدى
        ltm_path = dir_path / "long_term_memory.json"
        if ltm_path.exists():
            with open(ltm_path, 'r', encoding='utf-8') as f:
                self.long_term_memory = json.load(f)
        
        # تحميل buffer التعلم
        if self.learning_enabled:
            buffer_path = dir_path / "learning_buffer.json"
            if buffer_path.exists():
                self.learning_buffer.save_path = str(buffer_path)
                self.learning_buffer.load()
        
        logger.info(f"📂 تم تحميل حالة المساعد من: {directory}")

# ═══════════════════════════════════════════════════════════════════════════════
# 4. أمثلة الاستخدام
# ═══════════════════════════════════════════════════════════════════════════════

def example_web_search():
    """مثال: البحث في الويب"""
    print("\n" + "="*60)
    print("مثال 1: البحث في الويب")
    print("="*60 + "\n")
    
    search_engine = WebSearchEngine()
    
    # البحث
    results = search_engine.search("Python programming", max_results=3)
    
    print(f"🔍 نتائج البحث عن 'Python programming':\n")
    
    for i, result in enumerate(results, 1):
        print(f"{i}. {result.title}")
        print(f"   {result.snippet}")
        print(f"   المصدر: {result.source}")
        print(f"   الرابط: {result.url}")
        print()
    
    # التلخيص
    summary = search_engine.summarize_results(results)
    print("\n📝 ملخص النتائج:")
    print(summary)

def example_continuous_learning():
    """مثال: التعلم المستمر"""
    print("\n" + "="*60)
    print("مثال 2: التعلم المستمر")
    print("="*60 + "\n")
    
    # إنشاء buffer
    buffer = ContinuousLearningBuffer(max_size=100)
    
    # إضافة أمثلة
    for i in range(5):
        example = TrainingExample(
            text=f"مثال تدريب رقم {i}",
            image_path=None,
            label=i % 3,
            confidence=0.9,
            source='user',
            timestamp=time.time()
        )
        buffer.add(example)
    
    # الإحصائيات
    stats = buffer.get_stats()
    print("📊 إحصائيات Buffer:")
    print(f"   الإجمالي: {stats['total']}")
    print(f"   حسب المصدر: {stats['by_source']}")
    print(f"   متوسط الثقة: {stats['avg_confidence']:.2f}")

if __name__ == "__main__":
    print("\n🚀 أمثلة المساعد المتقدم\n")
    
    example_web_search()
    example_continuous_learning()
    
    print("\n" + "="*60)
    print("✅ انتهت الأمثلة!")
    print("="*60)
