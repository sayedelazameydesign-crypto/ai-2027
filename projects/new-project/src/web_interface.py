"""
🌐 واجهة ويب للمساعد الذكي
================================

واجهة تفاعلية جميلة باستخدام Gradio
"""

import gradio as gr
import torch
from PIL import Image
import json
from pathlib import Path
import logging
from typing import Optional, Tuple, List
import time

from smart_assistant import SmartAssistant, TaskType, AssistantResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. المساعد العام (Global Assistant Instance)
# ═══════════════════════════════════════════════════════════════════════════════

class WebAssistant:
    """غلاف للمساعد الذكي للاستخدام في الويب"""
    
    def __init__(self):
        self.assistant: Optional[SmartAssistant] = None
        self.is_initialized = False
    
    def initialize(self, model_path: str, vocab_path: str) -> str:
        """تهيئة المساعد"""
        try:
            if not Path(vocab_path).exists():
                return f"❌ ملف القاموس غير موجود: {vocab_path}"
            
            logger.info("🚀 تهيئة المساعد الذكي...")
            
            self.assistant = SmartAssistant(
                model_path=model_path,
                vocab_path=vocab_path,
                task_configs={
                    TaskType.SENTIMENT_ANALYSIS: {
                        'labels': ['سلبي 😢', 'محايد 😐', 'إيجابي 😊'],
                        'response_template': '{prediction}'
                    },
                    TaskType.CONTENT_CLASSIFICATION: {
                        'labels': ['تقني 💻', 'رياضي ⚽', 'ترفيهي 🎬', 'أخبار 📰', 'أخرى 📚'],
                        'response_template': '{prediction}'
                    },
                    TaskType.IMAGE_DESCRIPTION: {
                        'labels': ['شخص 👤', 'طبيعة 🌳', 'مدينة 🏙️', 'طعام 🍕', 'حيوان 🐾', 'أخرى 📷'],
                        'response_template': '{prediction}'
                    },
                    TaskType.MODERATION: {
                        'labels': ['آمن ✅', 'يحتاج مراجعة ⚠️', 'غير مناسب ❌'],
                        'response_template': '{prediction}'
                    }
                }
            )
            
            self.is_initialized = True
            logger.info("✅ المساعد جاهز!")
            return "✅ تم تهيئة المساعد بنجاح!"
            
        except Exception as e:
            logger.error(f"❌ خطأ في التهيئة: {e}")
            return f"❌ خطأ: {str(e)}"
    
    def process_request(self,
                       text: str,
                       image: Optional[Image.Image],
                       task: str,
                       use_context: bool) -> Tuple[str, str, str, str]:
        """
        معالجة الطلب
        
        Returns:
            (prediction, confidence, explanation, probabilities_chart)
        """
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ", "0%", "يرجى تهيئة المساعد أولاً", ""
        
        try:
            # تحويل نوع المهمة
            task_map = {
                'تحليل المشاعر': TaskType.SENTIMENT_ANALYSIS,
                'تصنيف المحتوى': TaskType.CONTENT_CLASSIFICATION,
                'وصف الصور': TaskType.IMAGE_DESCRIPTION,
                'فحص المحتوى': TaskType.MODERATION,
                'تلقائي (ذكي)': None
            }
            
            task_type = task_map.get(task, None)
            
            # المعالجة
            response = self.assistant.process(
                text=text if text else None,
                image=image,
                task=task_type,
                use_context=use_context
            )
            
            # تنسيق الاحتمالات
            prob_text = self._format_probabilities(response.probabilities)
            
            # تنسيق النتائج
            prediction = f"## {response.prediction}\n\n"
            confidence = f"{response.confidence:.1%}"
            explanation = (
                f"### التفسير\n{response.explanation}\n\n"
                f"### معلومات إضافية\n"
                f"- **المهمة**: {response.task}\n"
                f"- **وقت المعالجة**: {response.processing_time:.2f} ثانية\n"
                f"- **استخدام السياق**: {'نعم' if response.metadata.get('context_used') else 'لا'}\n"
            )
            
            if response.metadata.get('keywords'):
                keywords = ', '.join(response.metadata['keywords'])
                explanation += f"- **الكلمات المفتاحية**: {keywords}\n"
            
            return prediction, confidence, explanation, prob_text
            
        except Exception as e:
            logger.error(f"❌ خطأ في المعالجة: {e}")
            return f"❌ خطأ: {str(e)}", "0%", "", ""
    
    def _format_probabilities(self, probs: dict) -> str:
        """تنسيق الاحتمالات كنص"""
        if not probs:
            return "لا توجد احتمالات"
        
        sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
        
        result = "### الاحتمالات\n\n"
        for label, prob in sorted_probs:
            bar_length = int(prob * 20)
            bar = "█" * bar_length + "░" * (20 - bar_length)
            result += f"**{label}**: {bar} {prob:.1%}\n\n"
        
        return result
    
    def chat(self, message: str, image: Optional[Image.Image], history: List) -> Tuple[List, str]:
        """
        دالة المحادثة
        
        Returns:
            (updated_history, status)
        """
        if not self.is_initialized:
            history.append((message, "❌ المساعد غير مهيأ. يرجى تهيئته من تبويب الإعدادات."))
            return history, ""
        
        try:
            # إضافة رسالة المستخدم
            user_msg = message
            if image is not None:
                user_msg += " 🖼️"
            
            # الحصول على الرد
            response = self.assistant.chat(message, image)
            
            # إضافة للسجل
            history.append((user_msg, response))
            
            return history, ""
            
        except Exception as e:
            history.append((message, f"❌ خطأ: {str(e)}"))
            return history, ""
    
    def get_stats(self) -> str:
        """الحصول على الإحصائيات"""
        if not self.is_initialized:
            return "المساعد غير مهيأ"
        
        stats = self.assistant.get_stats()
        
        return f"""
## 📊 إحصائيات الأداء

- **إجمالي الاستفسارات**: {stats['total_queries']}
- **نسبة النجاح**: {stats['success_rate']:.1%}
- **متوسط الثقة**: {stats['avg_confidence']:.1%}
- **حجم الذاكرة**: {stats['memory_size']} تفاعل

### نصائح
{'✅ الأداء ممتاز!' if stats['avg_confidence'] > 0.8 else '⚠️ قد تحتاج لتحسين البيانات أو النموذج'}
"""
    
    def reset_memory(self) -> str:
        """مسح الذاكرة"""
        if not self.is_initialized:
            return "المساعد غير مهيأ"
        
        self.assistant.reset_memory()
        return "✅ تم مسح ذاكرة المحادثات"

# المساعد العام
web_assistant = WebAssistant()

# ═══════════════════════════════════════════════════════════════════════════════
# 2. بناء الواجهة (Build Interface)
# ═══════════════════════════════════════════════════════════════════════════════

def create_interface():
    """إنشاء واجهة Gradio"""
    
    # الثيم المخصص
    theme = gr.themes.Soft(
        primary_hue="blue",
        secondary_hue="cyan",
    )
    
    with gr.Blocks(theme=theme, title="🤖 المساعد الذكي", css="""
        .gradio-container {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        .header {
            text-align: center;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .stat-box {
            background: #f0f9ff;
            padding: 15px;
            border-radius: 8px;
            border-left: 4px solid #3b82f6;
        }
    """) as demo:
        
        # العنوان
        gr.HTML("""
            <div class="header">
                <h1>🤖 المساعد الذكي متعدد الوسائط</h1>
                <p>مساعد ذكي قائم على BitNet يفهم النصوص والصور معاً</p>
            </div>
        """)
        
        with gr.Tabs():
            # ═══════════════════════════════════════════════════════════════
            # تبويب 1: المحادثة
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("💬 المحادثة"):
                gr.Markdown("""
                ### 👋 مرحباً! كيف يمكنني مساعدتك؟
                
                يمكنني:
                - 😊 تحليل المشاعر من النصوص
                - 📂 تصنيف المحتوى
                - 🖼️ وصف الصور
                - ✅ فحص المحتوى
                - 💡 الإجابة على الأسئلة
                """)
                
                chatbot = gr.Chatbot(
                    label="المحادثة",
                    height=400,
                    show_label=True,
                    avatar_images=(None, "🤖")
                )
                
                with gr.Row():
                    with gr.Column(scale=4):
                        msg = gr.Textbox(
                            label="رسالتك",
                            placeholder="اكتب رسالتك هنا...",
                            lines=2
                        )
                    with gr.Column(scale=1):
                        chat_image = gr.Image(
                            label="صورة (اختياري)",
                            type="pil",
                            height=100
                        )
                
                with gr.Row():
                    send_btn = gr.Button("📤 إرسال", variant="primary")
                    clear_btn = gr.Button("🗑️ مسح المحادثة")
                
                chat_status = gr.Textbox(label="الحالة", visible=False)
                
                # الأحداث
                send_btn.click(
                    web_assistant.chat,
                    inputs=[msg, chat_image, chatbot],
                    outputs=[chatbot, chat_status]
                ).then(
                    lambda: ("", None),
                    outputs=[msg, chat_image]
                )
                
                msg.submit(
                    web_assistant.chat,
                    inputs=[msg, chat_image, chatbot],
                    outputs=[chatbot, chat_status]
                ).then(
                    lambda: ("", None),
                    outputs=[msg, chat_image]
                )
                
                clear_btn.click(lambda: [], outputs=chatbot)
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 2: التحليل المتقدم
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("🔬 التحليل المتقدم"):
                gr.Markdown("""
                ### 🔍 التحليل التفصيلي
                احصل على تحليل مفصل مع الاحتمالات والتفسيرات
                """)
                
                with gr.Row():
                    with gr.Column():
                        analysis_text = gr.Textbox(
                            label="النص",
                            placeholder="أدخل النص للتحليل...",
                            lines=5
                        )
                        analysis_image = gr.Image(
                            label="الصورة (اختياري)",
                            type="pil"
                        )
                        
                        task_choice = gr.Radio(
                            label="نوع المهمة",
                            choices=[
                                'تلقائي (ذكي)',
                                'تحليل المشاعر',
                                'تصنيف المحتوى',
                                'وصف الصور',
                                'فحص المحتوى'
                            ],
                            value='تلقائي (ذكي)'
                        )
                        
                        use_context_check = gr.Checkbox(
                            label="استخدام سياق المحادثات السابقة",
                            value=True
                        )
                        
                        analyze_btn = gr.Button("🔍 تحليل", variant="primary", size="lg")
                    
                    with gr.Column():
                        prediction_out = gr.Markdown(label="النتيجة")
                        confidence_out = gr.Textbox(label="مستوى الثقة")
                        explanation_out = gr.Markdown(label="التفسير")
                        probabilities_out = gr.Markdown(label="الاحتمالات")
                
                analyze_btn.click(
                    web_assistant.process_request,
                    inputs=[analysis_text, analysis_image, task_choice, use_context_check],
                    outputs=[prediction_out, confidence_out, explanation_out, probabilities_out]
                )
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 3: الإحصائيات
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("📊 الإحصائيات"):
                gr.Markdown("### 📈 أداء المساعد")
                
                stats_display = gr.Markdown()
                
                with gr.Row():
                    refresh_stats_btn = gr.Button("🔄 تحديث الإحصائيات", variant="secondary")
                    reset_memory_btn = gr.Button("🗑️ مسح الذاكرة", variant="stop")
                
                reset_status = gr.Textbox(label="الحالة", visible=False)
                
                refresh_stats_btn.click(
                    web_assistant.get_stats,
                    outputs=stats_display
                )
                
                reset_memory_btn.click(
                    web_assistant.reset_memory,
                    outputs=reset_status
                ).then(
                    lambda x: gr.Info(x),
                    inputs=reset_status
                )
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 4: الإعدادات
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("⚙️ الإعدادات"):
                gr.Markdown("""
                ### ⚙️ إعدادات المساعد
                قم بتهيئة المساعد قبل الاستخدام
                """)
                
                with gr.Row():
                    model_path = gr.Textbox(
                        label="مسار النموذج",
                        value="checkpoints/best_model.safetensors",
                        placeholder="checkpoints/best_model.safetensors"
                    )
                    vocab_path = gr.Textbox(
                        label="مسار القاموس",
                        value="vocab.json",
                        placeholder="vocab.json"
                    )
                
                init_btn = gr.Button("🚀 تهيئة المساعد", variant="primary", size="lg")
                init_status = gr.Textbox(label="حالة التهيئة", interactive=False)
                
                init_btn.click(
                    web_assistant.initialize,
                    inputs=[model_path, vocab_path],
                    outputs=init_status
                )
                
                gr.Markdown("""
                ---
                ### 📚 معلومات
                
                **الإصدار**: 1.0.0  
                **النموذج**: Ultimate BitNet  
                **المطور**: AI Assistant Team
                
                #### المتطلبات:
                - نموذج مدرب (.safetensors أو .pth)
                - ملف قاموس (vocab.json)
                - PyTorch 2.0+
                
                #### الاستخدام:
                1. تأكد من وجود ملفات النموذج والقاموس
                2. اضغط على "تهيئة المساعد"
                3. انتقل لتبويب المحادثة أو التحليل
                4. استمتع! 🎉
                """)
        
        # تحميل الإحصائيات عند فتح الصفحة
        demo.load(
            web_assistant.get_stats,
            outputs=stats_display
        )
    
    return demo

# ═══════════════════════════════════════════════════════════════════════════════
# 3. التشغيل
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='🌐 واجهة ويب للمساعد الذكي')
    parser.add_argument('--port', type=int, default=7860, help='منفذ الخادم')
    parser.add_argument('--share', action='store_true', help='إنشاء رابط عام')
    parser.add_argument('--model', type=str, help='مسار النموذج (للتهيئة التلقائية)')
    parser.add_argument('--vocab', type=str, help='مسار القاموس (للتهيئة التلقائية)')
    
    args = parser.parse_args()
    
    # التهيئة التلقائية إذا تم توفير المسارات
    if args.model and args.vocab:
        logger.info("🔧 تهيئة تلقائية...")
        result = web_assistant.initialize(args.model, args.vocab)
        logger.info(result)
    
    # إنشاء وتشغيل الواجهة
    demo = create_interface()
    
    print("\n" + "="*60)
    print("🌐 تشغيل واجهة الويب...")
    print(f"📍 العنوان المحلي: http://localhost:{args.port}")
    if args.share:
        print("🌍 سيتم إنشاء رابط عام للمشاركة")
    print("="*60 + "\n")
    
    demo.launch(
        server_port=args.port,
        share=args.share,
        show_error=True,
        inbrowser=True  # فتح المتصفح تلقائياً
    )
