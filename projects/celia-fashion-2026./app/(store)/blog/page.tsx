"use client";

import { useEffect, useState } from "react";
import { BlogPost } from "@/lib/seed";
import { getBlogPosts } from "@/lib/actions";
import Image from "next/image";
import Link from "next/link";
import { Clock, TrendingUp, Sparkles, ArrowUpRight } from "lucide-react";

export default function BlogPage() {
    const [posts, setPosts] = useState<BlogPost[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            const data = await getBlogPosts();
            setPosts(data);
            setLoading(false);
        }
        load();
    }, []);

    if (loading || posts.length === 0) return (
        <div className="min-h-screen bg-[#0a0a0f] flex items-center justify-center">
            <div className="w-12 h-12 border-4 border-accent border-t-transparent animate-spin rounded-full" />
        </div>
    );

    return (
        <div className="min-h-screen bg-gradient-to-br from-[#0a0a0f] via-[#13131a] to-[#0a0a0f] text-white pt-24 pb-24 px-6 relative overflow-hidden" style={{ perspective: '2000px', transformStyle: 'preserve-3d' }}>
            {/* 3D Animated Grid Background */}
            <div className="fixed inset-0 opacity-10 pointer-events-none" style={{ transform: 'translateZ(-200px)' }}>
                <div className="absolute inset-0" style={{
                    backgroundImage: 'linear-gradient(#15f4ee 1px, transparent 1px), linear-gradient(90deg, #15f4ee 1px, transparent 1px)',
                    backgroundSize: '60px 60px',
                    animation: 'gridMove 20s linear infinite'
                }}></div>
            </div>

            {/* 3D Glowing Orbs */}
            <div className="fixed top-20 left-20 w-96 h-96 bg-[#15f4ee]/20 rounded-full blur-[100px] animate-pulse float-3d" style={{ transform: 'translateZ(50px)' }}></div>
            <div className="fixed bottom-20 right-20 w-96 h-96 bg-[#3B00DB]/20 rounded-full blur-[100px] animate-pulse float-3d" style={{ animationDelay: '1s', transform: 'translateZ(30px)' }}></div>

            <div className="max-w-7xl mx-auto relative z-10">
                {/* Header */}
                <div className="mb-20 text-center space-y-8">
                    {/* Badge */}
                    <div className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full bg-[#15f4ee]/10 border border-[#15f4ee]/30 backdrop-blur-md">
                        <Sparkles className="w-4 h-4 text-[#15f4ee] animate-pulse" />
                        <span className="text-[#15f4ee] text-sm font-bold tracking-widest">FUTURE INTELLIGENCE 2026</span>
                    </div>

                    {/* Main Title with 3D Glitch Effect */}
                    <div className="relative" style={{ transformStyle: 'preserve-3d' }}>
                        <h1 className="text-8xl md:text-9xl font-black tracking-tighter leading-none text-3d" style={{ transform: 'translateZ(80px)' }}>
                            <span className="bg-gradient-to-r from-white via-[#15f4ee] to-white bg-clip-text text-transparent">
                                FASHION
                            </span>
                            <br />
                            <span className="bg-gradient-to-r from-[#66ff00] via-[#15f4ee] to-[#66ff00] bg-clip-text text-transparent">
                                INTEL
                            </span>
                        </h1>

                        {/* Neon Underline */}
                        <div className="absolute -bottom-4 left-1/2 -translate-x-1/2 w-64 h-1 bg-gradient-to-r from-transparent via-[#15f4ee] to-transparent blur-sm"></div>
                    </div>

                    {/* Subtitle */}
                    <div className="flex items-center justify-center gap-4 text-lg">
                        <span className="text-gray-400 tracking-[0.3em] uppercase">ANALYSIS</span>
                        <div className="w-2 h-2 rounded-full bg-[#15f4ee] animate-pulse"></div>
                        <span className="text-gray-400 tracking-[0.3em] uppercase">TRENDS</span>
                        <div className="w-2 h-2 rounded-full bg-[#66ff00] animate-pulse"></div>
                        <span className="text-gray-400 tracking-[0.3em] uppercase">INSIGHTS</span>
                    </div>
                </div>

                {/* Featured Post - 3D Large Bento */}
                <div className="mb-12 group">
                    <Link href={`/blog/${posts[0].id}`}>
                        <div className="relative rounded-3xl overflow-hidden bg-gradient-to-br from-white/5 to-white/[0.02] backdrop-blur-sm border border-white/10 hover:border-[#15f4ee]/50 transition-all duration-500 card-3d shadow-3d-hover"
                            style={{
                                transformStyle: 'preserve-3d',
                                transform: 'translateZ(0px)'
                            }}>
                            <div className="grid md:grid-cols-2 gap-0">
                                {/* Image Side */}
                                <div className="relative aspect-[4/3] md:aspect-auto overflow-hidden">
                                    <Image
                                        src={posts[0].image}
                                        alt={posts[0].title}
                                        fill
                                        className="object-cover group-hover:scale-110 transition-transform duration-700"
                                    />
                                    <div className="absolute inset-0 bg-gradient-to-r from-black/60 to-transparent"></div>

                                    {/* Featured Badge */}
                                    <div className="absolute top-6 left-6 px-4 py-2 rounded-full bg-[#66ff00] backdrop-blur-sm">
                                        <span className="text-black text-xs font-black tracking-wider">FEATURED</span>
                                    </div>
                                </div>

                                {/* 3D Content Side */}
                                <div className="p-10 flex flex-col justify-center space-y-6" style={{ transform: 'translateZ(30px)' }}>
                                    {/* Meta */}
                                    <div className="flex items-center gap-4 text-sm">
                                        <div className="flex items-center gap-2 text-[#15f4ee]">
                                            <Clock className="w-4 h-4" />
                                            <span className="tracking-wider">{posts[0].readTime}</span>
                                        </div>
                                        <div className="w-1 h-1 rounded-full bg-gray-600"></div>
                                        <span className="text-gray-400 tracking-wider">{posts[0].date}</span>
                                    </div>

                                    {/* Title */}
                                    <h2 className="text-4xl font-black leading-tight group-hover:text-[#15f4ee] transition-colors">
                                        {posts[0].title}
                                    </h2>

                                    {/* Excerpt */}
                                    <p className="text-gray-400 text-lg leading-relaxed">
                                        {posts[0].excerpt}
                                    </p>

                                    {/* Read More */}
                                    <div className="flex items-center gap-2 text-[#15f4ee] font-bold group-hover:gap-4 transition-all">
                                        <span>READ ARTICLE</span>
                                        <ArrowUpRight className="w-5 h-5" />
                                    </div>
                                </div>
                            </div>
                        </div>
                    </Link>
                </div>

                {/* Regular Posts 3D Grid */}
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6" style={{ transformStyle: 'preserve-3d' }}>
                    {posts.slice(1).map((post, i) => (
                        <Link
                            key={post.id}
                            href={`/blog/${post.id}`}
                            className="group relative card-3d"
                        >
                            <div className="blog-card-3d relative rounded-2xl overflow-hidden bg-gradient-to-br from-white/5 to-white/[0.02] backdrop-blur-sm border border-white/10 hover:border-[#15f4ee]/50 transition-all duration-500 h-full flex flex-col shadow-3d-hover"
                                style={{ transformStyle: 'preserve-3d', transform: 'translateZ(0px)' }}>
                                {/* Neon Corner */}
                                <div className="absolute top-0 right-0 w-20 h-20 bg-gradient-to-br from-[#15f4ee]/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>

                                {/* Image */}
                                <div className="relative aspect-video overflow-hidden">
                                    <Image
                                        src={post.image}
                                        alt={post.title}
                                        fill
                                        className="object-cover group-hover:scale-110 transition-transform duration-700 brightness-90 group-hover:brightness-100"
                                    />
                                    <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent"></div>

                                    {/* Category Badge */}
                                    <div className="absolute top-4 right-4 px-3 py-1 rounded-full bg-black/60 backdrop-blur-md border border-white/10">
                                        <span className="text-[#15f4ee] text-xs tracking-wider">ARTICLE</span>
                                    </div>
                                </div>

                                {/* Content */}
                                <div className="p-6 flex-1 flex flex-col space-y-4">
                                    {/* Meta */}
                                    <div className="flex items-center gap-3 text-xs">
                                        <div className="flex items-center gap-1.5 text-[#66ff00]">
                                            <Clock className="w-3.5 h-3.5" />
                                            <span className="tracking-wider">{post.readTime}</span>
                                        </div>
                                        <div className="w-1 h-1 rounded-full bg-gray-600"></div>
                                        <span className="text-gray-500 tracking-wider">{post.date}</span>
                                    </div>

                                    {/* Title */}
                                    <h3 className="text-xl font-bold leading-tight group-hover:text-[#15f4ee] transition-colors line-clamp-2">
                                        {post.title}
                                    </h3>

                                    {/* Excerpt */}
                                    <p className="text-gray-400 text-sm leading-relaxed line-clamp-3 flex-1">
                                        {post.excerpt}
                                    </p>

                                    {/* Bottom Bar */}
                                    <div className="pt-4 border-t border-white/10 flex items-center justify-between">
                                        <span className="text-[#15f4ee] text-sm font-bold tracking-wider">READ MORE</span>
                                        <ArrowUpRight className="w-4 h-4 text-[#15f4ee] group-hover:translate-x-1 group-hover:-translate-y-1 transition-transform" />
                                    </div>
                                </div>

                                {/* Hover Glow */}
                                <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-[#15f4ee] via-[#2454FF] to-[#66ff00] opacity-0 group-hover:opacity-100 transition-opacity"></div>
                            </div>
                        </Link>
                    ))}
                </div>

                {/* Bottom CTA */}
                <div className="mt-20 text-center">
                    <button className="group px-10 py-5 rounded-full bg-gradient-to-r from-[#15f4ee] to-[#2454FF] text-white font-black tracking-widest shadow-2xl shadow-[#15f4ee]/30 hover:shadow-[#15f4ee]/50 transition-all duration-300 hover:scale-105 relative overflow-hidden">
                        <span className="relative z-10 flex items-center gap-3">
                            EXPLORE MORE INSIGHTS
                            <TrendingUp className="w-5 h-5 group-hover:translate-y-[-2px] transition-transform" />
                        </span>
                        <div className="absolute inset-0 bg-gradient-to-r from-[#66ff00] to-[#15f4ee] opacity-0 group-hover:opacity-100 transition-opacity"></div>
                    </button>
                </div>
            </div>

            <style jsx>{`
                @keyframes gridMove {
                    0% { transform: translate(0, 0); }
                    100% { transform: translate(60px, 60px); }
                }
            `}</style>
        </div>
    );
}