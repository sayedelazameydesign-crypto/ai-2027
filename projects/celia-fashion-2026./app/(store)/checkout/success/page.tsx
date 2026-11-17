"use client";
import { motion } from "framer-motion";
import { Plane, MapPin, CheckCircle, ArrowRight } from "lucide-react";
import Link from "next/link";

export default function OrderSuccess() {
    return (
        <div className="min-h-screen bg-black text-white p-8 flex flex-col items-center justify-center font-mono relative overflow-hidden">
            {/* Background Grid */}
            <div className="absolute inset-0 bg-[url('https://grainy-gradients.vercel.app/noise.svg')] opacity-10 pointer-events-none" />

            {/* Success Message */}
            <motion.div
                initial={{ scale: 0.8, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ duration: 0.5 }}
                className="text-center mb-12 relative z-10"
            >
                <div className="w-20 h-20 bg-[var(--accent)]/10 rounded-full flex items-center justify-center mx-auto mb-6 backdrop-blur-md border border-[var(--accent)]/20 animate-pulse">
                    <CheckCircle size={40} className="text-[var(--accent)]" />
                </div>
                <h1 className="text-4xl md:text-5xl font-black uppercase tracking-tighter mb-2">Order Secured</h1>
                <p className="text-zinc-500 font-bold tracking-widest">ID: #DRN-2026-X99</p>
            </motion.div>

            {/* Radar Widget - Drone Tracking */}
            <div className="relative w-full max-w-md aspect-square border border-white/10 rounded-full flex items-center justify-center overflow-hidden bg-[radial-gradient(circle,_rgba(255,255,255,0.03)_0%,_transparent_70%)] mb-12">
                {/* Radar Rings */}
                <div className="absolute inset-0 border border-white/5 rounded-full animate-ping opacity-20 duration-[3s]" />
                <div className="absolute inset-[15%] border border-white/5 rounded-full" />
                <div className="absolute inset-[30%] border border-white/10 rounded-full" />
                <div className="absolute inset-[45%] border border-[var(--accent)]/10 rounded-full" />

                {/* Drone Icon Animation */}
                <motion.div
                    animate={{
                        rotate: 360,
                        x: [0, 60, -60, 0],
                        y: [0, -40, 40, 0]
                    }}
                    transition={{ duration: 12, repeat: Infinity, ease: "linear" }}
                    className="relative z-20 text-[var(--accent)]"
                >
                    <Plane size={36} fill="currentColor" className="drop-shadow-[0_0_10px_var(--accent)]" />
                    <div className="absolute -top-1 -right-1 w-2 h-2 bg-red-500 rounded-full animate-pulse shadow-[0_0_10px_red]" />
                </motion.div>

                {/* Home/User Pin */}
                <div className="absolute text-white/40 drop-shadow-md">
                    <MapPin size={28} />
                </div>

                {/* Radar Scanner Sweep */}
                <div className="absolute inset-0 bg-gradient-to-tr from-transparent via-[var(--accent)]/10 to-transparent animate-spin duration-[4s] rounded-full" />
            </div>

            {/* Delivery Info */}
            <div className="w-full max-w-md grid grid-cols-2 gap-4 relative z-10">
                <div className="bg-white/5 p-4 border border-white/10 rounded-lg backdrop-blur-sm">
                    <p className="text-[10px] text-zinc-400 uppercase mb-1 font-bold tracking-widest">Status</p>
                    <p className="text-sm font-bold text-[var(--accent)] flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" />
                        In Transit (Air)
                    </p>
                </div>
                <div className="bg-white/5 p-4 border border-white/10 rounded-lg backdrop-blur-sm">
                    <p className="text-[10px] text-zinc-400 uppercase mb-1 font-bold tracking-widest">ETA</p>
                    <p className="text-sm font-bold text-white">14 Minutes</p>
                </div>
            </div>

            <div className="mt-8 flex flex-col items-center gap-4 relative z-10 w-full max-w-md">
                <motion.button
                    whileHover={{ scale: 1.05 }}
                    className="text-xs uppercase tracking-[0.3em] text-zinc-500 hover:text-[var(--accent)] transition-colors border-b border-transparent hover:border-[var(--accent)] pb-1"
                >
                    View Flight Path Details
                </motion.button>

                <Link href="/shop" className="w-full py-4 bg-white text-black font-bold uppercase tracking-widest hover:bg-[var(--accent)] transition-colors rounded text-center flex items-center justify-center gap-2">
                    Return to Base <ArrowRight className="w-4 h-4" />
                </Link>
            </div>
        </div>
    );
}
