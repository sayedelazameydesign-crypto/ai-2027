import streamlit as st
import json
from datetime import datetime
from orchestrator import run_agent_task
from storage_manager import save_to_drive

# ═══════════════════════════════════════════════════════════════
# إعدادات الصفحة
# ═══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="CeliaOS Agent v2026",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ═══════════════════════════════════════════════════════════════
# CSS مخصص للواجهة الاحترافية
# ═══════════════════════════════════════════════════════════════
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 800;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .metric-card {
        background: #0b0f19;
        border: 1px solid #1e293b;
        border-radius: 12px;
        padding: 1rem;
        text-align: center;
    }
    .task-history {
        background: #0f172a;
        border-radius: 8px;
        padding: 1rem;
        margin: 0.5rem 0;
        border-right: 3px solid #667eea;
    }
    .status-dot {
        height: 10px;
        width: 10px;
        border-radius: 50%;
        display: inline-block;
        margin-left: 8px;
    }
    .status-active { background-color: #22c55e; }
    .status-idle { background-color: #6b7280; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════
# حالة الجلسة (Session State)
# ═══════════════════════════════════════════════════════════════
if "task_history" not in st.session_state:
    st.session_state.task_history = []
if "total_tasks" not in st.session_state:
    st.session_state.total_tasks = 0
if "last_result" not in st.session_state:
    st.session_state.last_result = None

# ═══════════════════════════════════════════════════════════════
# الشريط الجانبي - الإعدادات والمفاتيح
# ═══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown('<p class="main-header">⚙️ الإعدادات</p>', unsafe_allow_html=True)
    
    # ── مفتاح API ──
    api_key = st.secrets.get("GOOGLE_API_KEY") if "GOOGLE_API_KEY" in st.secrets else None
    
    if not api_key:
        st.warning("🔐 GOOGLE_API_KEY غير مُضبوط في Secrets")
        api_key = st.text_input(
            "أدخل Google API Key:",
            type="password",
            key="api_input",
            help="مفتاح Gemini API من Google AI Studio"
        )
        st.caption("💡 نصيحة: اضبط المفتاح في Streamlit Cloud → Settings → Secrets")
    
    # ── إعدادات متقدمة ──
    with st.expander("🔧 إعدادات متقدمة", expanded=False):
        model_choice = st.selectbox(
            "نموذج الذكاء الاصطناعي:",
            ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
            index=0
        )
        max_tokens = st.slider("الحد الأقصى للرموز:", 512, 8192, 4096, step=512)
        temperature = st.slider("درجة الإبداع:", 0.0, 1.0, 0.7, step=0.1)
        save_enabled = st.toggle("💾 الحفظ التلقائي في Google Drive", value=True)
    
    st.divider()
    
    # ── إحصائيات ──
    st.markdown("### 📊 إحصائيات الجلسة")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("المهام المنفذة", st.session_state.total_tasks)
    with col2:
        st.metric("المهام المخزنة", len(st.session_state.task_history))
    
    st.divider()
    
    # ── سجل المهام ──
    st.markdown("### 🕐 سجل المهام")
    if st.session_state.task_history:
        for idx, task in enumerate(reversed(st.session_state.task_history[-10:])):
            with st.container():
                st.markdown(f"""
                <div class="task-history">
                    <small>{task['timestamp']}</small><br/>
                    <b>{task['summary'][:50]}...</b>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("لا توجد مهام مسجلة بعد")
    
    # ── أزرار التحكم ──
    if st.button("🗑️ مسح السجل", use_container_width=True):
        st.session_state.task_history = []
        st.session_state.total_tasks = 0
        st.rerun()

# ═══════════════════════════════════════════════════════════════
# المحتوى الرئيسي
# ═══════════════════════════════════════════════════════════════
st.markdown('<h1 class="main-header">🤖 CeliaOS: وكيل التصفح المؤسسي</h1>', unsafe_allow_html=True)
st.caption("v2026.7 | Powered by Gemini 2.0 | Sovereign AI Architecture")

# ── شريط الحالة ──
status_col1, status_col2, status_col3 = st.columns([1, 1, 1])
with status_col1:
    st.markdown("""
    <div class="metric-card">
        <span class="status-dot status-active"></span>
        <b>النظام</b><br/>متصل
    </div>
    """, unsafe_allow_html=True)
with status_col2:
    st.markdown(f"""
    <div class="metric-card">
        <b>النموذج</b><br/>{model_choice if 'model_choice' in locals() else 'gemini-2.0-flash'}
    </div>
    """, unsafe_allow_html=True)
with status_col3:
    drive_status = "✅ مفعل" if save_enabled if 'save_enabled' in locals() else "⚪ معطل"
    st.markdown(f"""
    <div class="metric-card">
        <b>Google Drive</b><br/>{drive_status}
    </div>
    """, unsafe_allow_html=True)

st.divider()

# ═══════════════════════════════════════════════════════════════
# منطقة إدخال المهمة
# ═══════════════════════════════════════════════════════════════
st.markdown("### ✍️ ما هي مهمتك اليوم؟")

task_container = st.container()
with task_container:
    # نماذج سريعة
    quick_tasks = st.pills(
        "اختصارات سريعة:",
        ["🔍 بحث ويب", "📄 تلخيص مقال", "💻 كتابة كود", "📊 تحليل بيانات", "🌐 ترجمة"],
        key="quick_pills"
    )
    
    user_task = st.text_area(
        "وصف المهمة بالتفصيل:",
        placeholder="مثال: ابحث لي عن أحدث تقنيات الذكاء الاصطناعي في 2026 وقدم ملخصاً تنفيذياً...",
        height=120,
        key="task_input"
    )
    
    # ملء تلقائي من الاختصار
    if quick_tasks:
        templates = {
            "🔍 بحث ويب": "ابحث لي عن [الموضوع] وقدم ملخصاً شاملاً مع المصادر",
            "📄 تلخيص مقال": "لخص لي المقال التالي واستخرج النقاط الرئيسية:\n\n[ألصق النص هنا]",
            "💻 كتابة كود": "اكتب لي كود [اللغة] يقوم بـ [الوظيفة] مع شرح مفصل",
            "📊 تحليل بيانات": "حلل لي البيانات التالية وقدم رؤى قابلة للتنفيذ:\n\n[ألصق البيانات هنا]",
            "🌐 ترجمة": "ترجم النص التالي من [الجهة المصدر] إلى [الجهة الهدف]:\n\n[ألصق النص هنا]"
        }
        if user_task == "" or user_task in templates.values():
            st.session_state.task_input = templates.get(quick_tasks, "")
            st.rerun()

# ═══════════════════════════════════════════════════════════════
# تنفيذ المهمة
# ═══════════════════════════════════════════════════════════════
execute_col1, execute_col2 = st.columns([3, 1])
with execute_col2:
    execute_btn = st.button(
        "🚀 تنفيذ المهمة",
        type="primary",
        use_container_width=True,
        disabled=not api_key
    )

if execute_btn:
    if not user_task.strip():
        st.error("⚠️ يرجى إدخال وصف للمهمة أولاً")
    elif not api_key:
        st.error("🔐 يرجى توفير مفتاح API للمتابعة")
    else:
        # ── بدء المعالجة ──
        progress_placeholder = st.empty()
        status_placeholder = st.empty()
        
        with progress_placeholder:
            progress_bar = st.progress(0)
        
        steps = [
            "🔍 تحليل المهمة...",
            "🧠 توليد الاستراتيجية...",
            "⚡ تنفيذ الوكيل...",
            "💾 حفظ النتائج...",
            "✅ اكتمال!"
        ]
        
        try:
            for i, step in enumerate(steps[:-1]):
                status_placeholder.info(step)
                progress_bar.progress((i + 1) * 20)
            
            status_placeholder.info("⚡ تنفيذ الوكيل...")
            progress_bar.progress(80)
            
            # ── استدعاء الوكيل ──
            result = run_agent_task(user_task, api_key)
            
            progress_bar.progress(90)
            status_placeholder.info("💾 حفظ النتائج...")
            
            # ── الحفظ في Google Drive ──
            drive_path = None
            if save_enabled:
                try:
                    drive_path = save_to_drive(user_task, result)
                except Exception as drive_err:
                    st.warning(f"⚠️ لم يتم الحفظ في Drive: {drive_err}")
            
            progress_bar.progress(100)
            status_placeholder.success("✅ تم إنجاز المهمة بنجاح!")
            
            # ── تحديث السجل ──
            st.session_state.total_tasks += 1
            st.session_state.task_history.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "summary": user_task[:100],
                "full_task": user_task,
                "result": result,
                "drive_path": drive_path
            })
            st.session_state.last_result = result
            
            # ── عرض النتائج ──
            st.divider()
            st.markdown("### 📤 النتيجة")
            
            if drive_path:
                st.success(f"💾 تم الحفظ في Google Drive: `{drive_path}`")
            
            with st.container(border=True):
                st.markdown(result)
            
            # ── أدوات التصدير ──
            export_col1, export_col2, export_col3 = st.columns(3)
            with export_col1:
                st.download_button(
                    "📥 تحميل كـ Markdown",
                    data=str(result),
                    file_name=f"celia_task_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                    mime="text/markdown",
                    use_container_width=True
                )
            with export_col2:
                st.download_button(
                    "📥 تحميل كـ JSON",
                    data=json.dumps({"task": user_task, "result": str(result)}, ensure_ascii=False, indent=2),
                    file_name=f"celia_task_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json",
                    use_container_width=True
                )
            with export_col3:
                if st.button("📋 نسخ للحافظة", use_container_width=True):
                    st.toast("✅ تم النسخ!")
            
        except Exception as e:
            progress_placeholder.empty()
            status_placeholder.empty()
            st.error(f"❌ حدث خطأ أثناء التنفيذ: {str(e)}")
            st.exception(e)
