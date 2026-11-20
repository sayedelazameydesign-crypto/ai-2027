export const categories = {
  dev: { label: "التطوير", accent: "dev" },
  automation: { label: "الأتمتة", accent: "lab" },
  research: { label: "البحث", accent: "research" },
  security: { label: "الأمان", accent: "security" },
  navigation: { label: "التصفح", accent: "navigation" },
  advanced: { label: "محلي متقدم", accent: "lab" },
};

export const extensions = [
  {
    id: "refined-github",
    name: "Refined GitHub",
    category: "dev",
    icon: "diff",
    plan: "مجاني",
    scope: "واجهة GitHub فقط",
    source: "https://github.com/refined-github/refined-github",
    description: "حسّن قراءة التغييرات والتنقل بين طلبات الدمج على GitHub.",
    how: "في ملف الاختبار، افتح تبويب الملفات في طلب الدمج العام أدناه. أزل التركيز من حقول الإدخال واضغط d ثم w (لوحة مفاتيح إنجليزية)، مع تفعيل ميزة one-click-diff-options.",
    expected: "لاحظ تبدّل ?w=1 في الرابط. كرر الإجراء والإضافة معطّلة للتأكد من أن الاختصار ليس من المتصفح أو GitHub نفسه.",
    testLink: ["https://github.com/refined-github/refined-github/pull/6261/files", "افتح طلب الدمج العام"],
  },
  {
    id: "octotree",
    name: "Octotree",
    category: "dev",
    icon: "tree",
    plan: "استخدام أساسي مجاني",
    scope: "واجهة GitHub فقط",
    source: "https://www.octotree.io/download",
    description: "استكشف المجلدات والملفات من شجرة جانبية بدل التنقل بين صفحات متعددة.",
    how: "افتح مستودع AI-2027 العام بدون تسجيل دخول. افتح شجرة Octotree ووسّع مجلد docs واختر BROWSERSKILL-PLAN.md.",
    expected: "تظهر شجرة الملفات ويفتح الملف المحدد. لا تدخل رمز وصول؛ الاختبار لا يتطلب مستودعًا خاصًا.",
    testLink: ["https://github.com/sayedelazameydesign-crypto/ai-2027", "افتح المستودع العام"],
  },
  {
    id: "ui-vision",
    name: "Ui.Vision RPA",
    category: "automation",
    icon: "bolt",
    plan: "إضافة مجانية",
    scope: "تشغيل يدوي محلي",
    source: "https://github.com/A9T9/RPA",
    description: "سجّل تعبئة نموذج أو أعد تشغيلها في ملف Chrome مخصص للاختبار.",
    how: "افتح المختبر في تبويب مستقل. سجّل كتابة ARENA-2027 والنقر على زر الفحص، ثم صفّر النتيجة وأعد التشغيل. يتوفر أيضًا ماكرو جاهز للخادم المحلي على المنفذ 8787.",
    expected: "يظهر LAB_OK بعد إعادة التشغيل. التجربة لا تفعّل وحدات النظام أو MCP ولا تمنح Arena تحكمًا بالمتصفح.",
    testLink: ["lab.html#fixture", "افتح نموذج المختبر"],
  },
  {
    id: "singlefile",
    name: "SingleFile",
    category: "research",
    icon: "file",
    plan: "مجاني ومفتوح المصدر",
    scope: "مشاركة ملخص يدويًا",
    source: "https://www.getsinglefile.com/#download",
    description: "احفظ نسخة من صفحة عامة في ملف HTML واحد للرجوع إليه من دون اتصال.",
    how: "احفظ تبويب المختبر بصيغة HTML عادية، دون ملء الحقل. أوقف الشبكة أو الخادم المحلي وافتح النسخة المحفوظة من القرص.",
    expected: "تبقى علامة ARENA-KIT-2027 وصورة العينة والتنسيق ظاهرة. شارك نصًا منقّحًا بدل لقطة صفحة خاصة.",
    testLink: ["lab.html#reference", "افتح العينة المرجعية"],
  },
  {
    id: "gofullpage",
    name: "GoFullPage",
    category: "research",
    icon: "camera",
    plan: "مجاني · نسخة أساسية",
    scope: "لقطة محلية، مشاركة يدوية",
    source: "https://gofullpage.com/",
    description: "التقط الصفحة كاملة رأسيًا كصورة واحدة لتوثيق نتيجة أو واجهة تراجعها بنفسك.",
    how: "افتح عينة الصفحة الطويلة أدناه والتقطها بالكامل. لا تلتقط صفحات فيها بيانات حساب أو بريد.",
    expected: "تظهر العلامتان CAPTURE-TOP-2027 وCAPTURE-BOTTOM-2027 في اللقطة نفسها. إذا ظهرت العليا فقط فلم تُلتقط الصفحة كاملة؛ سجّل «فشل يدويًا».",
    testLink: ["lab.html#longpage", "افتح عينة الصفحة الطويلة"],
  },
  {
    id: "json-formatter",
    name: "JSON Formatter",
    category: "dev",
    icon: "braces",
    plan: "مجاني",
    scope: "عرض محلي لملف أو استجابة",
    source: "https://chromewebstore.google.com/detail/json-formatter/bcjindcccaagfpapcccchgcdbkhfbgej",
    description: "يعرض JSON مُنسَّقًا ليصبح فحص استجابات API وملفات الإعداد ممكنًا بالعين.",
    how: "افتح ملفًا محليًا يحتوي عينة JSON من المختبر في تبويب، أو الصق العينة في أداة الإضافة إن وفّرتها. العينة اصطناعية وغير سرية.",
    expected: "تظهر ستة مفاتيح عليا منفصلة في سطور، ويبقى true وnull كما هما. إذا أضافت الأداة أو غيّرت قيمًا فسجّل «فشل يدويًا».",
    testLink: ["lab.html#json", "افتح عينة JSON"],
  },
  {
    id: "zotero-connector",
    name: "Zotero Connector",
    category: "research",
    icon: "book",
    plan: "موصّل مجاني",
    scope: "مشاركة استشهاد يدويًا",
    source: "https://www.zotero.org/download/connectors",
    description: "احفظ المراجع العامة في Zotero ثم صدّر استشهادًا لتشاركه مع الوكيل.",
    how: "شغّل تطبيق Zotero المكتبي وافتح دليل البداية العام أدناه. احفظ المرجع في مجموعة اختبار محلية لا تزامنها.",
    expected: "تطابق خانتي العنوان والرابط صفحة الدليل. صدّر استشهادًا واحدًا بعد مراجعته، لا المكتبة بالكامل.",
    testLink: ["https://www.zotero.org/support/quick_start_guide", "افتح المرجع العام"],
  },
  {
    id: "ublock-origin-lite",
    name: "uBlock Origin Lite",
    category: "security",
    icon: "shield",
    plan: "مجاني · Manifest V3",
    scope: "تصفية محلية",
    source: "https://chromewebstore.google.com/detail/ublock-origin-lite/ddkjiahejlhfcafbddmgiahcphecmpfh",
    description: "خفّف ظهور الإعلانات والمتتبعات باستخدام الإصدار Lite المخصص لمتصفحات Chromium الحديثة.",
    how: "على ملف مختبر بلا حاجب آخر، أعد تحميل المختبر مع تعطيل الإضافة ثم تفعيلها بالقوائم الافتراضية. افحص العيّنتين وطلبات Network في أدوات المطوّر.",
    expected: "تُحمَّل الصورتان في الأساس. مع التصفية تبقى العينة العادية وتُحجب الصورة المرشّحة بـ ERR_BLOCKED_BY_CLIENT. إذا لم تستطع إثبات السبب فسجّل «تعذّر الاختبار».",
    testLink: ["lab.html#filtering", "افتح عينتَي التصفية"],
  },
  {
    id: "bitwarden",
    name: "Bitwarden",
    category: "security",
    icon: "key",
    plan: "خطة شخصية مجانية",
    scope: "لا مشاركة مع الوكيل",
    source: "https://bitwarden.com/download/#downloads-web-browser-extensions",
    description: "استخدم مدير كلمات مرور منفصلًا عن الأتمتة، مع قفل تلقائي وتعبئة يدوية.",
    how: "افتح مولّد كلمات المرور من الإضافة واضبط الطول على 20. استخدم حساب اختبار فارغًا إذا طلب الإصدار تسجيل الدخول، أو سجّل «تعذّر الاختبار».",
    expected: "يعرض المولّد قيمة تجريبية بالطول المحدد. لا تنسخها أو تحفظها أو تلتقط صورة لها، ولا تستخدم خزانتك الشخصية للاختبار.",
  },
  {
    id: "vimium",
    name: "Vimium",
    category: "navigation",
    icon: "keyboard",
    plan: "مجاني ومفتوح المصدر",
    scope: "اختصارات محلية",
    source: "https://github.com/philc/vimium",
    description: "انتقل بين الروابط والتبويبات من لوحة المفاتيح دون تغيير سير عمل الوكيل.",
    how: "افتح المختبر، اضغط Esc للخروج من الحقل ثم f باللوحة الإنجليزية. اختر التلميح الذي يظهر فوق رابط العلامة المرجعية.",
    expected: "ينتقل المتصفح إلى #reference في الصفحة نفسها، ولا يُنفَّذ أي أمر في Arena.",
    testLink: ["lab.html", "افتح المختبر في تبويب"],
  },
  {
    id: "browserskill",
    name: "BrowserSkill",
    category: "advanced",
    icon: "terminal",
    plan: "مفتوح المصدر · اختياري",
    scope: "محوّل محلي، لا تكامل مثبت مع Arena",
    source: "https://github.com/Tencent/BrowserSkill",
    description: "جرّب محوّل المستودع المحلي في ملف معزول. اختبارات المحوّل ليست إثباتًا لاتصال Chrome.",
    how: "عند الحاجة، اتبع دليل المحوّل على الجهاز نفسه. شغّل الفحص للقراءة فقط، ثم اختبر example.com بوضع التصفّح المتخفّي في المرحلة المحلية حصراً.",
    expected: "READY يثبت الجاهزية المحلية فقط. نجاح المهمة يتطلب VERIFIED وإغلاق الجلسة وتطابق بصمة الأثر. لا يثبت أي منهما اتصالًا بخدمة Arena.",
    testLink: ["https://github.com/sayedelazameydesign-crypto/ai-2027/tree/main/browser_skill_adapter", "اقرأ دليل المحوّل المحلي"],
  },
];

export const statusLabels = {
  not_run: "لم يُختبر",
  pass: "نجح يدويًا",
  fail: "فشل يدويًا",
  blocked: "تعذّر الاختبار",
};

export function normalizeStatuses(input) {
  return Object.fromEntries(extensions.map(({ id }) => {
    const status = input && Object.hasOwn(input, id) ? input[id] : null;
    return [id, typeof status === "string" && Object.hasOwn(statusLabels, status) ? status : "not_run"];
  }));
}

function normalizedText(text) {
  return text.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "").trim();
}

export function filterExtensions(category, query) {
  const needle = normalizedText(query);
  return extensions.filter((entry) => {
    if (category !== "all" && category !== entry.category) return false;
    const text = `${entry.name} ${entry.description} ${categories[entry.category].label}`;
    return normalizedText(text).includes(needle);
  });
}

export function buildReport(input, date = new Date()) {
  const statuses = normalizeStatuses(input);
  return {
    kit: "Arena Productivity Kit 2027",
    schema_version: 1,
    source: "manual_local_self_report",
    exported_at: date.toISOString(),
    results: extensions.map(({ id, name }) => ({ id, name, status: statuses[id] })),
  };
}
