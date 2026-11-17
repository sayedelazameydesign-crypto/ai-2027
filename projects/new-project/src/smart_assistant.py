"""
🤖 مساعد ذكي متعدد الوسائط - Smart Multimodal Assistant
================================================================

مساعد ذكي قائم على Ultimate BitNet يمكنه:
- فهم النصوص والصور معاً
- الإجابة على الأسئلة
- تصنيف المحتوى
- تحليل المشاعر
- وصف الصور
- التوصيات الذكية
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Optional, Tuple, Union
from pathlib import Path
from PIL import Image
import json
import logging
from dataclasses import dataclass
from enum import Enum
import time

from ultimate_bitnet_improved import (
    UltimateBitNet,
    ModelConfig,
    get_transforms,
    load_checkpoint
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. تعريف قدرات المساعد (Assistant Capabilities)
# ═══════════════════════════════════════════════════════════════════════════════

class TaskType(Enum):
    """أنواع المهام التي يمكن للمساعد القيام بها"""
    SENTIMENT_ANALYSIS = "تحليل المشاعر"
    CONTENT_CLASSIFICATION = "تصنيف المحتوى"
    IMAGE_DESCRIPTION = "وصف الصور"
    QA = "الإجابة على الأسئلة"
    RECOMMENDATION = "التوصيات"
    MODERATION = "فحص المحتوى"
    CUSTOM = "مهمة مخصصة"

@dataclass
class AssistantResponse:
    """استجابة المساعد"""
    task: str
    input_text: Optional[str]
    input_image: Optional[str]
    prediction: str
    confidence: float
    probabilities: Dict[str, float]
    explanation: str
    processing_time: float
    metadata: Dict

# ═══════════════════════════════════════════════════════════════════════════════
# 2. نظام الذاكرة والسياق (Memory & Context System)
# ═══════════════════════════════════════════════════════════════════════════════

class ConversationMemory:
    """نظام ذاكرة للمحادثات مع المستخدم"""
    
    def __init__(self, max_history: int = 10):
        self.max_history = max_history
        self.history: List[Dict] = []
        self.user_preferences: Dict = {}
        self.conversation_context: Dict = {}
    
    def add_interaction(self, user_input: str, assistant_response: AssistantResponse):
        """إضافة تفاعل جديد للذاكرة"""
        interaction = {
            'timestamp': time.time(),
            'user_input': user_input,
            'response': {
                'prediction': assistant_response.prediction,
                'confidence': assistant_response.confidence,
                'task': assistant_response.task
            }
        }
        
        self.history.append(interaction)
        
        # الاحتفاظ بآخر N تفاعل فقط
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
    
    def get_context(self, n_previous: int = 3) -> str:
        """استرجاع السياق من المحادثات السابقة"""
        if not self.history:
            return ""
        
        recent = self.history[-n_previous:]
        context_parts = []
        
        for interaction in recent:
            context_parts.append(f"المستخدم: {interaction['user_input']}")
            context_parts.append(f"المساعد: {interaction['response']['prediction']}")
        
        return "\n".join(context_parts)
    
    def update_preferences(self, key: str, value):
        """تحديث تفضيلات المستخدم"""
        self.user_preferences[key] = value
    
    def get_preference(self, key: str, default=None):
        """الحصول على تفضيل المستخدم"""
        return self.user_preferences.get(key, default)
    
    def clear(self):
        """مسح الذاكرة"""
        self.history = []
        self.conversation_context = {}

# ═══════════════════════════════════════════════════════════════════════════════
# 3. محرك فهم النية (Intent Understanding Engine)
# ═══════════════════════════════════════════════════════════════════════════════

class IntentClassifier:
    """تصنيف نية المستخدم من النص"""
    
    def __init__(self):
        # كلمات مفتاحية لكل نوع مهمة
        self.intent_keywords = {
            TaskType.SENTIMENT_ANALYSIS: [
                'شعور', 'مشاعر', 'رأي', 'احساس', 'سعيد', 'حزين', 'غاضب',
                'sentiment', 'feeling', 'emotion', 'happy', 'sad'
            ],
            TaskType.CONTENT_CLASSIFICATION: [
                'صنف', 'نوع', 'فئة', 'category', 'classify', 'type', 'ما هو'
            ],
            TaskType.IMAGE_DESCRIPTION: [
                'صف', 'اشرح', 'ماذا في', 'describe', 'what is in', 'explain',
                'الصورة', 'image', 'picture'
            ],
            TaskType.QA: [
                'ما', 'هل', 'كيف', 'لماذا', 'متى', 'أين', 'من',
                'what', 'how', 'why', 'when', 'where', 'who'
            ],
            TaskType.RECOMMENDATION: [
                'اقترح', 'نصحني', 'recommend', 'suggest', 'توصية', 'اختر'
            ],
            TaskType.MODERATION: [
                'فاحص', 'مناسب', 'آمن', 'moderate', 'check', 'safe', 'appropriate'
            ]
        }
    
    def classify_intent(self, text: str, has_image: bool = False) -> TaskType:
        """تحديد نية المستخدم"""
        text_lower = text.lower()
        
        # إذا كان هناك صورة ونص يتعلق بها
        if has_image:
            for keyword in self.intent_keywords[TaskType.IMAGE_DESCRIPTION]:
                if keyword in text_lower:
                    return TaskType.IMAGE_DESCRIPTION
        
        # البحث عن الكلمات المفتاحية
        scores = {}
        for task_type, keywords in self.intent_keywords.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[task_type] = score
        
        # اختيار النية بأعلى درجة
        if scores:
            return max(scores.items(), key=lambda x: x[1])[0]
        
        # افتراضي: تصنيف محتوى أو Q&A
        return TaskType.CONTENT_CLASSIFICATION if has_image else TaskType.QA

# ═══════════════════════════════════════════════════════════════════════════════
# 4. معالج النصوص الذكي (Smart Text Processor)
# ═══════════════════════════════════════════════════════════════════════════════

class SmartTextProcessor:
    """معالج نصوص متقدم مع فهم السياق"""
    
    def __init__(self, vocab: Dict[str, int], max_seq_length: int = 128):
        self.vocab = vocab
        self.max_seq_length = max_seq_length
        
        # قاموس عكسي
        self.idx_to_word = {v: k for k, v in vocab.items()}
        
        # كلمات التوقف (يمكن إضافة المزيد)
        self.stopwords = {'في', 'من', 'إلى', 'على', 'the', 'a', 'an', 'in', 'on'}
    
    def preprocess(self, text: str, context: str = "") -> Tuple[torch.Tensor, torch.Tensor]:
        """
        معالجة متقدمة للنص مع السياق
        
        Returns:
            text_tensor: (1, max_seq_length)
            length: (1,)
        """
        # دمج السياق مع النص الحالي
        full_text = f"{context} {text}" if context else text
        
        # تنظيف وتقسيم
        words = self._tokenize(full_text)
        
        # تحويل لـ indices
        indices = []
        for word in words[:self.max_seq_length]:
            idx = self.vocab.get(word, self.vocab.get('<UNK>', 1))
            indices.append(idx)
        
        # Padding
        actual_length = len(indices)
        while len(indices) < self.max_seq_length:
            indices.append(0)
        
        text_tensor = torch.tensor([indices], dtype=torch.long)
        length = torch.tensor([actual_length], dtype=torch.long)
        
        return text_tensor, length
    
    def _tokenize(self, text: str) -> List[str]:
        """تقسيم النص إلى كلمات (tokenization بسيط)"""
        # إزالة علامات الترقيم وتحويل لأحرف صغيرة
        text = text.lower()
        for char in '.,;:!?()[]{}«»""\'':
            text = text.replace(char, ' ')
        
        # تقسيم
        words = text.split()
        
        return words
    
    def extract_keywords(self, text: str, top_k: int = 5) -> List[str]:
        """استخراج الكلمات المفتاحية"""
        words = self._tokenize(text)
        
        # إزالة كلمات التوقف
        keywords = [w for w in words if w not in self.stopwords and len(w) > 2]
        
        # حساب التكرار
        from collections import Counter
        word_counts = Counter(keywords)
        
        return [word for word, count in word_counts.most_common(top_k)]

# ═══════════════════════════════════════════════════════════════════════════════
# 5. المساعد الذكي الرئيسي (Main Intelligent Assistant)
# ═══════════════════════════════════════════════════════════════════════════════

class SmartAssistant:
    """
    🤖 المساعد الذكي الرئيسي
    
    قدرات:
    - فهم النصوص والصور معاً
    - ذاكرة للمحادثات
    - فهم النية والسياق
    - استجابات ذكية ومفصلة
    """
    
    def __init__(self, 
                 model_path: str,
                 vocab_path: str,
                 config_path: Optional[str] = None,
                 task_configs: Optional[Dict] = None):
        """
        Args:
            model_path: مسار النموذج المدرب
            vocab_path: مسار ملف القاموس
            config_path: مسار ملف الإعدادات
            task_configs: إعدادات مخصصة لكل مهمة
        """
        logger.info("🚀 تهيئة المساعد الذكي...")
        
        # تحميل الإعدادات
        self.config = self._load_config(config_path)
        
        # تحميل القاموس
        with open(vocab_path, 'r', encoding='utf-8') as f:
            self.vocab = json.load(f)
        logger.info(f"✓ تم تحميل القاموس ({len(self.vocab)} كلمة)")
        
        # تحديث حجم القاموس
        self.config.vocab_size = len(self.vocab)
        
        # تحميل النموذج
        self.model = UltimateBitNet(self.config).to(self.config.device)
        
        try:
            load_checkpoint(self.model, checkpoint_path=model_path, cfg=self.config)
            logger.info(f"✓ تم تحميل النموذج من {model_path}")
        except Exception as e:
            logger.warning(f"⚠ فشل تحميل النموذج: {e}")
            logger.warning("   سيتم استخدام أوزان عشوائية (للتجربة فقط)")
        
        self.model.eval()
        
        # المكونات الذكية
        self.memory = ConversationMemory(max_history=20)
        self.intent_classifier = IntentClassifier()
        self.text_processor = SmartTextProcessor(self.vocab, self.config.max_seq_length)
        self.image_transform = get_transforms(is_training=False)
        
        # إعدادات المهام
        self.task_configs = task_configs or self._default_task_configs()
        
        # الإحصائيات
        self.stats = {
            'total_queries': 0,
            'successful': 0,
            'failed': 0,
            'avg_confidence': 0.0
        }
        
        logger.info("✅ المساعد جاهز!")
    
    def _load_config(self, config_path: Optional[str]) -> ModelConfig:
        """تحميل الإعدادات"""
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                config_dict = json.load(f)
            config = ModelConfig()
            for key, value in config_dict.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            return config
        return ModelConfig()
    
    def _default_task_configs(self) -> Dict:
        """الإعدادات الافتراضية للمهام"""
        return {
            TaskType.SENTIMENT_ANALYSIS: {
                'labels': ['سلبي', 'محايد', 'إيجابي'],
                'response_template': 'التحليل العاطفي: {prediction} (ثقة: {confidence:.1%})'
            },
            TaskType.CONTENT_CLASSIFICATION: {
                'labels': ['تقني', 'رياضي', 'ترفيهي', 'أخبار', 'أخرى'],
                'response_template': 'التصنيف: {prediction} (ثقة: {confidence:.1%})'
            },
            TaskType.IMAGE_DESCRIPTION: {
                'labels': ['شخص', 'طبيعة', 'مدينة', 'طعام', 'حيوان', 'أخرى'],
                'response_template': 'الصورة تحتوي على: {prediction}'
            },
            TaskType.MODERATION: {
                'labels': ['آمن', 'يحتاج مراجعة', 'غير مناسب'],
                'response_template': 'تقييم المحتوى: {prediction}'
            }
        }
    
    def process(self,
                text: Optional[str] = None,
                image: Optional[Union[str, Image.Image]] = None,
                task: Optional[TaskType] = None,
                use_context: bool = True) -> AssistantResponse:
        """
        المعالجة الرئيسية للمدخلات
        
        Args:
            text: النص المدخل
            image: الصورة (مسار أو PIL.Image)
            task: نوع المهمة (اختياري - سيتم تحديده تلقائياً)
            use_context: استخدام السياق من المحادثات السابقة
        
        Returns:
            AssistantResponse
        """
        start_time = time.time()
        self.stats['total_queries'] += 1
        
        try:
            # 1. فهم النية
            if task is None:
                task = self.intent_classifier.classify_intent(
                    text or "", 
                    has_image=(image is not None)
                )
            
            logger.info(f"📝 المهمة المحددة: {task.value}")
            
            # 2. معالجة المدخلات
            text_tensor, text_length = self._prepare_text(text, use_context)
            image_tensor = self._prepare_image(image)
            
            # 3. الاستدلال
            with torch.no_grad():
                logits = self.model(text_tensor, image_tensor, text_length)
                probabilities = F.softmax(logits, dim=1)
                predicted_class = logits.argmax(dim=1).item()
                confidence = probabilities[0, predicted_class].item()
            
            # 4. تفسير النتائج
            response = self._interpret_results(
                task=task,
                predicted_class=predicted_class,
                probabilities=probabilities[0],
                confidence=confidence,
                text=text,
                image_path=image if isinstance(image, str) else None,
                processing_time=time.time() - start_time
            )
            
            # 5. حفظ في الذاكرة
            if text:
                self.memory.add_interaction(text, response)
            
            self.stats['successful'] += 1
            self.stats['avg_confidence'] = (
                (self.stats['avg_confidence'] * (self.stats['successful'] - 1) + confidence) 
                / self.stats['successful']
            )
            
            return response
            
        except Exception as e:
            logger.error(f"❌ خطأ في المعالجة: {e}")
            self.stats['failed'] += 1
            
            return AssistantResponse(
                task=task.value if task else "غير محدد",
                input_text=text,
                input_image=image if isinstance(image, str) else None,
                prediction="عذراً، حدث خطأ في المعالجة",
                confidence=0.0,
                probabilities={},
                explanation=f"خطأ: {str(e)}",
                processing_time=time.time() - start_time,
                metadata={'error': str(e)}
            )
    
    def _prepare_text(self, text: Optional[str], use_context: bool) -> Tuple[torch.Tensor, torch.Tensor]:
        """تحضير النص للنموذج"""
        if text is None:
            # نص افتراضي فارغ
            text = ""
        
        # إضافة السياق
        context = ""
        if use_context:
            context = self.memory.get_context(n_previous=2)
        
        text_tensor, length = self.text_processor.preprocess(text, context)
        return text_tensor.to(self.config.device), length.to(self.config.device)
    
    def _prepare_image(self, image: Optional[Union[str, Image.Image]]) -> torch.Tensor:
        """تحضير الصورة للنموذج"""
        if image is None:
            # صورة افتراضية (أسود)
            return torch.zeros(1, 3, 224, 224).to(self.config.device)
        
        # تحميل الصورة
        if isinstance(image, str):
            img = Image.open(image).convert('RGB')
        else:
            img = image.convert('RGB')
        
        # تطبيق التحويلات
        img_tensor = self.image_transform(img)
        return img_tensor.unsqueeze(0).to(self.config.device)
    
    def _interpret_results(self,
                          task: TaskType,
                          predicted_class: int,
                          probabilities: torch.Tensor,
                          confidence: float,
                          text: Optional[str],
                          image_path: Optional[str],
                          processing_time: float) -> AssistantResponse:
        """تفسير النتائج وإنشاء استجابة ذكية"""
        
        # الحصول على إعدادات المهمة
        task_config = self.task_configs.get(task, {
            'labels': [f'فئة {i}' for i in range(self.config.num_classes)],
            'response_template': '{prediction}'
        })
        
        labels = task_config['labels']
        
        # التنبؤ
        if predicted_class < len(labels):
            prediction = labels[predicted_class]
        else:
            prediction = f"فئة {predicted_class}"
        
        # الاحتمالات
        prob_dict = {
            labels[i] if i < len(labels) else f'فئة {i}': probabilities[i].item()
            for i in range(min(len(labels), probabilities.size(0)))
        }
        
        # توليد الشرح
        explanation = self._generate_explanation(
            task=task,
            prediction=prediction,
            confidence=confidence,
            text=text,
            probabilities=prob_dict
        )
        
        # التنسيق النهائي
        formatted_prediction = task_config['response_template'].format(
            prediction=prediction,
            confidence=confidence
        )
        
        return AssistantResponse(
            task=task.value,
            input_text=text,
            input_image=image_path,
            prediction=formatted_prediction,
            confidence=confidence,
            probabilities=prob_dict,
            explanation=explanation,
            processing_time=processing_time,
            metadata={
                'keywords': self.text_processor.extract_keywords(text) if text else [],
                'context_used': len(self.memory.history) > 0
            }
        )
    
    def _generate_explanation(self,
                            task: TaskType,
                            prediction: str,
                            confidence: float,
                            text: Optional[str],
                            probabilities: Dict[str, float]) -> str:
        """توليد شرح ذكي للنتيجة"""
        
        explanation_parts = []
        
        # تقييم الثقة
        if confidence >= 0.9:
            explanation_parts.append("أنا واثق جداً من هذا التحليل.")
        elif confidence >= 0.7:
            explanation_parts.append("أنا واثق من هذا التحليل بشكل معقول.")
        elif confidence >= 0.5:
            explanation_parts.append("هذا التحليل محتمل، لكن قد يحتاج تأكيد.")
        else:
            explanation_parts.append("النتيجة غير مؤكدة، قد تحتاج معلومات إضافية.")
        
        # الاحتمالات البديلة
        sorted_probs = sorted(probabilities.items(), key=lambda x: x[1], reverse=True)
        if len(sorted_probs) > 1:
            second_best = sorted_probs[1]
            if second_best[1] > 0.3:
                explanation_parts.append(
                    f"هناك احتمال {second_best[1]:.1%} أن يكون '{second_best[0]}' أيضاً."
                )
        
        # نصائح حسب المهمة
        if task == TaskType.SENTIMENT_ANALYSIS and text:
            keywords = self.text_processor.extract_keywords(text, top_k=3)
            if keywords:
                explanation_parts.append(
                    f"الكلمات المؤثرة: {', '.join(keywords[:3])}"
                )
        
        return " ".join(explanation_parts)
    
    def chat(self, message: str, image: Optional[str] = None) -> str:
        """
        واجهة محادثة بسيطة
        
        Args:
            message: رسالة المستخدم
            image: صورة اختيارية
        
        Returns:
            رد المساعد
        """
        response = self.process(text=message, image=image, use_context=True)
        
        # تنسيق الرد
        reply = f"{response.prediction}\n\n{response.explanation}"
        
        if response.confidence < 0.6:
            reply += "\n\n💡 نصيحة: يمكنك إعطائي المزيد من التفاصيل للحصول على نتيجة أفضل."
        
        return reply
    
    def get_stats(self) -> Dict:
        """الحصول على إحصائيات الأداء"""
        return {
            **self.stats,
            'success_rate': (
                self.stats['successful'] / self.stats['total_queries'] 
                if self.stats['total_queries'] > 0 else 0
            ),
            'memory_size': len(self.memory.history)
        }
    
    def reset_memory(self):
        """إعادة تعيين الذاكرة"""
        self.memory.clear()
        logger.info("🔄 تم مسح ذاكرة المحادثات")
    
    def set_preference(self, key: str, value):
        """تعيين تفضيل للمستخدم"""
        self.memory.update_preferences(key, value)
        logger.info(f"✓ تم تحديث التفضيل: {key} = {value}")
    
    def __repr__(self):
        return (
            f"SmartAssistant(\n"
            f"  Model: {self.config.hidden_dim}D BitNet\n"
            f"  Vocab: {len(self.vocab)} words\n"
            f"  Device: {self.config.device}\n"
            f"  Stats: {self.get_stats()}\n"
            f")"
        )

# ═══════════════════════════════════════════════════════════════════════════════
# 6. واجهة تفاعلية للمساعد (Interactive Interface)
# ═══════════════════════════════════════════════════════════════════════════════

class InteractiveChatbot:
    """واجهة تفاعلية للمحادثة مع المساعد"""
    
    def __init__(self, assistant: SmartAssistant):
        self.assistant = assistant
        self.running = True
    
    def print_welcome(self):
        """رسالة ترحيب"""
        print("\n" + "="*60)
        print("🤖 مرحباً! أنا مساعدك الذكي")
        print("="*60)
        print("\nيمكنني مساعدتك في:")
        print("  • تحليل المشاعر")
        print("  • تصنيف المحتوى")
        print("  • وصف الصور")
        print("  • الإجابة على الأسئلة")
        print("  • التوصيات الذكية")
        print("\nالأوامر المتاحة:")
        print("  /image <path>  - إضافة صورة")
        print("  /stats         - عرض الإحصائيات")
        print("  /reset         - مسح المحادثة")
        print("  /help          - عرض المساعدة")
        print("  /quit          - إنهاء البرنامج")
        print("="*60 + "\n")
    
    def run(self):
        """تشغيل الواجهة التفاعلية"""
        self.print_welcome()
        
        current_image = None
        
        while self.running:
            try:
                # الحصول على مدخل المستخدم
                user_input = input("👤 أنت: ").strip()
                
                if not user_input:
                    continue
                
                # معالجة الأوامر
                if user_input.startswith('/'):
                    self._handle_command(user_input)
                    continue
                
                # معالجة المحادثة
                print("\n🤖 المساعد: ", end="", flush=True)
                
                response = self.assistant.chat(user_input, image=current_image)
                print(response)
                print()
                
                # مسح الصورة بعد الاستخدام
                current_image = None
                
            except KeyboardInterrupt:
                print("\n\n👋 إلى اللقاء!")
                self.running = False
            except Exception as e:
                print(f"\n❌ خطأ: {e}\n")
    
    def _handle_command(self, command: str):
        """معالجة الأوامر الخاصة"""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        
        if cmd == '/quit' or cmd == '/exit':
            print("\n👋 شكراً لاستخدامك المساعد الذكي!")
            self.running = False
        
        elif cmd == '/stats':
            stats = self.assistant.get_stats()
            print("\n📊 الإحصائيات:")
            print(f"  إجمالي الاستفسارات: {stats['total_queries']}")
            print(f"  نسبة النجاح: {stats['success_rate']:.1%}")
            print(f"  متوسط الثقة: {stats['avg_confidence']:.1%}")
            print(f"  حجم الذاكرة: {stats['memory_size']}\n")
        
        elif cmd == '/reset':
            self.assistant.reset_memory()
            print("\n🔄 تم مسح المحادثات السابقة\n")
        
        elif cmd == '/image':
            if len(parts) < 2:
                print("\n⚠ الاستخدام: /image <path>\n")
            else:
                global current_image
                current_image = parts[1]
                print(f"\n✓ تم تحميل الصورة: {current_image}\n")
        
        elif cmd == '/help':
            self.print_welcome()
        
        else:
            print(f"\n⚠ أمر غير معروف: {cmd}")
            print("   استخدم /help لعرض الأوامر المتاحة\n")

# ═══════════════════════════════════════════════════════════════════════════════
# 7. أمثلة الاستخدام
# ═══════════════════════════════════════════════════════════════════════════════

def example_sentiment_analysis():
    """مثال: تحليل المشاعر"""
    print("\n" + "="*60)
    print("مثال 1: تحليل المشاعر")
    print("="*60 + "\n")
    
    # إنشاء قاموس بسيط للتجربة
    vocab = {
        '<PAD>': 0, '<UNK>': 1,
        'رائع': 2, 'جميل': 3, 'ممتاز': 4, 'سيء': 5, 'سعيد': 6,
        'حزين': 7, 'جيد': 8, 'سيئ': 9, 'محايد': 10
    }
    
    # حفظ القاموس
    with open('temp_vocab.json', 'w', encoding='utf-8') as f:
        json.dump(vocab, f, ensure_ascii=False)
    
    # إنشاء المساعد
    assistant = SmartAssistant(
        model_path="checkpoints/best_model.safetensors",
        vocab_path="temp_vocab.json",
        task_configs={
            TaskType.SENTIMENT_ANALYSIS: {
                'labels': ['سلبي', 'محايد', 'إيجابي'],
                'response_template': '😊 المشاعر: {prediction} (ثقة: {confidence:.0%})'
            }
        }
    )
    
    # أمثلة
    examples = [
        "هذا المنتج رائع وجميل جداً!",
        "لست متأكداً من رأيي",
        "تجربة سيئة للأسف"
    ]
    
    for text in examples:
        print(f"📝 النص: {text}")
        response = assistant.process(text=text, task=TaskType.SENTIMENT_ANALYSIS)
        print(f"🤖 النتيجة: {response.prediction}")
        print(f"💡 الشرح: {response.explanation}")
        print()

def example_interactive_mode():
    """مثال: الوضع التفاعلي"""
    print("\n" + "="*60)
    print("مثال 2: الوضع التفاعلي")
    print("="*60 + "\n")
    
    # تحضير القاموس
    vocab = {'<PAD>': 0, '<UNK>': 1}
    for i, word in enumerate(['مرحبا', 'كيف', 'حالك', 'جيد', 'شكرا'], start=2):
        vocab[word] = i
    
    with open('temp_vocab.json', 'w', encoding='utf-8') as f:
        json.dump(vocab, f, ensure_ascii=False)
    
    # إنشاء المساعد
    assistant = SmartAssistant(
        model_path="checkpoints/best_model.safetensors",
        vocab_path="temp_vocab.json"
    )
    
    # تشغيل الوضع التفاعلي
    chatbot = InteractiveChatbot(assistant)
    chatbot.run()

# ═══════════════════════════════════════════════════════════════════════════════
# 8. Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='🤖 المساعد الذكي')
    parser.add_argument('--mode', choices=['interactive', 'demo'], default='demo',
                       help='وضع التشغيل')
    parser.add_argument('--model', type=str, default='checkpoints/best_model.safetensors',
                       help='مسار النموذج')
    parser.add_argument('--vocab', type=str, default='vocab.json',
                       help='مسار القاموس')
    
    args = parser.parse_args()
    
    if args.mode == 'interactive':
        print("\n🚀 تشغيل الوضع التفاعلي...\n")
        example_interactive_mode()
    else:
        print("\n🎬 تشغيل العروض التوضيحية...\n")
        example_sentiment_analysis()
        
        print("\n" + "="*60)
        print("💡 للتشغيل التفاعلي، استخدم: --mode interactive")
        print("="*60)
