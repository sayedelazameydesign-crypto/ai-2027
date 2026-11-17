"use client";

import React, { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
    MessageSquare,
    X,
    Send,
    Sparkles,
    User,
    Cpu,
    Image as ImageIcon,
    Camera,
    ShoppingBag,
    HelpCircle
} from "lucide-react";
import { cn } from "@/lib/utils";
import { generateStylingAdvice } from "@/lib/ai";

interface Message {
    id: string;
    text: string;
    sender: "user" | "ai";
    timestamp: Date;
    type?: "text" | "product-suggestion" | "size-advice";
}

export const AIChatAssistant = () => {
    const [isOpen, setIsOpen] = useState(false);
    const [input, setInput] = useState("");
    const [messages, setMessages] = useState<Message[]>([
        {
            id: "1",
            text: "أهلاً بك في CELIA 2026. أنا مساعدك الشخصي المدعوم بالذكاء الاصطناعي. كيف يمكنني تنسيق يومك اليوم؟",
            sender: "ai",
            timestamp: new Date()
        }
    ]);
    const [isTyping, setIsTyping] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages, isTyping]);

    const handleSend = async () => {
        if (!input.trim()) return;

        const userMsg: Message = {
            id: Date.now().toString(),
            text: input,
            sender: "user",
            timestamp: new Date()
        };

        setMessages(prev => [...prev, userMsg]);
        setInput("");
        setIsTyping(true);

        // Simple logic for Demo/MVP
        // In a real app, this would call an LLM (already prepared in lib/ai.ts)
        try {
            let aiResponseText = "";
            const lowerInput = input.toLowerCase();

            if (lowerInput.includes("مقاس") || lowerInput.includes("size")) {
                aiResponseText = "بناءً على مشترياتك السابقة ونموذجك ثلاثي الأبعاد، ننصحك بمقاس (L) لهذا النوع من الخامات المرنة.";
            } else if (lowerInput.includes("تنسيق") || lowerInput.includes("style")) {
                aiResponseText = await generateStylingAdvice("اختيارك الحالي", "أزياء راقية");
            } else {
                aiResponseText = "هذا اختيار رائع! هل تعلم أن هذه القطعة مصنوعة من ألياف ذكية تنظم درجة حرارة الجسم؟";
            }

            const aiMsg: Message = {
                id: (Date.now() + 1).toString(),
                text: aiResponseText,
                sender: "ai",
                timestamp: new Date()
            };

            setTimeout(() => {
                setMessages(prev => [...prev, aiMsg]);
                setIsTyping(false);
            }, 1000);
        } catch (error) {
            setIsTyping(false);
        }
    };

    return (
        <>
            {/* Floating Toggle Button */}
            <motion.button
                whileHover={{ scale: 1.1, rotate: 5 }}
                whileTap={{ scale: 0.9 }}
                onClick={() => setIsOpen(true)}
                className={cn(
                    "fixed bottom-8 right-8 z-[100] w-16 h-16 rounded-full bg-accent text-black flex items-center justify-center shadow-[0_0_30px_var(--color-neon-lime)] transition-opacity",
                    isOpen ? "opacity-0 pointer-events-none" : "opacity-100"
                )}
            >
                <MessageSquare className="w-8 h-8" />
                <span className="absolute -top-1 -left-1 flex h-4 w-4">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-4 w-4 bg-white/50"></span>
                </span>
            </motion.button>

            {/* Chat Window */}
            <AnimatePresence>
                {isOpen && (
                    <motion.div
                        initial={{ opacity: 0, y: 100, scale: 0.9 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: 100, scale: 0.9 }}
                        className="fixed bottom-8 right-8 z-[100] w-[400px] h-[600px] bg-zinc-950 border border-white/10 rounded-[2.5rem] shadow-3xl flex flex-col overflow-hidden"
                        dir="rtl"
                    >
                        {/* Header */}
                        <div className="p-6 bg-accent text-black flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <div className="w-10 h-10 rounded-full bg-black/10 flex items-center justify-center">
                                    <Cpu className="w-6 h-6" />
                                </div>
                                <div>
                                    <h3 className="font-black uppercase italic text-sm tracking-tighter">CELIA AI v4.0</h3>
                                    <p className="text-[10px] font-mono font-bold">OPERATIONAL // REALTIME</p>
                                </div>
                            </div>
                            <button onClick={() => setIsOpen(false)} className="hover:rotate-90 transition-transform">
                                <X className="w-6 h-6" />
                            </button>
                        </div>

                        {/* Messages Area */}
                        <div
                            ref={scrollRef}
                            className="flex-1 overflow-y-auto p-6 space-y-6 scrollbar-hide"
                        >
                            {messages.map((msg) => (
                                <motion.div
                                    initial={{ opacity: 0, x: msg.sender === "ai" ? 20 : -20 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    key={msg.id}
                                    className={cn(
                                        "flex gap-3",
                                        msg.sender === "user" ? "flex-row-reverse" : ""
                                    )}
                                >
                                    <div className={cn(
                                        "w-8 h-8 rounded-full flex items-center justify-center shrink-0",
                                        msg.sender === "ai" ? "bg-accent/20 text-accent" : "bg-white/10 text-white"
                                    )}>
                                        {msg.sender === "ai" ? <Sparkles className="w-4 h-4" /> : <User className="w-4 h-4" />}
                                    </div>
                                    <div className={cn(
                                        "max-w-[75%] p-4 rounded-2xl text-xs leading-relaxed",
                                        msg.sender === "ai"
                                            ? "bg-zinc-900 text-zinc-300 border border-white/5"
                                            : "bg-white text-black font-bold shadow-[0_0_15px_rgba(255,255,255,0.1)]"
                                    )}>
                                        {msg.text}
                                    </div>
                                </motion.div>
                            ))}
                            {isTyping && (
                                <div className="flex gap-3">
                                    <div className="w-8 h-8 rounded-full bg-accent/20 text-accent flex items-center justify-center">
                                        <Sparkles className="w-4 h-4 animate-spin" />
                                    </div>
                                    <div className="bg-zinc-900 p-4 rounded-2xl flex gap-1">
                                        <div className="w-1 h-1 bg-accent rounded-full animate-bounce" />
                                        <div className="w-1 h-1 bg-accent rounded-full animate-bounce [animation-delay:0.2s]" />
                                        <div className="w-1 h-1 bg-accent rounded-full animate-bounce [animation-delay:0.4s]" />
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* Quick Actions */}
                        <div className="px-6 py-2 flex gap-2 overflow-x-auto scrollbar-hide">
                            <button className="whitespace-nowrap px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-[10px] hover:bg-accent hover:text-black transition-colors">📦 تتبع طلبي</button>
                            <button className="whitespace-nowrap px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-[10px] hover:bg-accent hover:text-black transition-colors">📏 اقتراح مقاس</button>
                            <button className="whitespace-nowrap px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-[10px] hover:bg-accent hover:text-black transition-colors">👔 تنسيق طقم</button>
                        </div>

                        {/* Input Area */}
                        <div className="p-6 pt-2">
                            <div className="relative">
                                <input
                                    type="text"
                                    value={input}
                                    onChange={(e) => setInput(e.target.value)}
                                    onKeyPress={(e) => e.key === 'Enter' && handleSend()}
                                    placeholder="اسأل CELIA عن أي شيء..."
                                    className="w-full bg-zinc-900 border border-white/10 rounded-2xl py-4 pl-14 pr-4 text-xs focus:border-accent outline-none transition-all"
                                />
                                <div className="absolute left-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
                                    <button className="p-2 text-zinc-500 hover:text-accent transition-colors">
                                        <ImageIcon className="w-4 h-4" />
                                    </button>
                                    <button
                                        onClick={handleSend}
                                        className="p-2 bg-accent text-black rounded-xl hover:scale-105 transition-transform"
                                    >
                                        <Send className="w-4 h-4 -rotate-45" />
                                    </button>
                                </div>
                            </div>
                            <div className="mt-4 flex justify-between items-center text-[8px] font-mono text-zinc-600 uppercase tracking-widest">
                                <span>End-to-end Neural Audio encryption enabled</span>
                                <div className="flex gap-2">
                                    <Camera className="w-3 h-3" />
                                    <ShoppingBag className="w-3 h-3" />
                                </div>
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </>
    );
};
