"use client";

import Link from "next/link";
import { Search, ShoppingBag, Menu } from "lucide-react";
import { useCartStore } from "@/store/cart";
import { useEffect, useState } from "react";

export function Navbar() {
    const { items, toggleCart } = useCartStore();
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    return (
        <nav className="fixed top-0 left-0 right-0 z-50 bg-white/80 backdrop-blur-md border-b border-zinc-100">
            <div className="flex items-center justify-between px-6 py-6 max-w-7xl mx-auto">
                {/* Logo */}
                <Link href="/" className="text-3xl font-bold tracking-tight text-zinc-900 group">
                    <span className="font-heading italic font-black">Celia</span>
                    <span className="text-sm font-light tracking-widest ml-1 text-[var(--accent)] group-hover:text-pink-400 transition-colors">FASHION</span>
                </Link>

                {/* Desktop Nav - Arabic */}
                <div className="hidden md:flex items-center gap-8 text-sm font-bold tracking-wide text-zinc-600" dir="rtl">
                    <Link href="/shop" className="hover:text-[var(--accent)] transition-colors">وصل حديثاً</Link>
                    <Link href="/women" className="hover:text-[var(--accent)] transition-colors">نسائي</Link>
                    <Link href="/men" className="hover:text-[var(--accent)] transition-colors">رجالي</Link>
                    <Link href="/accessories" className="hover:text-[var(--accent)] transition-colors">إكسسوارات</Link>
                    <Link href="/about" className="hover:text-[var(--accent)] transition-colors">عن المتجر</Link>
                </div>

                {/* Icons */}
                <div className="flex items-center gap-6">
                    <button className="hover:text-[var(--accent)] transition-colors">
                        <Search className="w-5 h-5" />
                    </button>
                    <button
                        onClick={toggleCart}
                        className="flex items-center gap-2 hover:text-[var(--accent)] transition-colors"
                    >
                        <ShoppingBag className="w-5 h-5" />
                        <span className="text-xs font-mono hidden sm:inline-block">
                            [{mounted ? items.length : 0}]
                        </span>
                    </button>
                    <button className="md:hidden">
                        <Menu className="w-5 h-5" />
                    </button>
                </div>
            </div>
        </nav>
    );
}
