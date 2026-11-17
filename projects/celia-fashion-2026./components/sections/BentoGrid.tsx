import { Product, products } from "@/lib/data";
import { cn } from "@/lib/utils";
import Image from "next/image";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

export function BentoGrid() {
    return (
        <section className="py-24 px-6 max-w-7xl mx-auto">
            <div className="flex items-end justify-between mb-12">
                <div>
                    <h2 className="text-4xl font-bold tracking-tighter mb-2">LATEST DROPS</h2>
                    <p className="text-muted-foreground uppercase tracking-widest text-sm">Collection 001 // Year 2026</p>
                </div>
                <Link href="/shop" className="hidden md:flex items-center gap-2 hover:text-[var(--accent)] transition-colors">
                    View All <ArrowUpRight className="w-4 h-4" />
                </Link>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 auto-rows-[300px]">
                {products.map((product) => (
                    <div
                        key={product.id}
                        className={cn(
                            "group relative overflow-hidden bg-muted/20 border border-white/5",
                            product.className
                        )}
                    >
                        {/* Image */}
                        <div className="absolute inset-0 transition-transform duration-700 group-hover:scale-105">
                            <Image
                                src={product.image}
                                alt={product.name}
                                fill
                                className="object-cover opacity-80 group-hover:opacity-100 transition-opacity"
                            />
                        </div>

                        {/* Overlay Gradient */}
                        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-60" />

                        {/* Content */}
                        <div className="absolute bottom-0 left-0 right-0 p-6 flex items-end justify-between text-white">
                            <div>
                                <span className="text-xs font-mono text-[var(--accent)] mb-1 block">{product.category}</span>
                                <h3 className="text-xl font-bold uppercase tracking-tight">{product.name}</h3>
                                <p className="text-sm text-gray-300 mt-1">${product.price}</p>
                            </div>
                            <button className="bg-white/10 backdrop-blur-md p-3 rounded-full opacity-0 group-hover:opacity-100 transition-all hover:bg-[var(--accent)] hover:text-black">
                                <ArrowUpRight className="w-5 h-5" />
                            </button>
                        </div>
                    </div>
                ))}
            </div>
        </section>
    );
}
