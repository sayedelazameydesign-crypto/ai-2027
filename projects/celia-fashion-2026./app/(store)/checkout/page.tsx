"use client";

import { useCartStore } from "@/store/cart";
import { CheckCircle, CreditCard, ShoppingBag, Truck, Banknote, ShieldCheck, ArrowRight } from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { cn } from "@/lib/utils";

export default function CheckoutPage() {
    const { items, clearCart } = useCartStore();
    const router = useRouter();
    const [isProcessing, setIsProcessing] = useState(false);
    const [paymentMethod, setPaymentMethod] = useState<'cod' | 'card'>('cod');

    const total = items.reduce((acc, item) => acc + item.price * item.quantity, 0);
    const shipping = 50; // EGP
    const finalTotal = (total * 50) + shipping;

    const handlePayment = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsProcessing(true);
        await new Promise(resolve => setTimeout(resolve, 2000));
        clearCart();
        router.push("/checkout/success");
    };

    if (items.length === 0) {
        return (
            <div className="min-h-screen flex flex-col items-center justify-center p-6 text-center bg-black text-white">
                <ShoppingBag className="w-16 h-16 text-zinc-800 mb-6 animate-bounce" />
                <h1 className="text-4xl font-black tracking-tighter mb-4">YOUR BAG IS EMPTY</h1>
                <p className="text-zinc-500 mb-8 max-w-sm">The future is waiting for you. Go back and pick some pieces.</p>
                <button onClick={() => router.push("/shop")} className="px-10 py-4 bg-accent text-black font-bold uppercase text-xs tracking-widest hover:bg-white transition-colors">Start Shopping</button>
            </div>
        )
    }

    return (
        <div className="pt-32 pb-24 px-6 max-w-7xl mx-auto min-h-screen bg-black text-white" dir="rtl">
            <header className="mb-16 text-right">
                <h1 className="text-5xl font-black tracking-tighter uppercase mb-4">Secure Gateway</h1>
                <div className="flex items-center gap-2 text-xs font-mono text-accent">
                    <ShieldCheck className="w-4 h-4" />
                    <span>PROTOCOL: SSL-2026-CHROME-V2</span>
                </div>
            </header>

            <div className="grid lg:grid-cols-12 gap-12">
                {/* Left: Forms */}
                <div className="lg:col-span-8 space-y-12">

                    {/* Shipping */}
                    <section className="bg-zinc-900/50 p-8 rounded-3xl border border-zinc-800">
                        <h2 className="text-xl font-bold mb-8 flex items-center gap-3">
                            <Truck className="w-5 h-5 text-accent" />
                            تفاصيل الشحن
                        </h2>
                        <form id="checkout-form" onSubmit={handlePayment} className="grid md:grid-cols-2 gap-8">
                            <div className="space-y-2">
                                <label className="text-[10px] uppercase font-bold text-zinc-500 font-mono">البريد الإلكتروني</label>
                                <input required type="email" className="w-full bg-black border border-zinc-800 p-4 text-sm focus:border-accent outline-none transition-colors rounded-xl" placeholder="you@2026.com" />
                            </div>
                            <div className="space-y-2">
                                <label className="text-[10px] uppercase font-bold text-zinc-500 font-mono">الاسم الكامل</label>
                                <input required type="text" className="w-full bg-black border border-zinc-800 p-4 text-sm focus:border-accent outline-none transition-colors rounded-xl" placeholder="Alexander Nova" />
                            </div>
                            <div className="md:col-span-2 space-y-2">
                                <label className="text-[10px] uppercase font-bold text-zinc-500 font-mono">العنوان بالتفصيل</label>
                                <input required type="text" className="w-full bg-black border border-zinc-800 p-4 text-sm focus:border-accent outline-none transition-colors rounded-xl" placeholder="المنطقة، الشارع، المبنى" />
                            </div>
                            <div className="space-y-2">
                                <label className="text-[10px] uppercase font-bold text-zinc-500 font-mono">المدينة</label>
                                <select className="w-full bg-black border border-zinc-800 p-4 text-sm focus:border-accent outline-none transition-colors rounded-xl appearance-none">
                                    <option>القاهرة</option>
                                    <option>الجيزة</option>
                                    <option>الإسكندرية</option>
                                    <option>محافظات أخرى</option>
                                </select>
                            </div>
                            <div className="space-y-2">
                                <label className="text-[10px] uppercase font-bold text-zinc-500 font-mono">رقم الهاتف</label>
                                <input required type="tel" className="w-full bg-black border border-zinc-800 p-4 text-sm focus:border-accent outline-none transition-colors rounded-xl" placeholder="01XXXXXXXXX" />
                            </div>
                        </form>
                    </section>

                    {/* Payment */}
                    <section className="bg-zinc-900/50 p-8 rounded-3xl border border-zinc-800">
                        <h2 className="text-xl font-bold mb-8 flex items-center gap-3">
                            <CreditCard className="w-5 h-5 text-accent" />
                            طريقة الدفع
                        </h2>

                        <div className="grid md:grid-cols-2 gap-4 text-right">
                            {/* Cash on Delivery */}
                            <div
                                onClick={() => setPaymentMethod('cod')}
                                className={cn(
                                    "p-6 border rounded-2xl cursor-pointer transition-all flex items-center justify-between group",
                                    paymentMethod === 'cod' ? "border-accent bg-accent/5" : "border-zinc-800 hover:border-zinc-700"
                                )}
                            >
                                <div className="space-y-1">
                                    <h3 className="font-bold flex items-center gap-2">
                                        <Banknote className="w-4 h-4 text-accent" /> الدفع عند الاستلام
                                    </h3>
                                    <p className="text-xs text-zinc-500">ادفع فقط عند وصول المنتج إليك.</p>
                                </div>
                                <div className={cn("w-4 h-4 rounded-full border border-zinc-700", paymentMethod === 'cod' && "border-accent bg-accent")} />
                            </div>

                            {/* Credit Card */}
                            <div
                                onClick={() => setPaymentMethod('card')}
                                className={cn(
                                    "p-6 border rounded-2xl cursor-pointer transition-all flex items-center justify-between group",
                                    paymentMethod === 'card' ? "border-accent bg-accent/5" : "border-zinc-800 hover:border-zinc-700"
                                )}
                            >
                                <div className="space-y-1">
                                    <h3 className="font-bold flex items-center gap-2">
                                        <CreditCard className="w-4 h-4 text-accent" /> بطاقة ائتمان
                                    </h3>
                                    <p className="text-xs text-zinc-500">Stripe Test Mode (VISA/Mastercard)</p>
                                </div>
                                <div className={cn("w-4 h-4 rounded-full border border-zinc-700", paymentMethod === 'card' && "border-accent bg-accent")} />
                            </div>
                        </div>
                    </section>
                </div>

                {/* Right: Summary */}
                <div className="lg:col-span-4">
                    <div className="bg-zinc-900 border border-zinc-800 p-8 rounded-3xl sticky top-32 space-y-10">
                        <div>
                            <h3 className="text-2xl font-black mb-8 uppercase italic flex justify-between items-center">
                                ملخص الطلب
                                <span className="text-xs font-mono text-zinc-500">{items.length} قطع</span>
                            </h3>
                            <div className="space-y-6 max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                                {items.map((item) => (
                                    <div key={item.id} className="flex gap-4">
                                        <div className="relative w-16 h-16 bg-black rounded-lg overflow-hidden shrink-0 border border-zinc-800">
                                            <Image src={item.image} alt={item.name} fill className="object-cover grayscale" />
                                        </div>
                                        <div className="space-y-1">
                                            <p className="font-bold text-xs truncate w-32">{item.name}</p>
                                            <p className="text-[10px] text-zinc-500 uppercase font-mono">QTY: {item.quantity}</p>
                                            <p className="text-sm font-black text-accent">{(item.price * 50).toLocaleString()} EGP</p>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        <div className="space-y-4 border-t border-zinc-800 pt-8 font-mono text-xs uppercase tracking-widest">
                            <div className="flex justify-between text-zinc-500">
                                <span>المجموع الفرعي</span>
                                <span>{(total * 50).toLocaleString()} EGP</span>
                            </div>
                            <div className="flex justify-between text-zinc-500">
                                <span>الشحن (Alpha Speed)</span>
                                <span>{shipping} EGP</span>
                            </div>
                            <div className="flex justify-between text-2xl font-black pt-6 border-t border-zinc-800 mt-4 text-white">
                                <span>الإجمالي</span>
                                <span className="text-accent">{finalTotal.toLocaleString()} EGP</span>
                            </div>
                        </div>

                        <button
                            form="checkout-form"
                            disabled={isProcessing}
                            className="w-full py-5 bg-white text-black font-black uppercase text-sm tracking-[0.2em] hover:bg-accent transition-all disabled:opacity-50 flex items-center justify-center gap-3 rounded-2xl group"
                        >
                            {isProcessing ? "PROCESSING..." : (
                                <>
                                    تأكيد الطلب <ArrowRight className="w-5 h-5 -rotate-180 group-hover:translate-x-[-10px] transition-transform" />
                                </>
                            )}
                        </button>

                        <div className="text-center">
                            <span className="text-[10px] font-mono text-zinc-600 uppercase">End-to-End Encrypted Transaction</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
