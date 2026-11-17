"use client";

import { useState, useEffect } from "react";
import { Product } from "@/lib/seed";
import { getProducts } from "@/lib/actions";
import Link from "next/link";
import Image from "next/image";
import { Star, ArrowLeft, Sparkles, TrendingUp, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

// Map URL categories to Seed Data categories
const CATEGORY_MAP: Record<string, string> = {
    "women": "فساتين",
    "men": "فساتين", // Updated to match seed logic if needed
    "accessories": "إكسسوارات"
};

const ARABIC_TITLES: Record<string, string> = {
    "women": "قسم النساء",
    "men": "قسم الرجال",
    "accessories": "الإكسسوارات",
    "shop": "المتجر الكامل"
};

export default function CategoryView({ category = "shop" }: { category?: string }) {
    const [products, setProducts] = useState<Product[]>([]);
    const [loading, setLoading] = useState(true);
    const [hoveredId, setHoveredId] = useState<string | null>(null);

    useEffect(() => {
        async function load() {
            setLoading(true);
            const allProducts = await getProducts();
            const isAll = category === "all" || category === "shop";
            const mappedCat = CATEGORY_MAP[category] || "";

            const filtered = isAll
                ? allProducts
                : allProducts.filter(p => p.category === mappedCat || p.category.includes(mappedCat));

            setProducts(filtered.length > 0 ? filtered : allProducts.slice(0, 8));
            setLoading(false);
        }
        load();
    }, [category]);

    if (loading) return (
        <div className="min-h-screen bg-[#0a0a0f] flex items-center justify-center">
            <RefreshCw className="w-10 h-10 text-accent animate-spin" />
        </div>
    );

    return (
        <div className="min-h-screen bg-black text-white pt-32 pb-24 px-6 perspective-container" dir="rtl" style={{ perspective: '2000px' }}>
            {/* 3D Animated Background Grid */}
            <div className="fixed inset-0 opacity-10 pointer-events-none" style={{ transform: 'translateZ(-100px)' }}>
                <div className="absolute inset-0" style={{
                    backgroundImage: 'linear-gradient(#15f4ee 1px, transparent 1px), linear-gradient(90deg, #15f4ee 1px, transparent 1px)',
                    backgroundSize: '50px 50px'
                }}></div>
            </div>

            {/* Header with Neon Effect */}
            <div className="max-w-7xl mx-auto mb-16 relative">
                <div className="text-center space-y-6">
                    <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-accent/10 border border-accent/30 backdrop-blur-sm">
                        <Sparkles className="w-4 h-4 text-accent" />
                        <span className="text-accent text-sm font-black tracking-[0.2em]">COLLECTION 2026</span>
                    </div>

                    <h1 className="text-7xl font-black tracking-tighter uppercase italic">
                        {ARABIC_TITLES[category] || "المتجر"}
                    </h1>

                    <p className="text-gray-400 font-mono text-xs tracking-widest uppercase">
                        Digital-Physical Fusion // Series 01 // Optimal Quality
                    </p>

                    <div className="flex items-center justify-center gap-8 text-[10px] font-mono border-t border-b border-white/5 py-4 max-w-md mx-auto">
                        <div className="flex items-center gap-2">
                            <TrendingUp className="w-4 h-4 text-accent" />
                            <span>{products.length} PRODUCTS</span>
                        </div>
                        <div className="w-px h-2 bg-zinc-800" />
                        <div>LIVE INDEX</div>
                        <div className="w-px h-2 bg-zinc-800" />
                        <div className="text-accent">ALPHA STATUS</div>
                    </div>
                </div>
            </div>

            {/* Products Grid - 3D Bento Style */}
            <div className="max-w-7xl mx-auto" style={{ transformStyle: 'preserve-3d' }}>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                    {products.map((product, idx) => (
                        <Link
                            key={product.id}
                            href={`/product/${product.id}`}
                            onMouseEnter={() => setHoveredId(product.id)}
                            onMouseLeave={() => setHoveredId(null)}
                            className={cn(
                                "group relative overflow-hidden rounded-3xl transition-all duration-500",
                                "bg-zinc-950 border border-white/5 hover:border-accent",
                                hoveredId === product.id && "shadow-2xl shadow-accent/20"
                            )}
                            style={{
                                transformStyle: 'preserve-3d',
                                transform: hoveredId === product.id
                                    ? 'translateZ(30px) rotateX(2deg) rotateY(-2deg)'
                                    : 'translateZ(0px)'
                            }}
                        >
                            {/* 3D Image Container */}
                            <div className="relative aspect-[3/4] overflow-hidden" style={{ transform: 'translateZ(10px)' }}>
                                <Image
                                    src={product.image}
                                    alt={product.name}
                                    fill
                                    className={cn(
                                        "object-cover transition-all duration-700",
                                        hoveredId === product.id ? "scale-110 grayscale-0" : "scale-100 grayscale"
                                    )}
                                />

                                {/* Overlay */}
                                <div className="absolute inset-0 bg-gradient-to-t from-black via-transparent to-transparent opacity-60"></div>

                                {/* Category Badge */}
                                <div className="absolute top-4 right-4 px-3 py-1 bg-black/50 backdrop-blur-xl border border-white/10 rounded-full">
                                    <span className="text-white text-[10px] font-mono tracking-tighter uppercase">{product.category}</span>
                                </div>
                            </div>

                            {/* Content */}
                            <div className="p-6 space-y-4" style={{ transform: 'translateZ(15px)' }}>
                                <h3 className="text-lg font-black uppercase italic group-hover:text-accent transition-colors">
                                    {product.name}
                                </h3>

                                <div className="flex items-center justify-between">
                                    <span className="text-xl font-black text-white">
                                        {(product.price * 50).toLocaleString()} <span className="text-[10px] font-mono text-zinc-500">EGP</span>
                                    </span>
                                    <div className="flex items-center gap-1 text-[10px] font-mono text-accent">
                                        <Star className="w-3 h-3 fill-current" />
                                        {product.rating}
                                    </div>
                                </div>

                                <div className="h-[1px] w-full bg-zinc-900 group-hover:bg-accent/30 transition-colors" />

                                <div className="flex items-center justify-between text-[10px] font-mono text-zinc-600 uppercase">
                                    <span>In Stock: {product.stock}</span>
                                    <ArrowLeft className="w-4 h-4 opacity-0 group-hover:opacity-100 -rotate-180 transition-all group-hover:translate-x-[-4px]" />
                                </div>
                            </div>
                        </Link>
                    ))}
                </div>
            </div>
        </div>
    );
}