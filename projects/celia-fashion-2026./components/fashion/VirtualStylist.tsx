"use client";

import React, { useState } from "react";
import Image from "next/image";
import { Sparkles, RefreshCw, ShoppingBag, Cpu, MessageCircle } from "lucide-react";
import { generateStylingAdvice } from "@/lib/ai";

interface OutfitPart {
    id: string;
    name: string;
    category: string;
    image: string;
}

const MOCK_SUGGESTIONS: OutfitPart[] = [
    { id: "s1", name: "Cyber-Link Jacket", category: "Outerwear", image: "https://images.unsplash.com/photo-1551028719-00167b16eac5?w=200" },
    { id: "s2", name: "Nano-Fiber Slims", category: "Bottoms", image: "https://images.unsplash.com/photo-1541099649105-f69ad21f3246?w=200" },
    { id: "s3", name: "Pulse-Step Kicks", category: "Footwear", image: "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=200" },
];

export const VirtualStylist = ({ productName = "هذا المنتج", category = "أزياء" }: { productName?: string, category?: string }) => {
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const [showResult, setShowResult] = useState(false);
    const [aiAdvice, setAiAdvice] = useState<string>("");

    const startAnalysis = async () => {
        setIsAnalyzing(true);

        // 1. Call real AI for advice
        const advice = await generateStylingAdvice(productName, category);
        setAiAdvice(advice);

        // 2. Simulate visually complex processing
        setTimeout(() => {
            setIsAnalyzing(false);
            setShowResult(true);
        }, 1500);
    };

    return (
        <div className="p-6 rounded-3xl bg-zinc-950 border border-accent/20 text-white overflow-hidden relative group">
            <div className="absolute inset-0 bg-gradient-to-br from-accent/5 to-transparent pointer-events-none" />

            <div className="relative z-10 flex flex-col gap-6">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Cpu className="w-5 h-5 text-accent" />
                        <h3 className="font-heading font-bold text-lg uppercase tracking-tight">AI Virtual Stylist</h3>
                    </div>
                    <span className="text-[10px] font-mono text-accent bg-accent/10 px-2 py-0.5 rounded">v3.0 GENERATIVE</span>
                </div>

                {!showResult ? (
                    <div className="space-y-4">
                        <p className="text-zinc-400 text-sm leading-relaxed">
                            بناءً على اختيارك لـ <span className="text-white font-bold">{productName}</span>، ستقوم خوارزمية CELIA-GEN بإنشاء تنسيق مستقبلي ذكي لك.
                        </p>
                        <button
                            onClick={startAnalysis}
                            disabled={isAnalyzing}
                            className="w-full py-4 bg-accent text-black font-black text-xs uppercase tracking-widest flex items-center justify-center gap-2 hover:bg-white transition-colors disabled:opacity-50 group/btn"
                        >
                            {isAnalyzing ? (
                                <>
                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                    جاري المعالجة العصبية...
                                </>
                            ) : (
                                <>
                                    <Sparkles className="w-4 h-4 group-hover/btn:animate-pulse" />
                                    توليد التنسيق الذكي
                                </>
                            )}
                        </button>
                    </div>
                ) : (
                    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500" dir="rtl">
                        <div className="flex justify-between items-center text-xs text-zinc-500 font-mono">
                            <span>التنسيق الذكي: {productName} Core</span>
                            <button onClick={() => setShowResult(false)} className="hover:text-accent font-bold">إعادة البدء</button>
                        </div>

                        {/* AI Generative Text Result */}
                        <div className="p-4 bg-white/5 rounded-2xl border border-white/10 space-y-2">
                            <div className="flex items-center gap-2 text-[10px] text-accent font-black uppercase">
                                <MessageCircle className="w-3 h-3" /> نصيحة المنسق الذكي (AI Advice)
                            </div>
                            <p className="text-xs text-zinc-300 leading-relaxed font-light italic">
                                &quot;{aiAdvice}&quot;
                            </p>
                        </div>

                        <div className="grid grid-cols-3 gap-3">
                            {MOCK_SUGGESTIONS.map((item) => (
                                <div key={item.id} className="space-y-2 group/item">
                                    <div className="relative aspect-square rounded-xl overflow-hidden bg-zinc-900 border border-white/5 group-hover/item:border-accent transition-colors">
                                        <Image src={item.image} alt={item.name} fill className="object-cover" />
                                    </div>
                                    <div className="text-[10px] font-bold truncate">{item.name}</div>
                                    <div className="text-[8px] text-zinc-500 font-mono flex items-center gap-1">
                                        <ShoppingBag className="w-2 h-2 text-accent" />
                                        شراء
                                    </div>
                                </div>
                            ))}
                        </div>

                        <div className="p-3 bg-accent/10 rounded-xl border border-accent/20 text-[10px] italic text-accent flex items-center gap-2 uppercase font-black">
                            <Sparkles className="w-3 h-3" />
                            بصمة كربونية منخفضة بنسبة 15%
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
