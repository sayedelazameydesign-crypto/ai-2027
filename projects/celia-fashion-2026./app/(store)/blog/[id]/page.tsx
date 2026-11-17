import { getBlogPosts } from "@/lib/actions";
import { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { Clock, ArrowLeft, Share2, Sparkles, Box } from "lucide-react";
import { notFound } from "next/navigation";
import { MarkdownRenderer } from "@/components/blog/MarkdownRenderer";

export const dynamic = 'force-dynamic';

type Props = {
    params: Promise<{ id: string }>
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
    const { id } = await params;
    const posts = await getBlogPosts();
    const post = posts.find(p => p.id === id);

    if (!post) return { title: "Article Not Found" };

    return {
        title: `${post.title} | CELIA Intel`,
        description: post.excerpt,
    };
}

export default async function BlogPostPage({ params }: Props) {
    const { id } = await params;
    const posts = await getBlogPosts();
    const post = posts.find(p => p.id === id);

    if (!post) {
        notFound();
    }

    const relatedPosts = posts.filter(p => p.id !== id).slice(0, 3);

    return (
        <div className="min-h-screen bg-white dark:bg-[#050505] text-zinc-900 dark:text-zinc-100 selection:bg-accent selection:text-black">
            {/* Header / Navigation Overlay */}
            <div className="sticky top-0 z-50 bg-white/80 dark:bg-black/80 backdrop-blur-md border-b border-zinc-200 dark:border-zinc-800 px-6 py-4">
                <div className="max-w-5xl mx-auto flex justify-between items-center">
                    <Link href="/blog" className="flex items-center gap-2 text-sm font-mono hover:text-accent transition-colors">
                        <ArrowLeft className="w-4 h-4" />
                        <span className="hidden md:inline">Back to Intel</span>
                        <span className="md:hidden">Back</span>
                    </Link>
                    <div className="flex items-center gap-4">
                        <button className="p-2 hover:text-accent transition-colors"><Share2 className="w-5 h-5" /></button>
                        <Link href="/shop" className="px-4 py-1.5 bg-zinc-900 dark:bg-white text-white dark:text-black text-[10px] font-black uppercase tracking-widest rounded-full">Shop Collection</Link>
                    </div>
                </div>
            </div>

            <div className="max-w-4xl mx-auto px-6 py-20" dir="rtl">
                {/* Meta Article Header */}
                <header className="space-y-10 mb-20 text-right">
                    <div className="flex justify-end gap-3 flex-wrap">
                        {post.tags.map((tag, i) => (
                            <span key={i} className="text-[10px] font-mono uppercase tracking-widest px-3 py-1 border border-zinc-200 dark:border-zinc-800 rounded-lg">
                                {tag}
                            </span>
                        ))}
                    </div>

                    <h1 className="text-4xl md:text-7xl font-heading font-black leading-[1.1] tracking-tighter">
                        {post.title}
                    </h1>

                    <div className="flex justify-end items-center gap-8 text-xs font-mono text-zinc-500 uppercase tracking-widest">
                        <div className="flex items-center gap-2">
                            <Clock className="w-4 h-4" />
                            <span>{post.readTime} Read</span>
                        </div>
                        <div>
                            <span>Published: {post.date}</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <Sparkles className="w-3 text-accent" />
                            <span>AI Verified</span>
                        </div>
                    </div>
                </header>

                {/* Hero Image / 3D Layout */}
                <div className="relative aspect-[21/9] rounded-3xl overflow-hidden mb-24 grayscale-0 hover:grayscale transition-all duration-700 shadow-2xl">
                    <Image
                        src={post.image}
                        alt={post.title}
                        fill
                        className="object-cover"
                    />
                </div>

                {/* Content with Markdown */}
                <article className="max-w-2xl mx-auto">
                    <MarkdownRenderer content={post.content} />

                    {/* Placeholder for AI Generated Interactive Elements */}
                    <div className="mt-16 p-8 bg-zinc-50 dark:bg-zinc-900/50 rounded-3xl border border-zinc-200 dark:border-zinc-800 space-y-6">
                        <h4 className="flex items-center gap-2 font-bold text-lg">
                            <Box className="w-5 h-5 text-accent" />
                            عنصر تفاعلي (AI Insight)
                        </h4>
                        <p className="text-sm text-zinc-500 italic leading-relaxed">
                            هذا القسم يتم توليده ديناميكياً بناءً على محتوى المقال لعرض نماذج ثلاثية الأبعاد أو استطلاعات رأي مدعومة بالذكاء الاصطناعي الخاص بـ CELIA.
                        </p>
                        <div className="h-40 bg-white dark:bg-black rounded-xl border border-dashed border-zinc-300 dark:border-zinc-700 flex flex-col items-center justify-center gap-4 text-zinc-400">
                            <div className="w-12 h-12 rounded-full border-4 border-accent/20 border-t-accent animate-spin" />
                            <span className="text-[10px] font-mono">CALCULATING 2026 TREND METRICS...</span>
                        </div>
                    </div>
                </article>

                {/* Footer of Article */}
                <footer className="mt-32 pt-12 border-t border-zinc-200 dark:border-zinc-800">
                    <h2 className="text-2xl font-black mb-12">اكتشاف المزيد</h2>
                    <div className="flex flex-col gap-12 text-right">
                        {relatedPosts.map((relatedPost) => (
                            <Link
                                key={relatedPost.id}
                                href={`/blog/${relatedPost.id}`}
                                className="group flex flex-col md:flex-row gap-8 items-start"
                            >
                                <div className="relative aspect-square w-full md:w-48 rounded-2xl overflow-hidden shrink-0">
                                    <Image
                                        src={relatedPost.image}
                                        alt={relatedPost.title}
                                        fill
                                        className="object-cover"
                                    />
                                </div>
                                <div className="space-y-4 pt-2 flex-1">
                                    <h3 className="text-2xl font-bold group-hover:text-accent transition-colors leading-tight">
                                        {relatedPost.title}
                                    </h3>
                                    <p className="text-zinc-500 text-sm line-clamp-2">{relatedPost.excerpt}</p>
                                    <span className="text-[10px] font-mono text-accent uppercase tracking-widest">اقرأ المقال الكامل ←</span>
                                </div>
                            </Link>
                        ))}
                    </div>
                </footer>
            </div>
        </div>
    );
}