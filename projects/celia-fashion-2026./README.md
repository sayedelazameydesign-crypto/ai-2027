# CELIA FASHION 2026 // AI-POWERED RETAIL OS

![Project Status](https://img.shields.io/badge/Status-Optimal-oklch(0.85_0.26_126.34))
![Version](https://img.shields.io/badge/Version-2.6.0-black)
![Tech Stack](https://img.shields.io/badge/Stack-Next.js%2016%20|%20Supabase%20|%20HuggingFace-blue)

**CELIA FASHION** ليس مجرد متجر إلكتروني، بل هو نظام تشغيل متكامل لتجربة تسوق أزياء مستقبلية في عام 2026. تم بناؤه باستخدام تقنيات الذكاء الاصطناعي التوليدي والواجهات الهولوجرامية لتقديم تجربة تسوق شخصية وفريدة من نوعها.

---

## 🚀 الميزات الرئيسية (Key Features)

### 🤖 الذكاء الاصطناعي (AI Core)
- **Virtual AI Stylist**: منسق أزياء ذكي يحلل قطع الملابس ويقترح تنسيقات كاملة باستخدام نماذج LLM (Hugging Face).
- **Aura Personalization**: نظام تخصيص واجهة المستخدم بناءً على العوامل المحيطة (الطقس، الوقت، الموقع).
- **Smart Sizing AI**: مساعد دردشة تفاعلي يتنبأ بالمقاس المناسب بناءً على بيانات المستخدم.
- **Visual Search**: إمكانية البحث عن المنتجات عبر رفع الصور (Computer Vision).
- **Dynamic Pricing**: تسعير ديناميكي يتغير بناءً على العرض والطلب والمؤشرات العالمية.

### 💎 تجربة المستخدم (UX/UI)
- **2026 Cyber-Minimalist Aesthetic**: تصميم عصري يعتمد على نظام ألوان فسفوري (Neon Lime) وتدرجات داكنة.
- **Holographic Visuals**: تأثيرات بصرية متطورة مثل "Scan Lines" ونماذج ثلاثية الأبعاد (Alpha).
- **Bento Grid Layout**: تنظيم المنتجات والبيانات بأسلوب بنتو الحديث والمتجاوب.
- **Glassmorphism Panels**: واجهات شفافة مع تأثيرات Blur لخلق عمق بصري.

### ⚙️ الإدارة والتحكم (Admin OS)
- **Trend Radar**: شاشة تحليلات تنبؤية لمراقبة الترندات القادمة قبل حدوثها.
- **Command Center Dashboard**: لوحة تحكم متطورة لمتابعة المبيعات والبيانات اللحظية باستخدام Recharts و Framer Motion.
- **Data Sync System**: نظام مزامنة بيانات بضغطة واحدة بين البيانات الوهمية وقاعدة بيانات Supabase.

---

## 🛠 التكنولوجيا المستخدمة (Tech Stack)

- **Framework**: [Next.js 16](https://nextjs.org/) (App Router & Turbopack).
- **Styling**: [Tailwind CSS](https://tailwindcss.com/) + [OKLCH Colors](https://oklch.com/).
- **Database**: [Supabase](https://supabase.com/) (PostgreSQL).
- **AI Models**: [Hugging Face Inference API](https://huggingface.co/).
- **Animations**: [Framer Motion](https://www.framer.com/motion/).
- **Charts**: [Recharts](https://recharts.org/).
- **Icons**: [Lucide React](https://lucide.dev/).
- **State Management**: [Zustand](https://github.com/pmndrs/zustand).

---

## 📦 البدء بالعمل (Getting Started)

### المتطلبات (Prerequisites)
- Node.js 18+ 
- حساب Supabase
- توكن API من Hugging Face

### التثبيت (Installation)

1. قم بتحميل المشروع:
   ```bash
   git clone [repository-url]
   cd fashion-store
   ```

2. تثبيت المكتبات:
   ```bash
   npm install
   ```

3. إعداد البيئة (.env):
   قم بإنشاء ملف `.env` وإضافة القيم التالية:
   ```env
   NEXT_PUBLIC_SUPABASE_URL=your_supabase_url
   NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
   HUGGINGFACE_API_TOKEN=your_hf_token
   ```

4. تشغيل المشروع:
   ```bash
   npm run dev
   ```

5. مزامنة البيانات (أول مرة فقط):
   انتقل إلى `http://localhost:3000/dashboard/sync` لتعبئة قاعدة البيانات بالمنتجات الأولية.

---

## 📐 هيكلة الملفات (Folder Structure)

- `app/`: صفحات النكست (Next.js Pages & Layouts).
- `components/`: المكونات القابلة لإعادة الاستخدام (UI, Fashion, Admin).
- `lib/`: الوظائف البرمجية الأساسية (AI, Supabase, Actions).
- `store/`: إدارة الحالة (Cart, Preferences).
- `public/`: الصور والملفات الثابتة.

---

## 📜 الترخيص (License)
© 2026 CELIA DIGITAL RETAIL. جميع الحقوق محفوظة. تم التطوير لصالح العلامة التجارية "Celia Fashion Design".

---

> "Future is not what you wear, it's how you sync with it." — *CELIA AI*
