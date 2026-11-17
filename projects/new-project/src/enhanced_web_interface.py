"""
🌐 واجهة ويب محسّنة - Enhanced Web Interface
===============================================

واجهة شاملة للمساعد الذكي المحسّن مع:
- قاعدة المعرفة
- التوصيات الذكية
- نظام التقييمات
- معالجة المستندات
"""

import gradio as gr
from typing import Optional, Tuple, List
import logging
from pathlib import Path
import json

from enhanced_assistant import (
    EnhancedSmartAssistant,
    TaskType,
    KnowledgeBase,
    DocumentProcessor
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# المساعد المحسّن العام
# ═══════════════════════════════════════════════════════════════════════════════

class EnhancedWebAssistant:
    """غلاف للمساعد المحسّن للاستخدام في الويب"""
    
    def __init__(self):
        self.assistant: Optional[EnhancedSmartAssistant] = None
        self.is_initialized = False
    
    def initialize(self, 
                  model_path: str, 
                  vocab_path: str,
                  kb_path: Optional[str] = None) -> str:
        """تهيئة المساعد المحسّن"""
        try:
            if not Path(vocab_path).exists():
                return f"❌ ملف القاموس غير موجود: {vocab_path}"
            
            logger.info("🚀 تهيئة المساعد الذكي المحسّن...")
            
            self.assistant = EnhancedSmartAssistant(
                model_path=model_path,
                vocab_path=vocab_path,
                kb_path=kb_path,
                enable_recommendations=True,
                enable_feedback=True
            )
            
            self.is_initialized = True
            logger.info("✅ المساعد المحسّن جاهز!")
            
            stats = self.assistant.get_enhanced_stats()
            return (
                f"✅ تم تهيئة المساعد بنجاح!\n\n"
                f"📊 الإحصائيات:\n"
                f"- قاعدة المعرفة: {stats['knowledge_base']['total_items']} عنصر\n"
                f"- الفئات: {stats['knowledge_base']['total_categories']}\n"
            )
            
        except Exception as e:
            logger.error(f"❌ خطأ في التهيئة: {e}")
            return f"❌ خطأ: {str(e)}"
    
    def chat_enhanced(self, 
                     message: str, 
                     image,
                     use_knowledge: bool,
                     history: List) -> Tuple[List, str]:
        """محادثة محسّنة مع قاعدة المعرفة"""
        if not self.is_initialized:
            history.append((message, "❌ المساعد غير مهيأ"))
            return history, ""
        
        try:
            user_msg = message
            if image is not None:
                user_msg += " 🖼️"
            
            # استخدام المعالجة المحسّنة
            response = self.assistant.process_with_knowledge(
                text=message,
                image=image,
                use_knowledge=use_knowledge,
                use_context=True
            )
            
            # تنسيق الرد
            reply = f"{response.prediction}\n\n{response.explanation}"
            
            # إضافة معلومات قاعدة المعرفة
            if response.metadata.get('knowledge_used'):
                reply += f"\n\n💡 تم استخدام {response.metadata['knowledge_sources']} مصدر من قاعدة المعرفة"
            
            history.append((user_msg, reply))
            return history, ""
            
        except Exception as e:
            history.append((message, f"❌ خطأ: {str(e)}"))
            return history, ""
    
    def get_recommendations_ui(self, query: str, top_k: int) -> str:
        """الحصول على توصيات للعرض"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        try:
            recommendations = self.assistant.get_recommendations(
                query=query if query else None,
                top_k=top_k
            )
            
            if not recommendations:
                return "لا توجد توصيات حالياً. جرب البحث عن شيء محدد!"
            
            output = "## 🎯 توصيات لك:\n\n"
            
            for i, rec in enumerate(recommendations, 1):
                output += f"### {i}. {rec['topic']}\n"
                output += f"**الفئة**: {rec['category']}\n\n"
                output += f"{rec['content']}\n\n"
                
                if 'reason' in rec:
                    output += f"_السبب: {rec['reason']}_\n\n"
                
                output += f"**النقاط**: {rec['score']:.2f}\n\n"
                output += "---\n\n"
            
            return output
            
        except Exception as e:
            return f"❌ خطأ: {str(e)}"
    
    def search_knowledge_ui(self, query: str, top_k: int) -> str:
        """البحث في قاعدة المعرفة"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        try:
            results = self.assistant.search_knowledge(query, top_k=top_k)
            
            if not results:
                return f"لم يتم العثور على نتائج لـ '{query}'"
            
            output = f"## 🔍 نتائج البحث عن: '{query}'\n\n"
            
            for i, result in enumerate(results, 1):
                output += f"### {i}. {result['topic']}\n"
                output += f"**الفئة**: {result['category']}\n\n"
                output += f"{result['content']}\n\n"
                output += f"**النقاط**: {result['score']:.2f}\n\n"
                output += "---\n\n"
            
            return output
            
        except Exception as e:
            return f"❌ خطأ: {str(e)}"
    
    def add_knowledge_ui(self, category: str, topic: str, content: str) -> str:
        """إضافة معرفة جديدة"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        if not all([category, topic, content]):
            return "⚠️ يرجى ملء جميع الحقول"
        
        try:
            self.assistant.add_knowledge(category, topic, content)
            return f"✅ تمت إضافة '{topic}' في فئة '{category}'"
        except Exception as e:
            return f"❌ خطأ: {str(e)}"
    
    def rate_response_ui(self, rating: int, correction: str = None) -> str:
        """تقييم آخر استجابة"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        # الحصول على آخر استجابة من الذاكرة
        if not self.assistant.memory.history:
            return "⚠️ لا توجد استجابات سابقة للتقييم"
        
        try:
            # محاكاة استجابة (في التطبيق الفعلي، احفظ الاستجابة الأخيرة)
            from enhanced_assistant import AssistantResponse
            
            last_interaction = self.assistant.memory.history[-1]
            
            response = AssistantResponse(
                task="general",
                input_text=last_interaction.get('user_input', ''),
                input_image=None,
                prediction=last_interaction['response']['prediction'],
                confidence=last_interaction['response']['confidence'],
                probabilities={},
                explanation="",
                processing_time=0,
                metadata={}
            )
            
            self.assistant.add_feedback(
                response=response,
                rating=rating,
                correction=correction if correction else None
            )
            
            return f"✅ شكراً لتقييمك! ({rating}/5)"
            
        except Exception as e:
            return f"❌ خطأ: {str(e)}"
    
    def process_document_ui(self, file) -> str:
        """معالجة ملف مرفوع"""
        if not self.is_initialized:
            return "❌ المساعد غير مهيأ"
        
        if file is None:
            return "⚠️ يرجى رفع ملف"
        
        try:
            result = self.assistant.process_document(file.name)
            
            output = f"## 📄 تحليل الملف\n\n"
            output += f"**النوع**: {result['type']}\n\n"
            output += f"**الملخص**: {result['summary']}\n\n"
            output += f"### المحتوى:\n```\n{result['content'][:500]}"
            
            if len(result['content']) > 500:
                output += "\n... (تم اختصار المحتوى)"
            
            output += "\n```\n\n"
            output += f"### معلومات الملف:\n"
            
            for key, value in result['metadata'].items():
                output += f"- **{key}**: {value}\n"
            
            return output
            
        except Exception as e:
            return f"❌ خطأ في معالجة الملف: {str(e)}"
    
    def get_enhanced_stats_ui(self) -> str:
        """الحصول على الإحصائيات المحسّنة"""
        if not self.is_initialized:
            return "المساعد غير مهيأ"
        
        try:
            stats = self.assistant.get_enhanced_stats()
            
            output = "## 📊 إحصائيات شاملة\n\n"
            
            # إحصائيات أساسية
            output += "### المساعد الأساسي\n"
            output += f"- **إجمالي الاستفسارات**: {stats['total_queries']}\n"
            output += f"- **نسبة النجاح**: {stats['success_rate']:.1%}\n"
            output += f"- **متوسط الثقة**: {stats['avg_confidence']:.1%}\n"
            output += f"- **حجم الذاكرة**: {stats['memory_size']} تفاعل\n\n"
            
            # قاعدة المعرفة
            output += "### قاعدة المعرفة\n"
            output += f"- **الفئات**: {stats['knowledge_base']['total_categories']}\n"
            output += f"- **العناصر**: {stats['knowledge_base']['total_items']}\n"
            
            for cat, count in stats['knowledge_base']['categories'].items():
                output += f"  - {cat}: {count}\n"
            
            output += "\n"
            
            # التقييمات
            if 'feedback' in stats:
                output += "### التقييمات\n"
                output += f"- **الإجمالي**: {stats['feedback']['total']}\n"
                output += f"- **متوسط التقييم**: {stats['feedback']['avg_rating']:.2f}/5\n"
                
                if 'rating_distribution' in stats['feedback']:
                    output += "- **توزيع التقييمات**:\n"
                    for rating, count in sorted(stats['feedback']['rating_distribution'].items()):
                        output += f"  - {rating} نجوم: {count}\n"
                
                output += "\n"
            
            # ملفات المستخدمين
            if 'user_profiles' in stats:
                output += "### المستخدمون\n"
                output += f"- **عدد الملفات**: {stats['user_profiles']}\n\n"
            
            return output
            
        except Exception as e:
            return f"❌ خطأ: {str(e)}"

# المساعد المحسّن العام
enhanced_assistant = EnhancedWebAssistant()

# ═══════════════════════════════════════════════════════════════════════════════
# بناء الواجهة المحسّنة
# ═══════════════════════════════════════════════════════════════════════════════

def create_enhanced_interface():
    """إنشاء واجهة Gradio محسّنة"""
    
    theme = gr.themes.Soft(primary_hue="blue", secondary_hue="cyan")
    
    with gr.Blocks(theme=theme, title="🤖 المساعد الذكي المحسّن", css="""
        .header {
            text-align: center;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
            margin-bottom: 20px;
        }
    """) as demo:
        
        gr.HTML("""
            <div class="header">
                <h1>🤖 المساعد الذكي المحسّن</h1>
                <p>مساعد متقدم مع قاعدة معرفة، توصيات ذكية، ونظام تقييمات</p>
            </div>
        """)
        
        with gr.Tabs():
            # ═══════════════════════════════════════════════════════════════
            # تبويب 1: المحادثة المحسّنة
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("💬 المحادثة"):
                gr.Markdown("""
                ### 💬 محادثة ذكية مع قاعدة المعرفة
                المساعد الآن يستطيع استخدام قاعدة معرفة خارجية لإجابات أفضل!
                """)
                
                use_kb_check = gr.Checkbox(
                    label="استخدام قاعدة المعرفة",
                    value=True,
                    info="تفعيل للحصول على معلومات إضافية من قاعدة المعرفة"
                )
                
                chatbot = gr.Chatbot(label="المحادثة", height=400)
                
                with gr.Row():
                    with gr.Column(scale=4):
                        msg = gr.Textbox(
                            label="رسالتك",
                            placeholder="اكتب رسالتك هنا...",
                            lines=2
                        )
                    with gr.Column(scale=1):
                        chat_image = gr.Image(label="صورة", type="pil", height=100)
                
                with gr.Row():
                    send_btn = gr.Button("📤 إرسال", variant="primary")
                    clear_btn = gr.Button("🗑️ مسح")
                
                chat_status = gr.Textbox(visible=False)
                
                send_btn.click(
                    enhanced_assistant.chat_enhanced,
                    inputs=[msg, chat_image, use_kb_check, chatbot],
                    outputs=[chatbot, chat_status]
                ).then(lambda: ("", None), outputs=[msg, chat_image])
                
                clear_btn.click(lambda: [], outputs=chatbot)
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 2: قاعدة المعرفة
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("📚 قاعدة المعرفة"):
                gr.Markdown("""
                ### 📚 إدارة قاعدة المعرفة
                ابحث وأضف معلومات لقاعدة المعرفة
                """)
                
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("#### 🔍 البحث")
                        search_query = gr.Textbox(
                            label="استعلام البحث",
                            placeholder="ابحث عن معلومة..."
                        )
                        search_limit = gr.Slider(
                            label="عدد النتائج",
                            minimum=1,
                            maximum=10,
                            value=5,
                            step=1
                        )
                        search_btn = gr.Button("🔍 بحث", variant="primary")
                        
                        search_results = gr.Markdown(label="النتائج")
                    
                    with gr.Column():
                        gr.Markdown("#### ➕ إضافة معرفة جديدة")
                        new_category = gr.Textbox(
                            label="الفئة",
                            placeholder="مثال: tech, science, general"
                        )
                        new_topic = gr.Textbox(
                            label="الموضوع",
                            placeholder="مثال: Python"
                        )
                        new_content = gr.Textbox(
                            label="المحتوى",
                            placeholder="أدخل المعلومة هنا...",
                            lines=4
                        )
                        add_kb_btn = gr.Button("➕ إضافة", variant="primary")
                        
                        add_status = gr.Textbox(label="الحالة")
                
                search_btn.click(
                    enhanced_assistant.search_knowledge_ui,
                    inputs=[search_query, search_limit],
                    outputs=search_results
                )
                
                add_kb_btn.click(
                    enhanced_assistant.add_knowledge_ui,
                    inputs=[new_category, new_topic, new_content],
                    outputs=add_status
                ).then(
                    lambda: ("", "", ""),
                    outputs=[new_category, new_topic, new_content]
                )
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 3: التوصيات
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("🎯 التوصيات"):
                gr.Markdown("""
                ### 🎯 توصيات ذكية
                احصل على توصيات بناءً على اهتماماتك والسياق
                """)
                
                rec_query = gr.Textbox(
                    label="ماذا تريد؟ (اختياري)",
                    placeholder="مثال: تعلم البرمجة، أخبار تقنية..."
                )
                
                rec_limit = gr.Slider(
                    label="عدد التوصيات",
                    minimum=1,
                    maximum=10,
                    value=5,
                    step=1
                )
                
                get_rec_btn = gr.Button("🎯 احصل على توصيات", variant="primary", size="lg")
                
                recommendations_output = gr.Markdown(label="التوصيات")
                
                get_rec_btn.click(
                    enhanced_assistant.get_recommendations_ui,
                    inputs=[rec_query, rec_limit],
                    outputs=recommendations_output
                )
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 4: معالجة المستندات
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("📄 معالجة المستندات"):
                gr.Markdown("""
                ### 📄 معالجة المستندات
                ارفع ملف لتحليل محتواه (txt, md, json, csv)
                """)
                
                file_input = gr.File(
                    label="ارفع ملف",
                    file_types=[".txt", ".md", ".json", ".csv"]
                )
                
                process_btn = gr.Button("📄 تحليل الملف", variant="primary", size="lg")
                
                doc_output = gr.Markdown(label="نتائج التحليل")
                
                process_btn.click(
                    enhanced_assistant.process_document_ui,
                    inputs=file_input,
                    outputs=doc_output
                )
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 5: التقييمات
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("⭐ التقييمات"):
                gr.Markdown("""
                ### ⭐ قيّم أداء المساعد
                تقييمك يساعدنا على التحسين!
                """)
                
                gr.Markdown("#### قيّم آخر استجابة")
                
                rating_slider = gr.Slider(
                    label="التقييم",
                    minimum=1,
                    maximum=5,
                    value=3,
                    step=1,
                    info="1 = سيء جداً، 5 = ممتاز"
                )
                
                correction_text = gr.Textbox(
                    label="تصحيح (اختياري)",
                    placeholder="إذا كانت الإجابة خاطئة، ما هي الإجابة الصحيحة؟",
                    lines=3
                )
                
                submit_rating_btn = gr.Button("✅ إرسال التقييم", variant="primary")
                
                rating_status = gr.Textbox(label="الحالة")
                
                submit_rating_btn.click(
                    enhanced_assistant.rate_response_ui,
                    inputs=[rating_slider, correction_text],
                    outputs=rating_status
                ).then(
                    lambda: ("", 3),
                    outputs=[correction_text, rating_slider]
                )
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 6: الإحصائيات المحسّنة
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("📊 الإحصائيات"):
                gr.Markdown("### 📈 إحصائيات شاملة")
                
                stats_display = gr.Markdown()
                
                refresh_stats_btn = gr.Button("🔄 تحديث", variant="secondary")
                
                refresh_stats_btn.click(
                    enhanced_assistant.get_enhanced_stats_ui,
                    outputs=stats_display
                )
            
            # ═══════════════════════════════════════════════════════════════
            # تبويب 7: الإعدادات
            # ═══════════════════════════════════════════════════════════════
            with gr.TabItem("⚙️ الإعدادات"):
                gr.Markdown("""
                ### ⚙️ إعدادات المساعد المحسّن
                """)
                
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
                    placeholder="knowledge_base.json"
                )
                
                init_btn = gr.Button("🚀 تهيئة المساعد", variant="primary", size="lg")
                init_status = gr.Textbox(label="حالة التهيئة", interactive=False)
                
                init_btn.click(
                    enhanced_assistant.initialize,
                    inputs=[model_path, vocab_path, kb_path],
                    outputs=init_status
                )
                
                gr.Markdown("""
                ---
                ### 🎉 المميزات الجديدة
                
                ✅ **قاعدة المعرفة**: استخدام معلومات خارجية للإجابات  
                ✅ **التوصيات الذكية**: توصيات بناءً على اهتماماتك  
                ✅ **نظام التقييمات**: تحسين مستمر من تقييماتك  
                ✅ **معالجة المستندات**: تحليل ملفات متعددة  
                ✅ **إحصائيات شاملة**: مراقبة متقدمة للأداء  
                """)
        
        demo.load(
            enhanced_assistant.get_enhanced_stats_ui,
            outputs=stats_display
        )
    
    return demo

# ═══════════════════════════════════════════════════════════════════════════════
# التشغيل
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='🌐 واجهة ويب محسّنة')
    parser.add_argument('--port', type=int, default=7860)
    parser.add_argument('--share', action='store_true')
    parser.add_argument('--model', type=str)
    parser.add_argument('--vocab', type=str)
    parser.add_argument('--kb', type=str, help='مسار قاعدة المعرفة')
    
    args = parser.parse_args()
    
    # تهيئة تلقائية
    if args.model and args.vocab:
        logger.info("🔧 تهيئة تلقائية...")
        result = enhanced_assistant.initialize(args.model, args.vocab, args.kb)
        logger.info(result)
    
    demo = create_enhanced_interface()
    
    print("\n" + "="*60)
    print("🌐 تشغيل الواجهة المحسّنة...")
    print(f"📍 العنوان: http://localhost:{args.port}")
    print("="*60 + "\n")
    
    demo.launch(
        server_port=args.port,
        share=args.share,
        show_error=True,
        inbrowser=True
    )
