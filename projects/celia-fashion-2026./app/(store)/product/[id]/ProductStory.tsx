"use client";

import { useCartStore } from "@/store/cart";
import {
    ShoppingBag,
    Heart,
    Share2,
    Star,
    ShieldCheck,
    Truck,
    Cpu,
    ArrowRight,
    Zap,
    Box,
    Glasses,
    Eye,
    Activity
} from "lucide-react";
import Image from "next/image";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { motion, AnimatePresence } from "framer-motion";
import { VirtualStylist } from "@/components/fashion/VirtualStylist";
import { Product } from "@/lib/seed";

export default function ProductStory({ initialProduct }: { initialProduct: Product }) {
    const { addItem } = useCartStore();
    const [isLiked, setIsLiked] = useState(false);
    const [selectedSize, setSelectedSize] = useState("M");

    const sizes = ["XS", "S", "M", "L", "XL"];

    return (
        <div className="bg-black text-white min-h-screen pb-24" dir="rtl">
            {/* === NAVIGATION / HEADER SPACE === */}
            <div className="h-24" />

            {/* === MAIN BENTO PRODUCT LAYOUT === */}
            <div className="max-w-7xl mx-auto px-6 grid grid-cols-1 lg:grid-cols-12 gap-8">

                {/* 1. PRODUCT VISUALS (7 cols) */}
                <div className="lg:col-span-7 space-y-6">
                    {/* Main Holographic Image */}
                    <div className="relative aspect-[4/5] bg-zinc-900 rounded-3xl overflow-hidden border border-zinc-800 group">
                        <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,_var(--color-neon-lime)_0%,_transparent_70%)] opacity-0 group-hover:opacity-20 transition-opacity duration-1000" />
                        <Image
                            src={initialProduct.image}
                            alt={initialProduct.name}
                            fill
                            className="object-cover grayscale hover:grayscale-0 transition-all duration-700"
                            priority
                        />

                        {/* 2026 UI Overlays */}
                        <div className="absolute top-8 left-8 flex flex-col gap-2">
                            <div className="px-3 py-1 bg-black/50 backdrop-blur-xl border border-white/10 text-[10px] font-mono tracking-widest uppercase">
                                SKU: {initialProduct.id}
                            </div>
                            <div className="px-3 py-1 bg-accent text-black text-[10px] font-black tracking-widest uppercase">
                                LIMITED RELEASE
                            </div>
                        </div>

                        {/* Interactive Scan Line Effect */}
                        <div className="absolute inset-0 pointer-events-none overflow-hidden">
                            <div className="w-full h-[1px] bg-accent/30 absolute shadow-[0_0_15px_var(--color-neon-lime)] animate-scan-line" />
                        </div>

                        {/* AR VIRTUAL TRY-ON CTA */}
                        <div className="absolute bottom-8 right-8 left-8">
                            <button className="w-full py-4 bg-white/10 backdrop-blur-md border border-white/20 rounded-2xl flex items-center justify-center gap-3 text-xs font-black uppercase tracking-[0.2em] hover:bg-accent hover:text-black transition-all group/ar">
                                <Glasses className="w-5 h-5 group-hover/ar:animate-bounce" />
                                Virtual AR Try-on (Alpha)
                            </button>
                        </div>
                    </div>

                    {/* Thumbnail Grid / Detail Views */}
                    <div className="grid grid-cols-3 gap-4">
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="aspect-square bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden opacity-50 hover:opacity-100 transition-opacity cursor-crosshair">
                                <Image src={initialProduct.image} alt="Detail" fill className="object-cover scale-150 rotate-12" />
                            </div>
                        ))}
                    </div>
                </div>

                {/* 2. PRODUCT INTEL (5 cols) */}
                <div className="lg:col-span-5 space-y-8">
                    <div className="sticky top-32 space-y-8">

                        {/* Title & Brand */}
                        <div className="space-y-2">
                            <div className="flex items-center gap-2 text-accent text-[10px] font-mono font-black uppercase tracking-[0.2em]">
                                <Zap className="w-3 h-3 fill-current" />
                                CELIA CORE // SERIES 01
                            </div>
                            <h1 className="text-4xl md:text-6xl font-black tracking-tighter uppercase leading-none italic">
                                {initialProduct.name}
                            </h1>
                            <div className="flex items-center gap-4 pt-2">
                                <div className="flex text-accent">
                                    {[...Array(5)].map((_, i) => (
                                        <Star key={i} className={cn("w-3 h-3", i < Math.floor(Number(initialProduct.rating || 0)) ? "fill-accent" : "text-zinc-800")} />
                                    ))}
                                </div>
                                <span className="text-[10px] font-mono text-zinc-500 uppercase">{initialProduct.rating} / 5.0 (GLOBAL INDEX)</span>
                            </div>
                        </div>

                        {/* Description */}
                        <div className="space-y-4">
                            <p className="text-zinc-400 text-sm leading-relaxed max-w-md">
                                {initialProduct.description}
                            </p>
                            <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-2xl">
                                <p className="text-[10px] text-zinc-500 font-mono uppercase mb-2">AI Summary</p>
                                <p className="text-xs text-accent italic">"{initialProduct.ai_summary}"</p>
                            </div>
                        </div>

                        {/* Customization (Sizes) */}
                        <div className="space-y-4">
                            <div className="flex justify-between items-center text-[10px] font-mono uppercase tracking-widest text-zinc-500">
                                <span>اختر المقاس</span>
                                <span className="text-accent underline cursor-pointer">جدول القياسات</span>
                            </div>
                            <div className="flex flex-wrap gap-2">
                                {sizes.map((size) => (
                                    <button
                                        key={size}
                                        onClick={() => setSelectedSize(size)}
                                        className={cn(
                                            "w-12 h-12 rounded-xl border font-mono text-xs transition-all",
                                            selectedSize === size
                                                ? "bg-accent border-accent text-black font-black scale-110"
                                                : "bg-black border-zinc-800 text-zinc-500 hover:border-zinc-600"
                                        )}
                                    >
                                        {size}
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Price & Action */}
                        <div className="space-y-6 pt-6 border-t border-zinc-800">
                            <div className="flex flex-col gap-1">
                                <div className="flex items-end gap-2">
                                    <span className="text-4xl font-black">{(initialProduct.price * 50).toLocaleString()}</span>
                                    <span className="text-xs font-mono text-zinc-500 pb-1 uppercase">EGP // VAT INCL.</span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <div className="px-2 py-0.5 bg-green-500/10 border border-green-500/20 rounded text-[8px] font-mono text-green-500 uppercase flex items-center gap-1">
                                        <Activity className="w-3 h-3" />
                                        AI Optimized Price (-5%)
                                    </div>
                                    <span className="text-[8px] font-mono text-zinc-600 uppercase">Demand-linked Pricing Enabled</span>
                                </div>
                            </div>

                            <div className="flex gap-4">
                                <button
                                    onClick={() => addItem(initialProduct)}
                                    className="flex-1 h-16 bg-white text-black font-black uppercase text-xs tracking-[0.2em] rounded-2xl hover:bg-accent transition-all flex items-center justify-center gap-3 active:scale-95"
                                >
                                    إضافة إلى الحقيبة <ShoppingBag className="w-5 h-5 -rotate-12" />
                                </button>
                                <button
                                    onClick={() => setIsLiked(!isLiked)}
                                    className={cn(
                                        "w-16 h-16 rounded-2xl border border-zinc-800 flex items-center justify-center transition-all group",
                                        isLiked ? "bg-red-500/10 border-red-500 text-red-500" : "hover:border-zinc-400"
                                    )}
                                >
                                    <Heart className={cn("w-6 h-6", isLiked && "fill-current")} />
                                </button>
                            </div>
                        </div>

                        {/* Virtual Stylist Integration */}
                        <div className="pt-8">
                            <VirtualStylist productName={initialProduct.name} category={initialProduct.category} />
                        </div>

                        {/* Trust Protocol */}
                        <div className="grid grid-cols-2 gap-4">
                            <div className="flex items-center gap-3 p-4 bg-zinc-900/30 rounded-2xl border border-zinc-900">
                                <Truck className="w-4 h-4 text-accent" />
                                <div className="text-[10px] uppercase font-mono">
                                    <p className="text-white">Alpha Speed</p>
                                    <p className="text-zinc-500">24H Delivery</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-3 p-4 bg-zinc-900/30 rounded-2xl border border-zinc-900">
                                <Box className="w-4 h-4 text-accent" />
                                <div className="text-[10px] uppercase font-mono">
                                    <p className="text-white">Eco-Box</p>
                                    <p className="text-zinc-500">Zero Plastic</p>
                                </div>
                            </div>
                        </div>

                    </div>
                </div>
            </div>

            {/* === RELATED PRODUCTS / BENTO GRID === */}
            <section className="mt-32 border-t border-zinc-900 pt-24 px-6 overflow-hidden">
                <div className="max-w-7xl mx-auto space-y-12">
                    <h2 className="text-4xl font-black italic uppercase tracking-tighter">You Might Also <span className="text-accent">Sync </span>With</h2>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        {[1, 2, 3, 4].map(i => (
                            <div key={i} className="aspect-[2/3] bg-zinc-900 rounded-3xl border border-zinc-800 p-8 flex flex-col justify-end gap-4 group cursor-pointer overflow-hidden relative">
                                <div className="absolute inset-0 bg-gradient-to-t from-black to-transparent z-10" />
                                <Image src={initialProduct.image} alt="Related" fill className="object-cover grayscale group-hover:grayscale-0 transition-all" />
                                <div className="relative z-20 space-y-1 translate-y-4 group-hover:translate-y-0 transition-transform">
                                    <p className="text-[10px] font-mono text-accent">ACCESSORY // 00{i}</p>
                                    <h4 className="font-bold uppercase italic text-sm">NEO-SHELL V.0{i}</h4>
                                    <ArrowRight className="w-4 h-4 -rotate-45 text-white" />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </section>
        </div>
    );
}
