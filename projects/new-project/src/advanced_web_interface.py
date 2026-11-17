"""
🌐 واجهة ويب متقدمة - Advanced Web Interface  
====================================================

مع دعم:
- البحث في الويب
- التعلم المستمر
- ذاكرة طويلة المدى
"""

import gradio as gr
from typing import Optional, Tuple, List
import logging
from pathlib import Path

from advanced_assistant import (
    AdvancedSmartAssistant,
    WebSearchEngine,
    SearchResult,
    TaskType
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# المساعد المتقدم العام
# ═══════════════════════════════════════════════════════════════════════════════

class AdvancedWebAssistant:
    """غلاف للمساعد المتقدم"""
    
    def __init__(self):
        self.assistant: Optional[AdvancedSmartAssistant] = None
        self.is_initialized = False
    
    def initialize(self,
                  model_path: str,
                  vocab_path: str,
                  kb_path: Optional[str] = None,
                  enable_web_search: bool = True,
                  enable_learning: bool = True) -> str:
        """تهيئة المساعد المتقدم"""
        try:
            logger.info("🚀 تهيئة المساعد المتقدم...")
            
            self.assistant = AdvancedSmartAssistant(
                model_path=model_path,
                vocab_path=vocab_path,
                kb_path=kb_path,
                enable_recommendations=True,
                enable_feedback=True,
                enable_web_search=enable_web_search,
                enable_continuous_learning=enable_learning
            )
            
            self.is_initialized = True
            
            stats = self.assistant.get_advanced_stats()
            
            return (
                f"✅ تم تهيئة المساعد المتقدم!\n\n"
                f"🌟 المميزات المفعّلة:\n"
                f"- 🌐 البحث في الويب: {'نعم' if enable_web_search else 'لا'}\n"
                f"- 🎓 التعلم المستمر: {'نعم' if enable_learning else 'لا'}\n"
                f"- 📚 قاعدة المعرفة: {stats['knowledge_base']['total_items']} عنصر\n"
                f"- 💾 الذاكرة طويلة المدى: {stats.get('long_term_memory', 0)} تفاعل\n"
            )
            
        except Exception as e:
            logger.error(f"❌ خطأ: {e}")
            return f"❌ خطأ في التهيئة: {str(e)}"
    
    def chat_advanced(self,
                     message: str,
                     image,
                     use_web_search: bool,
                     use_knowledge: bool,
                     history: List) -> Tuple[List, str]:
        """محادثة متقدمة مع بحث ويب"""
        if not self.is_initialized:
            history.append((message, "❌ المساعد غير مهيأ"))
            return history, ""
        
        try:
            user_msg = message
            if image:
                user_msg += " 🖼️"
            
            # المعالجة المتقدمة
            response = self.assistant.process_with_web_search(
                text=message,
                image=image,
                use_knowledge=use_knowledge,
                force_search=use_web_search
            )
            
            # تنسيق الرد
            reply = f"{response.prediction}\n\n{response.explanation}"
            
            # معلومات إضافية
            if response.metadata.get('web_search_used'):
                reply += f"\n\n🌐 تم البحث في الإنترنت - {response.metadata['web_results_count']} نتيجة"
            
            if response.metadata.get('knowledge_used'):
                reply += f"\n💡 تم استخدام قاعدة المعرفة"
            
            # حفظ في الذاكرة طويلة المدى
            self.assistant.add_to_long_term_memory({
                'user_input': message,
                'response': response.prediction,
                'timestamp': response.processing_time,
                'metadata': response.metadata
            })
            
            history.append((user_msg, reply))
            return history, ""
            
        except Exception as e:
            logger.error(f"خطأ: {e}")
            history.append((message, f"❌ خطأ: {str(e)}"))
            return history, ""
    
    def web_search_only(self, query: str, max_results: int) -> str:
        """البحث في الويب فقط"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        try:
            results = self.assistant.search_web(query, max_results=max_results)
            
            if not results:
                return f"لم يتم العثور على نتائج لـ '{query}'"
            
            output = f"## 🔍 نتائج البحث عن: '{query}'\n\n"
            
            for i, result in enumerate(results, 1):
                output += f"### {i}. {result.title}\n\n"
                output += f"{result.snippet}\n\n"
                output += f"**المصدر**: {result.source}\n"
                output += f"**الرابط**: [{result.url}]({result.url})\n\n"
                output += "---\n\n"
            
            return output
            
        except Exception as e:
            return f"❌ خطأ في البحث: {str(e)}"
    
    def trigger_fine_tune(self, num_epochs: int) -> str:
        """تشغيل Fine-tuning يدوي"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        try:
            result = self.assistant.trigger_manual_fine_tune(num_epochs=num_epochs)
            
            if result['status'] == 'success':
                return (
                    f"✅ اكتمل Fine-tuning!\n\n"
                    f"- عدد الأمثلة: {result['num_examples']}\n"
                    f"- عدد الـ Epochs: {result['num_epochs']}\n"
                    f"- معدل الخسارة: {result['avg_loss']:.4f}\n"
                )
            elif result['status'] == 'insufficient_data':
                buffer_stats = self.assistant.learning_buffer.get_stats()
                return (
                    f"⚠️ البيانات غير كافية للتدريب\n\n"
                    f"- الحالي: {buffer_stats['total']}\n"
                    f"- المطلوب: {self.assistant.continuous_learner.fine_tune_threshold}\n"
                )
            else:
                return f"⚠️ حالة غير معروفة: {result['status']}"
                
        except Exception as e:
            return f"❌ خطأ: {str(e)}"
    
    def search_memory(self, query: str, top_k: int) -> str:
        """البحث في الذاكرة طويلة المدى"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        try:
            results = self.assistant.search_long_term_memory(query, top_k=top_k)
            
            if not results:
                return f"لم يتم العثور على نتائج في الذاكرة لـ '{query}'"
            
            output = f"## 🧠 نتائج من الذاكرة طويلة المدى: '{query}'\n\n"
            
            for i, result in enumerate(results, 1):
                interaction = result['interaction']
                output += f"### {i}. نقاط التطابق: {result['score']}\n\n"
                output += f"**السؤال**: {interaction.get('user_input', 'N/A')}\n\n"
                output += f"**الإجابة**: {interaction.get('response', 'N/A')}\n\n"
                output += "---\n\n"
            
            return output
            
        except Exception as e:
            return f"❌ خطأ: {str(e)}"
    
    def get_advanced_stats_ui(self) -> str:
        """إحصائيات متقدمة"""
        if not self.is_initialized:
            return "المساعد غير مهيأ"
        
        try:
            stats = self.assistant.get_advanced_stats()
            
            output = "## 📊 إحصائيات المساعد المتقدم\n\n"
            
            # الأساسيات
            output += "### المساعد الأساسي\n"
            output += f"- الاستفسارات: {stats['total_queries']}\n"
            output += f"- نسبة النجاح: {stats['success_rate']:.1%}\n"
            output += f"- متوسط الثقة: {stats['avg_confidence']:.1%}\n\n"
            
            # قاعدة المعرفة
            output += "### قاعدة المعرفة\n"
            output += f"- الفئات: {stats['knowledge_base']['total_categories']}\n"
            output += f"- العناصر: {stats['knowledge_base']['total_items']}\n\n"
            
            # البحث في الويب
            output += "### البحث في الويب\n"
            output += f"- مفعّل: {'نعم' if stats['web_search_enabled'] else 'لا'}\n"
            if stats['web_search_enabled']:
                output += f"- Cache: {stats.get('web_search_cache_size', 0)} استعلام\n"
            output += "\n"
            
            # التعلم المستمر
            if 'continuous_learning' in stats:
                cl = stats['continuous_learning']
                output += "### التعلم المستمر\n"
                output += f"- عدد مرات Fine-tuning: {cl['total_fine_tunes']}\n"
                output += f"- Buffer:\n"
                output += f"  - الإجمالي: {cl['buffer']['total']}\n"
                output += f"  - متوسط الثقة: {cl['buffer']['avg_confidence']:.2f}\n"
                output += f"  - حسب المصدر: {cl['buffer']['by_source']}\n\n"
            
            # الذاكرة طويلة المدى
            output += "### الذاكرة طويلة المدى\n"
            output += f"- التفاعلات المحفوظة: {stats.get('long_term_memory', 0)}\n\n"
            
            return output
            
        except Exception as e:
            return f"❌ خطأ: {str(e)}"
    
    def save_state_ui(self) -> str:
        """حفظ حالة المساعد"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        try:
            self.assistant.save_state("assistant_state")
            return "✅ تم حفظ حالة المساعد بنجاح!"
        except Exception as e:
            return f"❌ خطأ: {str(e)}"

# المساعد المتقدم العام
advanced_assistant = AdvancedWebAssistant()

# ═══════════════════════════════════════════════════════════════════════════════
# بناء الواجهة المتقدمة
# ═══════════════════════════════════════════════════════════════════════════════

def create_advanced_interface():
    """إنشاء واجهة Gradio متقدمة"""
    
    theme = gr.themes.Soft(primary_hue="indigo", secondary_hue="purple")
    
    with gr.Blocks(theme=theme, title="🧠 المساعد الذكي المتقدم", css="""
        .header {
            text-align: center;
            padding: 25px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 12px;
            margin-bottom: 25px;
        }
        .feature-badge {
            background: #10b981;
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.85em;
            margin: 0 4px;
        }
    """) as demo:
        
        gr.HTML("""
            <div class="header">
                <h1>🧠 المساعد الذكي المتقدم</h1>
                <p>مع <span class="feature-badge">🌐 بحث ويب</span> 
                   <span class="feature-badge">🎓 تعلم مستمر</span> 
                   <span class="feature-badge">🧠 ذاكرة طويلة</span></p>
            </div>
        """)
        
        with gr.Tabs():
            # ═══════════════════════════════════════════════════════════════
            # تبويب 1: المحادثة المتقدمة
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("💬 المحادثة المتقدمة"):
                gr.Markdown("""
                ### 💬 محادثة ذكية مع بحث ويب وتعلم مستمر
                """)
                
                with gr.Row():
                    use_web_check = gr.Checkbox(
                        label="🌐 البحث في الويب",
                        value=False,
                        info="تفعيل للبحث عن معلومات حديثة"
                    )
                    use_kb_check = gr.Checkbox(
                        label="📚 قاعدة المعرفة",
                        value=True
                    )
                
                chatbot = gr.Chatbot(label="المحادثة", height=450)
                
                with gr.Row():
                    with gr.Column(scale=4):
                        msg = gr.Textbox(
                            label="رسالتك",
                            placeholder="اسأل أي شيء... سأبحث في الويب إذا لزم الأمر!",
                            lines=2
                        )
                    with gr.Column(scale=1):
                        chat_image = gr.Image(label="صورة", type="pil", height=100)
                
                with gr.Row():
                    send_btn = gr.Button("📤 إرسال", variant="primary")
                    clear_btn = gr.Button("🗑️ مسح")
                
                gr.Markdown("""
                💡 **نصيحة**: المساعد سيبحث تلقائياً في الويب عند سؤاله عن:
                - أخبار حديثة
                - الطقس
                - أسعار
                - أحداث جارية
                """)
                
                chat_status = gr.Textbox(visible=False)
                
                send_btn.click(
                    advanced_assistant.chat_advanced,
                    inputs=[msg, chat_image, use_web_check, use_kb_check, chatbot],
                    outputs=[chatbot, chat_status]
                ).then(lambda: ("", None), outputs=[msg, chat_image])
                
                clear_btn.click(lambda: [], outputs=chatbot)
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 2: البحث في الويب
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("🌐 البحث في الويب"):
                gr.Markdown("""
                ### 🌐 بحث مباشر في الإنترنت
                ابحث عن أي شيء واحصل على نتائج فورية
                """)
                
                search_query = gr.Textbox(
                    label="استعلام البحث",
                    placeholder="مثال: آخر أخبار الذكاء الاصطناعي",
                    lines=2
                )
                
                max_results_slider = gr.Slider(
                    label="عدد النتائج",
                    minimum=1,
                    maximum=10,
                    value=5,
                    step=1
                )
                
                search_btn = gr.Button("🔍 بحث", variant="primary", size="lg")
                
                search_results = gr.Markdown(label="النتائج")
                
                search_btn.click(
                    advanced_assistant.web_search_only,
                    inputs=[search_query, max_results_slider],
                    outputs=search_results
                )
                
                gr.Markdown("""
                ---
                **المحركات المدعومة**:
                - 🦆 DuckDuckGo (افتراضي)
                - 🔵 Bing (قريباً)
                - 🔴 Google (قريباً)
                """)
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 3: التعلم المستمر
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("🎓 التعلم المستمر"):
                gr.Markdown("""
                ### 🎓 نظام التعلم الذاتي
                المساعد يتعلم تلقائياً من تفاعلاتك!
                """)
                
                gr.Markdown("""
                #### كيف يعمل؟
                1. المساعد يجمع الاستجابات عالية الثقة (>85%)
                2. عند جمع 100 مثال، يبدأ Fine-tuning تلقائياً
                3. يتحسن الأداء تدريجياً مع الوقت
                """)
                
                gr.Markdown("#### تشغيل يدوي")
                
                epochs_slider = gr.Slider(
                    label="عدد الـ Epochs",
                    minimum=1,
                    maximum=10,
                    value=3,
                    step=1,
                    info="عدد دورات التدريب"
                )
                
                fine_tune_btn = gr.Button(
                    "🎓 بدء Fine-tuning الآن",
                    variant="primary",
                    size="lg"
                )
                
                fine_tune_result = gr.Textbox(
                    label="النتيجة",
                    lines=6,
                    interactive=False
                )
                
                fine_tune_btn.click(
                    advanced_assistant.trigger_fine_tune,
                    inputs=epochs_slider,
                    outputs=fine_tune_result
                )
                
                gr.Markdown("""
                ---
                ⚠️ **تنبيه**: Fine-tuning يحتاج بيانات كافية للعمل بشكل صحيح.
                استخدم المساعد بكثرة لجمع بيانات أفضل!
                """)
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 4: الذاكرة طويلة المدى
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("🧠 الذاكرة طويلة المدى"):
                gr.Markdown("""
                ### 🧠 البحث في الذاكرة
                ابحث في جميع المحادثات السابقة
                """)
                
                memory_query = gr.Textbox(
                    label="ماذا تريد أن تتذكر؟",
                    placeholder="مثال: ما قلته عن Python",
                    lines=2
                )
                
                memory_limit = gr.Slider(
                    label="عدد النتائج",
                    minimum=1,
                    maximum=20,
                    value=5,
                    step=1
                )
                
                search_memory_btn = gr.Button("🔍 بحث في الذاكرة", variant="primary")
                
                memory_results = gr.Markdown(label="النتائج")
                
                search_memory_btn.click(
                    advanced_assistant.search_memory,
                    inputs=[memory_query, memory_limit],
                    outputs=memory_results
                )
                
                gr.Markdown("""
                💡 **فائدة**: الذاكرة طويلة المدى تحفظ آخر 1000 تفاعل
                """)
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 5: الإحصائيات المتقدمة
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("📊 الإحصائيات"):
                gr.Markdown("### 📈 إحصائيات شاملة")
                
                stats_display = gr.Markdown()
                
                with gr.Row():
                    refresh_btn = gr.Button("🔄 تحديث", variant="secondary")
                    save_state_btn = gr.Button("💾 حفظ الحالة", variant="primary")
                
                save_status = gr.Textbox(label="الحالة", visible=False)
                
                refresh_btn.click(
                    advanced_assistant.get_advanced_stats_ui,
                    outputs=stats_display
                )
                
                save_state_btn.click(
                    advanced_assistant.save_state_ui,
                    outputs=save_status
                ).then(
                    lambda x: gr.Info(x),
                    inputs=save_status
                )
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 6: الإعدادات
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("⚙️ الإعدادات"):
                gr.Markdown("### ⚙️ إعدادات المساعد المتقدم")
                
                with gr.Row():
                    model_path = gr.Textbox(
                        label="مسار النموذج",
                        value="checkpoints/best_model.safetensors"
                    )
                    vocab_path = gr.Textbox(
                        label="مسار القاموس",
                        value="vocab.json"
                    )
                
                kb_path = gr.Textbox(
                    label="مسار قاعدة المعرفة (اختياري)",
                    value=""
                )
                
                with gr.Row():
                    enable_web_search = gr.Checkbox(
                        label="تفعيل البحث في الويب",
                        value=True
                    )
                    enable_learning = gr.Checkbox(
                        label="تفعيل التعلم المستمر",
                        value=True
                    )
                
                init_btn = gr.Button("🚀 تهيئة المساعد", variant="primary", size="lg")
                init_status = gr.Textbox(label="حالة التهيئة", lines=8, interactive=False)
                
                init_btn.click(
                    advanced_assistant.initialize,
                    inputs=[model_path, vocab_path, kb_path, enable_web_search, enable_learning],
                    outputs=init_status
                )
                
                gr.Markdown("""
                ---
                ### 🌟 المميزات الجديدة
                
                **🌐 البحث في الويب**:
                - بحث تلقائي عند السؤال عن معلومات حديثة
                - دعم DuckDuckGo (المزيد قريباً)
                - نتائج فورية وموثوقة
                
                **🎓 التعلم المستمر**:
                - جمع تلقائي للبيانات عالية الجودة
                - Fine-tuning دوري للنموذج
                - تحسن مستمر في الدقة
                
                **🧠 الذاكرة طويلة المدى**:
                - حفظ 1000 تفاعل
                - بحث ذكي في التاريخ
                - سياق أفضل للمحادثات
                """)
        
        demo.load(
            advanced_assistant.get_advanced_stats_ui,
            outputs=stats_display
        )
    
    return demo

# ═══════════════════════════════════════════════════════════════════════════════
# التشغيل
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=7860)
    parser.add_argument('--share', action='store_true')
    parser.add_argument('--model', type=str)
    parser.add_argument('--vocab', type=str)
    
    args = parser.parse_args()
    
    # تهيئة تلقائية
    if args.model and args.vocab:
        result = advanced_assistant.initialize(
            args.model, 
            args.vocab,
            enable_web_search=True,
            enable_learning=True
        )
        logger.info(result)
    
    demo = create_advanced_interface()
    
    print("\n" + "="*60)
    print("🧠 المساعد الذكي المتقدم")
    print("="*60)
    print(f"📍 http://localhost:{args.port}")
    print("\n🌟 المميزات:")
    print("  🌐 البحث في الويب")
    print("  🎓 التعلم المستمر")
    print("  🧠 ذاكرة طويلة المدى")
    print("="*60 + "\n")
    
    demo.launch(
        server_port=args.port,
        share=args.share,
        show_error=True,
        inbrowser=True
    )
