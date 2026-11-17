export function AIPlaceholder() {
    return (
        <section className="py-24 px-6 bg-[#0a0a0a] border-y border-white/5">
            <div className="max-w-7xl mx-auto grid md:grid-cols-2 gap-12 items-center">
                <div className="space-y-6">
                    <div className="inline-block px-3 py-1 rounded-full border border-[var(--accent)] text-[var(--accent)] text-xs font-mono uppercase tracking-widest">
                        AI Powered
                    </div>
                    <h2 className="text-4xl md:text-5xl font-bold tracking-tighter">
                        YOUR VIRTUAL <span className="text-[var(--accent)]">STYLIST</span> is HERE.
                    </h2>
                    <p className="text-muted-foreground max-w-md">
                        Our new stylist algorithm analyzes your preferences to curate outfits that define your 2026 aesthetic.
                    </p>
                    <button className="px-8 py-3 bg-white text-black font-bold hover:bg-[var(--accent)] transition-colors uppercase tracking-wide">
                        Try Beta
                    </button>
                </div>
                <div className="h-[400px] w-full bg-white/5 rounded-lg border border-dashed border-white/10 flex items-center justify-center relative overflow-hidden group">
                    <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(204,255,0,0.1),transparent_70%)] opacity-0 group-hover:opacity-100 transition-opacity" />
                    <span className="font-mono text-muted-foreground animate-pulse text-sm">
                        [ 3D PREVIEW LOADING... ]
                    </span>
                </div>
            </div>
        </section>
    )
}
