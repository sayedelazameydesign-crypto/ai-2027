import {
    LayoutDashboard,
    Box,
    FileText,
    LogOut,
    Settings,
    RefreshCcw,
    Sparkles,
    Activity,
    BrainCircuit,
    Cpu
} from "lucide-react";
import Link from "next/link";
import { ReactNode } from "react";

export default function AdminLayout({ children }: { children: ReactNode }) {
    const navItems = [
        { href: "/dashboard", label: "Dashboard", icon: <LayoutDashboard className="w-5 h-5" /> },
        { href: "/dashboard/products", label: "Inventory", icon: <Box className="w-5 h-5" /> },
        { href: "/dashboard/blog", label: "AI Intel/Blog", icon: <FileText className="w-5 h-5" /> },
        { href: "/dashboard/sync", label: "Data Sync", icon: <RefreshCcw className="w-5 h-5" /> },
    ];

    return (
        <div className="flex min-h-screen bg-[#050505] text-white font-sans selection:bg-accent selection:text-black">
            {/* Sidebar */}
            <aside className="w-72 border-r border-white/5 flex flex-col fixed inset-y-0 left-0 bg-[#050505] z-50">
                <div className="p-8 border-b border-white/5 bg-gradient-to-br from-zinc-900 to-transparent">
                    <Link href="/" className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center text-black font-black italic">
                            C
                        </div>
                        <h1 className="text-xl font-black tracking-tighter uppercase italic leading-none">
                            CELIA <span className="text-accent underline">ROOT</span>
                        </h1>
                    </Link>
                    <p className="text-[8px] font-mono text-zinc-600 mt-2 tracking-[0.4em] uppercase">Control Authority 2.0</p>
                </div>

                <nav className="flex-1 p-6 space-y-3 mt-4">
                    <div className="px-4 mb-4 text-[10px] font-mono text-zinc-500 uppercase tracking-widest">Navigation</div>
                    {navItems.map((item) => (
                        <Link
                            key={item.href}
                            href={item.href}
                            className="flex items-center justify-between group px-4 py-3.5 hover:bg-white/5 rounded-2xl transition-all border border-transparent hover:border-white/5"
                        >
                            <div className="flex items-center gap-4">
                                <div className="text-zinc-500 group-hover:text-accent transition-colors">
                                    {item.icon}
                                </div>
                                <span className="text-sm font-bold uppercase italic tracking-tight">{item.label}</span>
                            </div>
                            <Sparkles className="w-3 h-3 text-accent opacity-0 group-hover:opacity-100 transition-opacity" />
                        </Link>
                    ))}

                    <div className="px-4 pt-8 mb-4 text-[10px] font-mono text-zinc-500 uppercase tracking-widest">Infrastructure</div>
                    <Link href="#" className="flex items-center gap-4 px-4 py-3 text-zinc-500 hover:text-accent transition-all group">
                        <Cpu className="w-5 h-5" />
                        <span className="text-xs font-bold uppercase tracking-widest">Neural Status</span>
                    </Link>
                    <Link href="#" className="flex items-center gap-4 px-4 py-3 text-zinc-500 hover:text-accent transition-all">
                        <Activity className="w-5 h-5" />
                        <span className="text-xs font-bold uppercase tracking-widest">Bandwidth</span>
                    </Link>
                </nav>

                {/* Footer Section in Sidebar */}
                <div className="p-6 border-t border-white/5 space-y-4">
                    <div className="p-4 rounded-xl bg-zinc-900/50 border border-white/5">
                        <div className="flex items-center gap-2 mb-2">
                            <BrainCircuit className="w-3 h-3 text-accent" />
                            <span className="text-[8px] font-mono text-zinc-400 uppercase tracking-widest">Safety Hash</span>
                        </div>
                        <p className="text-[10px] font-mono text-zinc-600 break-all leading-tight">
                            v2.6.01_OS_STABLE_#0923
                        </p>
                    </div>
                    <button className="flex items-center gap-3 text-zinc-500 hover:text-red-500 transition-colors w-full px-4 text-xs font-bold uppercase">
                        <LogOut className="w-4 h-4" />
                        Terminate Session
                    </button>
                </div>
            </aside>

            {/* Main Content Area */}
            <main className="flex-1 ml-72">
                <div className="min-h-screen bg-[radial-gradient(circle_at_top_right,_var(--color-neon-lime)_0%,_transparent_5%)]">
                    {children}
                </div>
            </main>
        </div>
    );
}
