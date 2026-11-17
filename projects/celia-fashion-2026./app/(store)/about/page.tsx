"use client";
import { motion } from "framer-motion";
import { Globe, ShieldCheck, Cpu, Zap } from "lucide-react";

export default function ManifestoPage() {
    const fadeInVariants = {
        hidden: { opacity: 0, y: 30 },
        visible: (i: number) => ({
            opacity: 1,
            y: 0,
            transition: { delay: i * 0.2, duration: 0.8, ease: "easeOut" },
        }),
    } as any;

    return (
        <main className="min-h-screen bg-black text-white font-mono selection:bg-[#ccff00] selection:text-black dir-rtl">
            {/* Hero Section - Manifesto Header */}
            <section className="relative h-[70vh] flex items-center justify-center overflow-hidden border-b border-white/10">
                <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,_var(--color-neon-lime)_0%,_transparent_100%)] opacity-5" />
                <div className="z-10 text-center px-6">
                    <motion.span
                        initial={{ letterSpacing: "0.5em", opacity: 0 }}
                        animate={{ letterSpacing: "0.2em", opacity: 1 }}
                        className="text-[#ccff00] text-xs uppercase mb-4 block"
                    >
                        EST. 2026 | CAIRO
                    </motion.span>
                    <motion.h1
                        initial={{ scale: 0.9, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        className="text-6xl md:text-8xl font-black uppercase tracking-tighter italic"
                    >
                        Celia Manifesto
                    </motion.h1>
                </div>
            </section>

            {/* Main Content */}
            <section className="max-w-4xl mx-auto px-6 py-24 space-y-32">

                {/* Core Vision */}
                <motion.div
                    custom={1} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeInVariants}
                    className="space-y-6 text-right"
                >
                    <h2 className="text-3xl font-bold text-[#ccff00] flex items-center justify-end gap-3 italic">
                        الجوهر: صُمم في القاهرة <Globe className="text-white" />
                    </h2>
                    <p className="text-xl text-gray-400 leading-relaxed font-sans" dir="rtl">
                        في "سيليا"، نحن لا نبيع مجرد ملابس؛ نحن نصمم دروعاً عصرية لمواطني الغد. انطلقنا من قلب القاهرة، المدينة التي لا تنام، لنجمع بين عراقة التاريخ المصري وبين صخب التكنولوجيا في عام 2026.
                    </p>
                </motion.div>

                {/* Philosophy Cards */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-8" dir="rtl">
                    {[
                        {
                            title: "فلسفتنا",
                            desc: "الأناقة ليست ترفاً، بل هي موقف حازم في عالم رقمي متسارع.",
                            icon: <ShieldCheck size={40} />
                        },
                        {
                            title: "المستقبل الآن",
                            desc: "ملابس تتنفس معك وتتكيف مع بيئتك المحيطة بذكاء.",
                            icon: <Cpu size={40} />
                        }
                    ].map((item, i) => (
                        <motion.div
                            key={i} custom={i + 2} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeInVariants}
                            className="p-8 border border-white/10 bg-white/5 backdrop-blur-xl rounded-2xl hover:border-[#ccff00] transition-colors group text-right"
                        >
                            <div className="text-[#ccff00] mb-6 group-hover:scale-110 transition-transform flex justify-end">{item.icon}</div>
                            <h3 className="text-2xl font-bold mb-4 uppercase italic">{item.title}</h3>
                            <p className="text-gray-400 font-sans leading-relaxed">{item.desc}</p>
                        </motion.div>
                    ))}
                </div>

                {/* Quote Section */}
                <motion.div
                    custom={4} initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeInVariants}
                    className="text-center py-20 border-y border-white/10 relative"
                >
                    <Zap className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 text-[#ccff00] animate-pulse" />
                    <blockquote className="text-3xl md:text-5xl font-black uppercase italic leading-tight">
                        "الأناقة هي الموقف الذي تختاره <br /> <span className="text-[#ccff00] underline decoration-white/20">لمواجهة العالم</span>"
                    </blockquote>
                    <p className="mt-8 text-sm tracking-[0.3em] text-gray-500 uppercase">— CELIA FASHION 2026</p>
                </motion.div>

            </section>

            {/* Background Grid Pattern */}
            <div className="fixed inset-0 z-[-1] opacity-[0.03] pointer-events-none bg-[radial-gradient(#fff_1px,transparent_1px)] [background-size:16px_16px]" />
        </main>
    );
}
