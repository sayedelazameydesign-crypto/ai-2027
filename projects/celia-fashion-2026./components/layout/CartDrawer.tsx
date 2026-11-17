"use client"

import { useCartStore } from "@/store/cart";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import Image from "next/image";
import { Minus, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

export function CartDrawer() {
    const { items, isOpen, toggleCart, removeItem, updateQuantity } = useCartStore();
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted) return null;

    const total = items.reduce((acc, item) => acc + item.price * item.quantity, 0);

    return (
        <Sheet open={isOpen} onOpenChange={toggleCart}>
            <SheetContent className="w-full sm:max-w-md border-l border-white/10 bg-[#0a0a0a] text-white">
                <SheetHeader className="mb-8">
                    <SheetTitle className="text-2xl font-bold tracking-tighter uppercase">Your Cart ({items.length})</SheetTitle>
                </SheetHeader>

                <div className="flex-1 overflow-y-auto h-[60vh] space-y-6">
                    {items.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-muted-foreground space-y-4">
                            <p>Your cart is empty.</p>
                            <button onClick={toggleCart} className="text-[var(--accent)] underline">Continue Shopping</button>
                        </div>
                    ) : (
                        items.map((item) => (
                            <div key={item.id} className="flex gap-4">
                                <div className="relative w-20 h-20 bg-white/5 rounded-md overflow-hidden">
                                    <Image src={item.image} alt={item.name} fill className="object-cover" />
                                </div>
                                <div className="flex-1 space-y-1">
                                    <h4 className="font-bold text-sm uppercase">{item.name}</h4>
                                    <p className="text-xs text-muted-foreground">{item.category}</p>
                                    <p className="text-[var(--accent)] font-mono text-sm">${item.price}</p>
                                    <div className="flex items-center gap-3 mt-2">
                                        <button onClick={() => updateQuantity(item.id, Math.max(1, item.quantity - 1))} className="p-1 hover:bg-white/10 rounded">
                                            <Minus className="w-3 h-3" />
                                        </button>
                                        <span className="text-xs font-mono">{item.quantity}</span>
                                        <button onClick={() => updateQuantity(item.id, item.quantity + 1)} className="p-1 hover:bg-white/10 rounded">
                                            <Plus className="w-3 h-3" />
                                        </button>
                                        <button onClick={() => removeItem(item.id)} className="ml-auto text-red-500 hover:text-red-400">
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ))
                    )}
                </div>

                {items.length > 0 && (
                    <div className="border-t border-white/10 pt-6 mt-6 space-y-4">
                        <div className="flex justify-between items-center text-lg font-bold">
                            <span>TOTAL</span>
                            <span className="font-mono text-[var(--accent)]">${total}</span>
                        </div>
                        <button className="w-full py-4 bg-[var(--accent)] text-black font-bold uppercase hover:opacity-90 transition-opacity">
                            Checkout Now
                        </button>
                        <p className="text-xs text-center text-muted-foreground">Shipping & taxes calculated at checkout.</p>
                    </div>
                )}
            </SheetContent>
        </Sheet>
    );
}
