"use client";

import Link from "next/link";
import { ArrowLeft, AlertCircle } from "lucide-react";

export default function NotFound() {
    return (
        <div className="min-h-screen bg-gradient-to-br from-[#0a0a0f] via-[#13131a] to-[#0a0a0f] text-white flex items-center justify-center px-6">
            {/* Animated Grid Background */}
            <div className="fixed inset-0 opacity-10 pointer-events-none">
                <div className="absolute inset-0" style={{
                    backgroundImage: 'linear-gradient(#15f4ee 1px, transparent 1px), linear-gradient(90deg, #15f4ee 1px, transparent 1px)',
                    backgroundSize: '60px 60px'
                }}></div>
            </div>

            <div className="max-w-2xl text-center space-y-8 relative z-10">
                {/* 404 Icon */}
                <div className="flex justify-center">
                    <div className="relative">
                        <AlertCircle className="w-32 h-32 text-[#15f4ee]" />
                        <div className="absolute inset-0 blur-xl bg-[#15f4ee]/30"></div>
                    </div>
                </div>

                {/* Error Code */}
                <div className="space-y-4">
                    <h1 className="text-8xl font-black">
                        <span className="bg-gradient-to-r from-[#15f4ee] via-[#66ff00] to-[#15f4ee] bg-clip-text text-transparent">
                            404
                        </span>
                    </h1>
                    <p className="text-2xl font-bold text-white">الصفحة غير موجودة</p>
                </div>

                {/* Description */}
                <p className="text-gray-400 text-lg leading-relaxed">
                    يبدو أن المقالة التي تبحث عنها قد تم نقلها أو لم تعد متاحة. لا تدع رحلتك تتوقف هنا.
                </p>

                {/* CTA Buttons */}
                <div className="flex items-center justify-center gap-4 pt-8">
                    <Link
                        href="/blog"
                        className="group flex items-center gap-2 px-8 py-4 rounded-full bg-gradient-to-r from-[#15f4ee] to-[#2454FF] text-white font-bold hover:scale-105 transition-all"
                    >
                        <ArrowLeft className="w-5 h-5 group-hover:-translate-x-1 transition-transform" />
                        <span>العودة للمقالات</span>
                    </Link>
                    
                    <Link
                        href="/"
                        className="px-8 py-4 rounded-full bg-white/5 border border-white/10 hover:border-[#15f4ee]/50 text-white font-bold hover:scale-105 transition-all"
                    >
                        الصفحة الرئيسية
                    </Link>
                </div>
            </div>
        </div>
    );
}