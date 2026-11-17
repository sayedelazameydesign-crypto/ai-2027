"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
    LineChart,
    Line,
    XAxis,
    YAxis,
    Tooltip,
    ResponsiveContainer,
    CartesianGrid,
    AreaChart,
    Area
} from 'recharts';
import {
    TrendingUp,
    Package,
    Users,
    DollarSign,
    BrainCircuit,
    Layers,
    ShoppingBag,
    Bell,
    Search,
    LayoutDashboard,
    RefreshCcw,
    Sun,
    Moon,
    ArrowUpRight,
    MoreHorizontal
} from "lucide-react";
import { TrendRadar } from "@/components/admin/TrendRadar";
import { cn } from "@/lib/utils";

// Mock Data Types
interface Order {
    id: string;
    items: number;
    total: string;
    status: 'PROCESSING' | 'SHIPPED' | 'DELIVERED';
    time: string;
}

const INITIAL_SALES_DATA = [
    { name: '01:00', sales: 400 },
    { name: '04:00', sales: 700 },
    { name: '08:00', sales: 500 },
    { name: '12:00', sales: 1200 },
    { name: '16:00', sales: 900 },
    { name: '20:00', sales: 1500 },
];

const MOCK_ORDERS: Order[] = [
    { id: "TR-2901", items: 2, total: "1,250 EGP", status: "PROCESSING", time: "2 mins ago" },
    { id: "TR-2902", items: 1, total: "750 EGP", status: "SHIPPED", time: "10 mins ago" },
    { id: "TR-2903", items: 3, total: "3,100 EGP", status: "DELIVERED", time: "45 mins ago" },
    { id: "TR-2904", items: 1, total: "800 EGP", status: "PROCESSING", time: "1 hour ago" },
];

export default function AdminDashboard() {
    const [salesData, setSalesData] = useState(INITIAL_SALES_DATA);
    const [orders, setOrders] = useState<Order[]>(MOCK_ORDERS);
    const [loading, setLoading] = useState(true);
    const [isDarkMode, setIsDarkMode] = useState(true);

    // Simulate Dynamic Data Fetching
    useEffect(() => {
        const timer = setTimeout(() => {
            setLoading(false);
            // Simulate a real-time update every 10 seconds
            const interval = setInterval(() => {
                setSalesData(prev => [
                    ...prev.slice(1),
                    { name: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), sales: Math.floor(Math.random() * 1000) + 500 }
                ]);
            }, 10000);
            return () => clearInterval(interval);
        }, 1500);
        return () => clearTimeout(timer);
    }, []);

    const toggleTheme = () => {
        setIsDarkMode(!isDarkMode);
        document.documentElement.classList.toggle("dark");
    };

    return (
        <div className={cn(
            "min-h-screen p-8 font-sans selection:bg-accent selection:text-black transition-colors duration-500",
            isDarkMode ? "bg-[#050505] text-white" : "bg-zinc-50 text-zinc-900"
        )}>
            {/* Header & Control Center */}
            <header className="mb-12 flex flex-col md:flex-row justify-between items-center gap-8">
                <div className="w-full md:w-auto">
                    <motion.h1
                        initial={{ x: -20, opacity: 0 }}
                        animate={{ x: 0, opacity: 1 }}
                        className="text-4xl font-black tracking-tighter uppercase flex items-center gap-3"
                    >
                        <LayoutDashboard className="w-8 h-8 text-accent" />
                        CELIA OS <span className="text-zinc-500">/</span> ADMIN
                    </motion.h1>
                    <div className="flex items-center gap-2 text-[10px] font-mono text-zinc-500 mt-2 uppercase tracking-widest">
                        <div className="w-2 h-2 rounded-full bg-accent animate-pulse" />
                        Network Level: Alpha-G7 // Status: Optimal
                    </div>
                </div>

                <div className="flex items-center gap-4 w-full md:w-auto">
                    <div className="relative flex-1 md:w-64">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
                        <input
                            type="text"
                            placeholder="SEARCH ACCOUNTS, PRODUCTS..."
                            className={cn(
                                "w-full border rounded-xl py-3 pl-10 pr-4 text-xs font-mono transition-all outline-none",
                                isDarkMode ? "bg-zinc-900/50 border-zinc-800 focus:border-accent" : "bg-white border-zinc-200 focus:border-accent"
                            )}
                        />
                    </div>

                    <button
                        onClick={toggleTheme}
                        className={cn(
                            "p-3 border rounded-xl transition-all",
                            isDarkMode ? "bg-zinc-900/50 border-zinc-800 hover:bg-zinc-800" : "bg-white border-zinc-200 hover:bg-zinc-100"
                        )}
                    >
                        {isDarkMode ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
                    </button>

                    <button className={cn(
                        "p-3 border rounded-xl relative transition-all",
                        isDarkMode ? "bg-zinc-900/50 border-zinc-800 hover:bg-zinc-800" : "bg-white border-zinc-200 hover:bg-zinc-100"
                    )}>
                        <Bell className="w-5 h-5" />
                        <span className="absolute top-3 right-3 w-2 h-2 bg-accent rounded-full border-2 border-zinc-950" />
                    </button>

                    <div className="w-12 h-12 rounded-2xl bg-accent flex items-center justify-center text-black font-black italic transform -rotate-12 hover:rotate-0 transition-transform cursor-pointer">
                        AI
                    </div>
                </div>
            </header>

            {/* Main Grid */}
            <div className="space-y-8">
                {/* 1. Stats Row with Skeleton & Motion */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    {[
                        { label: "Daily Revenue", value: "84,500 EGP", icon: <DollarSign className="w-4 h-4" />, trend: "+24% ^", color: "text-green-500" },
                        { label: "Active Orders", value: "124", icon: <ShoppingBag className="w-4 h-4" />, trend: "+12% ^", color: "text-accent" },
                        { label: "Traffic Index", value: "12.4K", icon: <Users className="w-4 h-4" />, trend: "+8% ^", color: "text-zinc-500" },
                        { label: "Efficiency", value: "98.2%", icon: <Zap className="w-4 h-4" />, trend: "OPTIMAL", color: "text-accent" },
                    ].map((stat, i) => (
                        loading ? (
                            <div key={i} className={cn("h-32 animate-pulse rounded-2xl", isDarkMode ? "bg-zinc-900" : "bg-zinc-200")} />
                        ) : (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: i * 0.1 }}
                                whileHover={{ y: -5 }}
                                className={cn(
                                    "p-6 border rounded-3xl transition-all group",
                                    isDarkMode ? "bg-zinc-900/40 border-zinc-800 hover:border-accent/40" : "bg-white border-zinc-200 hover:shadow-xl"
                                )}
                            >
                                <div className="flex justify-between items-center mb-6">
                                    <div className={cn(
                                        "p-2.5 rounded-xl transition-colors",
                                        isDarkMode ? "bg-zinc-800 group-hover:bg-accent group-hover:text-black" : "bg-zinc-100 group-hover:bg-accent"
                                    )}>
                                        {stat.icon}
                                    </div>
                                    <span className={cn("text-[10px] font-black uppercase tracking-tighter", stat.color)}>
                                        {stat.trend}
                                    </span>
                                </div>
                                <p className="text-zinc-500 text-[10px] font-mono tracking-widest uppercase mb-1">{stat.label}</p>
                                <p className="text-2xl font-black tracking-tighter">{stat.value}</p>
                            </motion.div>
                        )
                    ))}
                </div>

                {/* 2. Insights Grid */}
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
                    {/* Left Side: Charts & Orders */}
                    <div className="lg:col-span-8 space-y-8">
                        {/* Revenue Performance */}
                        <div className={cn(
                            "p-8 border rounded-[2rem] overflow-hidden",
                            isDarkMode ? "bg-zinc-900/40 border-zinc-800" : "bg-white border-zinc-200 shadow-sm"
                        )}>
                            <div className="flex justify-between items-center mb-10">
                                <div>
                                    <h2 className="text-xl font-black uppercase italic italic flex items-center gap-3">
                                        <TrendingUp className="w-5 h-5 text-accent" />
                                        Performance Index
                                    </h2>
                                    <p className="text-xs text-zinc-500 mt-1 uppercase font-mono">Live Revenue Delta Tracking</p>
                                </div>
                                <div className="flex bg-zinc-800/50 p-1 rounded-xl">
                                    <button className="px-4 py-1.5 bg-accent text-black text-[10px] font-black rounded-lg uppercase">Realtime</button>
                                    <button className="px-4 py-1.5 text-zinc-500 text-[10px] font-bold uppercase hover:text-white">Historical</button>
                                </div>
                            </div>

                            {loading ? (
                                <div className="h-[300px] w-full flex items-center justify-center">
                                    <RefreshCcw className="w-8 h-8 animate-spin text-accent opacity-20" />
                                </div>
                            ) : (
                                <div className="h-[320px] w-full">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <AreaChart data={salesData}>
                                            <defs>
                                                <linearGradient id="colorSales" x1="0" y1="0" x2="0" y2="1">
                                                    <stop offset="5%" stopColor="#d4ff00" stopOpacity={0.3} />
                                                    <stop offset="95%" stopColor="#d4ff00" stopOpacity={0} />
                                                </linearGradient>
                                            </defs>
                                            <CartesianGrid strokeDasharray="3 3" stroke={isDarkMode ? "#222" : "#eee"} vertical={false} />
                                            <XAxis
                                                dataKey="name"
                                                stroke="#555"
                                                fontSize={10}
                                                fontFamily="JetBrains Mono"
                                                tickLine={false}
                                                axisLine={false}
                                            />
                                            <YAxis hide domain={['auto', 'auto']} />
                                            <Tooltip
                                                content={({ payload, label }) => (
                                                    <div className="bg-black/90 backdrop-blur-xl border border-accent/30 p-4 rounded-2xl shadow-2xl">
                                                        <p className="text-[10px] font-mono text-zinc-500 uppercase mb-2">{label}</p>
                                                        <div className="flex items-center gap-2">
                                                            <div className="w-2 h-2 rounded-full bg-accent" />
                                                            <span className="text-lg font-black text-white">{payload?.[0]?.value} EGP</span>
                                                        </div>
                                                        <p className="text-[8px] text-accent font-black mt-1 uppercase tracking-widest">Growth detected</p>
                                                    </div>
                                                )}
                                            />
                                            <Area
                                                type="monotone"
                                                dataKey="sales"
                                                stroke="#d4ff00"
                                                strokeWidth={4}
                                                fillOpacity={1}
                                                fill="url(#colorSales)"
                                                animationDuration={2000}
                                            />
                                        </AreaChart>
                                    </ResponsiveContainer>
                                </div>
                            )}
                        </div>

                        {/* Smart Orders Queue */}
                        <div className={cn(
                            "p-8 border rounded-[2rem]",
                            isDarkMode ? "bg-zinc-900/40 border-zinc-800" : "bg-white border-zinc-200"
                        )}>
                            <div className="flex justify-between items-center mb-8">
                                <h2 className="text-xl font-black uppercase italic flex items-center gap-3">
                                    <ArrowUpRight className="w-5 h-5 text-accent" />
                                    Active Orders Feed
                                </h2>
                                <button className="text-[10px] font-mono text-zinc-500 hover:text-accent flex items-center gap-2">
                                    FULL LIST <MoreHorizontal className="w-4 h-4" />
                                </button>
                            </div>

                            <div className="space-y-4">
                                <AnimatePresence mode="popLayout">
                                    {orders.map((order, i) => (
                                        <motion.div
                                            key={order.id}
                                            initial={{ opacity: 0, x: -20 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: i * 0.1 }}
                                            className={cn(
                                                "flex items-center justify-between p-5 border rounded-2xl transition-all group",
                                                isDarkMode ? "bg-zinc-900/40 border-zinc-800 hover:bg-zinc-800/40 hover:border-accent/20" : "bg-zinc-50 border-zinc-100 hover:bg-white hover:shadow-lg"
                                            )}
                                        >
                                            <div className="flex items-center gap-5">
                                                <div className={cn(
                                                    "w-12 h-12 rounded-xl flex items-center justify-center font-black text-[10px]",
                                                    isDarkMode ? "bg-zinc-800 text-zinc-500 group-hover:bg-accent group-hover:text-black" : "bg-white text-zinc-900 shadow-sm"
                                                )}>
                                                    #{order.id.split('-')[1]}
                                                </div>
                                                <div>
                                                    <p className="font-black text-sm uppercase italic">Order {order.id}</p>
                                                    <div className="flex items-center gap-2 mt-1">
                                                        <span className="text-[10px] font-mono text-zinc-500 uppercase tracking-tighter">{order.items} ITEMS</span>
                                                        <div className="w-1 h-1 rounded-full bg-zinc-700" />
                                                        <span className="text-[10px] font-mono text-accent font-black">{order.total}</span>
                                                    </div>
                                                </div>
                                            </div>
                                            <div className="text-right flex flex-col items-end gap-2">
                                                <span className={cn(
                                                    "text-[8px] font-black uppercase tracking-[0.2em] px-3 py-1 rounded-full",
                                                    order.status === 'SHIPPED' ? "bg-green-500/10 text-green-500 border border-green-500/30" :
                                                        order.status === 'PROCESSING' ? "bg-yellow-500/10 text-yellow-500 border border-yellow-500/30" :
                                                            "bg-blue-500/10 text-blue-500 border border-blue-500/30"
                                                )}>
                                                    {order.status}
                                                </span>
                                                <p className="text-[9px] text-zinc-500 font-mono tracking-widest">{order.time}</p>
                                            </div>
                                        </motion.div>
                                    ))}
                                </AnimatePresence>
                            </div>
                        </div>
                    </div>

                    {/* Right Side: AI Radar & Alerts */}
                    <div className="lg:col-span-4 space-y-8">
                        <TrendRadar />

                        <motion.div
                            whileHover={{ scale: 1.02 }}
                            className="p-8 bg-accent text-black rounded-[2rem] space-y-4 shadow-xl shadow-accent/20 relative overflow-hidden group"
                        >
                            <div className="absolute top-0 right-0 p-8 opacity-10 group-hover:scale-150 transition-transform duration-1000">
                                <BrainCircuit className="w-40 h-40" />
                            </div>
                            <div className="relative z-10">
                                <div className="flex items-center gap-2 mb-4">
                                    <BrainCircuit className="w-5 h-5" />
                                    <span className="text-[10px] font-black uppercase tracking-[0.3em]">AI Trend Pulse</span>
                                </div>
                                <h3 className="font-heading font-black text-3xl italic tracking-tighter leading-tight">
                                    NEO-SILVER <br /> SURGE DETECTED
                                </h3>
                                <p className="text-xs font-medium leading-relaxed mt-4">
                                    تم ملاحظة زيادة بنسبة 140% في عمليات البحث عن "المعدني الفضي" في القاهرة. الذكاء الاصطناعي يقترح إعادة تحويل المخزون المخصص لهذه المنطقة فوراً.
                                </p>
                                <button className="w-full mt-6 py-4 bg-black text-white text-[10px] font-black uppercase tracking-[0.3em] hover:bg-zinc-900 transition-all rounded-2xl active:scale-95">
                                    تطبيق التغيير الآن
                                </button>
                            </div>
                        </motion.div>

                        <div className={cn(
                            "p-8 border rounded-[2rem] space-y-6",
                            isDarkMode ? "bg-zinc-900/40 border-zinc-800" : "bg-white border-zinc-200"
                        )}>
                            <h4 className="text-[10px] font-black uppercase tracking-widest text-zinc-500">Live Infrastructure</h4>
                            <div className="space-y-4">
                                {[
                                    { name: "Supabase DB", status: "Optimal", color: "bg-green-500" },
                                    { name: "Model Inference", status: "Latency 42ms", color: "bg-green-500" },
                                    { name: "Payment API", status: "Enabled", color: "bg-accent" },
                                ].map((sys, i) => (
                                    <div key={i} className="flex justify-between items-center text-[10px] font-mono">
                                        <span className="text-zinc-500">{sys.name}</span>
                                        <div className="flex items-center gap-2">
                                            <span className="text-zinc-300 font-black uppercase tracking-tighter">{sys.status}</span>
                                            <div className={cn("w-1.5 h-1.5 rounded-full", sys.color, "animate-pulse")} />
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Zap Component Placeholder (Icon) */}
            <div className="hidden">
                <Zap className="w-4 h-4" />
            </div>
        </div>
    );
}

// Sub-component for Zap icon as Lucide may not export it as Zap in some versions but as Bolt/Electricity variants
function Zap(props: any) {
    return (
        <svg
            {...props}
            xmlns="http://www.w3.org/2000/svg"
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
        >
            <path d="M4 14l11-10v8h5L9 22v-8H4z" />
        </svg>
    );
}
