"use client";

import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import Link from "next/link";

export function Hero() {
    return (
        <section className="relative h-screen w-full flex flex-col justify-center px-6 overflow-hidden">
            {/* Abstract Background Elements */}
            <div className="absolute inset-0 z-0 bg-background">
                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-[var(--accent)] opacity-10 blur-[120px] rounded-full" />
                <div className="absolute top-0 right-0 w-full h-full bg-[radial-gradient(circle_at_50%_50%,rgba(204,255,0,0.05),transparent_50%)]" />
            </div>

            <div className="relative z-10 max-w-7xl mx-auto w-full">
                <div className="space-y-6">
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.8 }}
                    >
                        <h1 className="text-6xl md:text-9xl font-black tracking-tighter leading-[0.9]">
                            FUTURE <br />
                            <span className="text-transparent bg-clip-text bg-gradient-to-r from-white to-[var(--metallic)]">
                                AESTHETICS
                            </span>
                        </h1>
                    </motion.div>

                    <motion.p
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.4, duration: 0.8 }}
                        className="text-lg md:text-xl text-muted-foreground max-w-md font-light"
                    >
                        Redefining minimalist fashion for the digital age.
                        Sustainable. Adaptive. Intelligent.
                    </motion.p>

                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.6, duration: 0.8 }}
                        className="pt-8"
                    >
                        <Link
                            href="/shop"
                            className="group inline-flex items-center gap-3 text-lg font-medium border-b border-foreground pb-1 hover:border-[var(--accent)] hover:text-[var(--accent)] transition-all"
                        >
                            Start Exploring
                            <ArrowRight className="w-5 h-5 transform group-hover:translate-x-1 transition-transform" />
                        </Link>
                    </motion.div>
                </div>
            </div>

            {/* Scroll Indicator */}
            <div className="absolute bottom-10 left-6 text-xs font-mono text-muted-foreground tracking-widest uppercase writing-vertical-rl">
                Scrl Down
            </div>
        </section>
    );
}
