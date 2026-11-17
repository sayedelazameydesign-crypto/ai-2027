"use client";

import React from "react";
import {
    TrendingUp,
    ArrowUpRight,
    ArrowDownRight,
    Zap,
    Activity,
    BarChart3
} from "lucide-react";

const TREND_DATA = [
    { item: "Neon Echo Silk", growth: "+45%", status: "Rising", color: "text-accent" },
    { item: "Bio-Leather Boots", growth: "+22%", status: "Stable", color: "text-blue-400" },
    { item: "Metallic Mesh Top", growth: "-5%", status: "Cooling", color: "text-zinc-500" },
    { item: "Smart Fiber Hijab", growth: "+88%", status: "Viral", color: "text-purple-400" },
];

export const TrendRadar = () => {
    return (
        <div className="p-8 rounded-3xl bg-zinc-950 border border-zinc-800 text-white space-y-8">
            <div className="flex justify-between items-start">
                <div className="space-y-1">
                    <h3 className="text-2xl font-heading font-black tracking-tighter flex items-center gap-2">
                        TREND RADAR <Zap className="w-5 h-5 text-accent fill-accent" />
                    </h3>
                    <p className="text-zinc-500 text-xs font-mono uppercase tracking-widest">Predictive Analytics 2026</p>
                </div>
                <div className="flex gap-2">
                    <div className="w-2 h-2 rounded-full bg-accent animate-ping" />
                    <span className="text-[10px] font-mono text-zinc-400">LIVE FEED</span>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {TREND_DATA.map((trend, i) => (
                    <div key={i} className="p-4 rounded-2xl bg-zinc-900 border border-white/5 hover:border-accent/40 transition-colors space-y-4 group">
                        <div className="flex justify-between items-center">
                            <span className="text-xs font-mono text-zinc-400">#{i + 1}</span>
                            {trend.growth.startsWith('+') ?
                                <ArrowUpRight className="w-4 h-4 text-accent" /> :
                                <ArrowDownRight className="w-4 h-4 text-zinc-500" />
                            }
                        </div>
                        <div className="space-y-1">
                            <p className="font-bold text-sm truncate">{trend.item}</p>
                            <div className="flex items-center gap-2">
                                <span className={trend.color + " text-lg font-black"}>{trend.growth}</span>
                                <span className="text-[8px] uppercase font-mono px-1.5 py-0.5 border border-zinc-800 rounded">{trend.status}</span>
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            <div className="relative h-48 w-full bg-zinc-900/50 rounded-2xl border border-dashed border-zinc-800 flex items-center justify-center overflow-hidden">
                {/* Simulated Waveform or Graph */}
                <div className="absolute inset-0 flex items-end gap-1 px-4 pb-4">
                    {Array.from({ length: 20 }).map((_, i) => (
                        <div
                            key={i}
                            className="bg-accent/20 hover:bg-accent transition-all duration-300 w-full"
                            style={{ height: `${Math.random() * 80 + 10}%` }}
                        />
                    ))}
                </div>
                <div className="relative z-10 flex flex-col items-center gap-2">
                    <BarChart3 className="w-8 h-8 text-accent animate-pulse" />
                    <p className="text-[10px] font-mono text-zinc-500">GENERATING MARKET PROJECTION...</p>
                </div>
            </div>

            <div className="flex justify-between items-center pt-4 border-t border-white/5">
                <div className="flex gap-4">
                    <div className="text-center">
                        <p className="text-[8px] text-zinc-500 font-mono">CONFIDENCE</p>
                        <p className="text-xl font-black">94.2%</p>
                    </div>
                    <div className="text-center">
                        <p className="text-[8px] text-zinc-500 font-mono">SAMPLES</p>
                        <p className="text-xl font-black">1.2M</p>
                    </div>
                </div>
                <button className="text-xs font-bold text-accent hover:underline flex items-center gap-1">
                    FULL DOWNLOAD <Activity className="w-3 h-3" />
                </button>
            </div>
        </div>
    );
};
