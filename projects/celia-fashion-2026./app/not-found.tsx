"use client";

import Link from 'next/link';
import { motion } from 'framer-motion';
import { ShoppingBag, ArrowRight, Zap, AlertTriangle } from 'lucide-react';

export default function NotFound() {
    return (
        <div className="h-screen bg-black text-white flex flex-col items-center justify-center p-6 font-mono overflow-hidden" dir="rtl">
            {/* Background Grid */}
            <div className="absolute inset-0 opacity-5 pointer-events-none" style={{
                backgroundImage: 'linear-gradient(var(--color-neon-lime) 1px, transparent 1px), linear-gradient(90deg, var(--color-neon-lime) 1px, transparent 1px)',
                backgroundSize: '40px 40px'
            }} />

            {/* Holographic scanning effect */}
            <div className="absolute inset-0 pointer-events-none overflow-hidden opacity-20">
                <div className="w-full h-1 bg-accent/50 absolute animate-scan-line shadow-[0_0_20px_var(--color-neon-lime)]" />
            </div>

            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-center z-10 space-y-8"
            >
                <div className="relative inline-block">
                    <h1 className="text-[12rem] font-black leading-none tracking-tighter italic text-transparent bg-clip-text bg-gradient-to-b from-accent via-white to-zinc-900 opacity-20">
                        404
                    </h1>
                    <div className="absolute inset-0 flex items-center justify-center">
                        <AlertTriangle className="w-24 h-24 text-accent animate-pulse" />
                    </div>
                </div>

                <div className="space-y-2">
                    <h2 className="text-3xl font-black uppercase tracking-[0.2em]">Lost in the Void</h2>
                    <p className="text-zinc-500 text-sm max-w-sm mx-auto uppercase tracking-widest font-light">
                        الصفحة التي تبحث عنها غير موجودة في هذا الخط الزمني.
                    </p>
                </div>

                <div className="flex flex-col md:flex-row gap-4 justify-center items-center pt-8">
                    <Link href="/shop" className="group h-16 px-10 bg-white text-black flex items-center gap-3 font-black uppercase text-xs tracking-[0.2em] rounded-2xl hover:bg-accent transition-all">
                        <ShoppingBag className="w-4 h-4" />
                        العودة للمتجر
                        <ArrowRight className="w-4 h-4 -rotate-180 group-hover:translate-x-[-10px] transition-transform" />
                    </Link>

                    <Link href="/" className="h-16 px-10 border border-zinc-800 text-zinc-400 flex items-center gap-3 font-black uppercase text-xs tracking-[0.2em] rounded-2xl hover:border-accent hover:text-white transition-all">
                        <Zap className="w-4 h-4" />
                        الرئيسية
                    </Link>
                </div>
            </motion.div>

            <div className="absolute bottom-10 text-[8px] font-mono tracking-[1em] text-zinc-800 uppercase">
                CELIA 2026 // NULL_REFERENCE_DETECTED
            </div>
        </div>
    );
}
