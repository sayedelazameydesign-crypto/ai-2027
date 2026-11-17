"use client";

import { syncSeedToSupabase } from "@/lib/sync";
import { useState } from "react";
import { Database, RefreshCcw, CheckCircle, AlertCircle } from "lucide-react";

export default function AdminSyncPage() {
    const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
    const [message, setMessage] = useState('');

    const handleSync = async () => {
        setStatus('loading');
        try {
            const result = await syncSeedToSupabase();
            if (result.success) {
                setStatus('success');
                setMessage('تم رفع جميع البيانات إلى Supabase بنجاح (المنتجات والمقالات).');
            } else {
                setStatus('error');
                setMessage('حدث خطأ أثناء الرفع. تأكد من إعداد الجداول (SQL Schema) في Supabase أولاً.');
            }
        } catch (err) {
            setStatus('error');
            setMessage('فشل الاتصال بـ Supabase. تحقق من الـ API Keys.');
        }
    };

    return (
        <div className="min-h-screen bg-black text-white p-8 flex items-center justify-center font-mono">
            <div className="max-w-md w-full bg-zinc-900 border border-zinc-800 p-8 rounded-3xl space-y-8">
                <div className="flex flex-col items-center text-center space-y-4">
                    <div className="w-16 h-16 bg-accent/20 rounded-full flex items-center justify-center">
                        <Database className="w-8 h-8 text-accent" />
                    </div>
                    <h1 className="text-2xl font-black uppercase italic tracking-tighter">Supabase Sync</h1>
                    <p className="text-zinc-500 text-sm">
                        قم برفع البيانات الافتراضية (Seed Data) إلى قاعدة بياناتك السحابية بضغطة واحدة.
                    </p>
                </div>

                <div className="space-y-4">
                    <button
                        onClick={handleSync}
                        disabled={status === 'loading'}
                        className="w-full py-4 bg-white text-black font-black uppercase tracking-widest hover:bg-accent transition-all disabled:opacity-50 flex items-center justify-center gap-3 rounded-xl"
                    >
                        {status === 'loading' ? (
                            <RefreshCcw className="w-5 h-5 animate-spin" />
                        ) : (
                            "ابدأ عملية المزامنة"
                        )}
                    </button>

                    {status === 'success' && (
                        <div className="flex items-center gap-3 p-4 bg-green-500/10 border border-green-500/50 rounded-xl text-green-500 text-xs">
                            <CheckCircle className="w-4 h-4 shrink-0" />
                            <span>{message}</span>
                        </div>
                    )}

                    {status === 'error' && (
                        <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/50 rounded-xl text-red-500 text-xs">
                            <AlertCircle className="w-4 h-4 shrink-0" />
                            <span>{message}</span>
                        </div>
                    )}
                </div>

                <div className="pt-6 border-t border-zinc-800 text-[10px] text-zinc-600 space-y-2">
                    <p>تنبيه: هذه العملية ستقوم بجلب البيانات من <span className="text-white">lib/seed.ts</span>.</p>
                    <p>تأكد من تفعيل RLS أو إضافة الـ SQL Schema المرفق.</p>
                </div>
            </div>
        </div>
    );
}
