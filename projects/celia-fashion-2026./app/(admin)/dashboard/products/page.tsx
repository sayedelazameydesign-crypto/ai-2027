"use client";

import { products } from "@/lib/data";
import { Edit, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import Image from "next/image";

export default function AdminProductsPage() {
    return (
        <div className="space-y-8 text-white">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Products</h1>
                    <p className="text-zinc-400">Manage your store inventory.</p>
                </div>
                <button className="flex items-center gap-2 bg-[var(--accent)] text-black px-4 py-2 rounded font-bold uppercase text-xs hover:opacity-90 transition-opacity">
                    <Plus className="w-4 h-4" /> Add Product
                </button>
            </div>

            <div className="rounded-md border border-white/10 overflow-hidden bg-black">
                <table className="w-full text-sm text-left">
                    <thead className="bg-zinc-900 uppercase font-mono text-zinc-400 border-b border-white/10">
                        <tr>
                            <th className="px-6 py-4">Product</th>
                            <th className="px-6 py-4">Status</th>
                            <th className="px-6 py-4">Price</th>
                            <th className="px-6 py-4">Stock</th>
                            <th className="px-6 py-4 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                        {products.map((product) => (
                            <tr key={product.id} className="hover:bg-zinc-900/50 transition-colors">
                                <td className="px-6 py-4 flex items-center gap-3">
                                    <div className="relative w-10 h-10 rounded overflow-hidden">
                                        <Image src={product.image} alt={product.name} fill className="object-cover" />
                                    </div>
                                    <span className="font-bold">{product.name}</span>
                                </td>
                                <td className="px-6 py-4">
                                    <span className="inline-block px-2 py-1 rounded-full bg-green-500/10 text-green-500 text-[10px] font-bold uppercase">
                                        Active
                                    </span>
                                </td>
                                <td className="px-6 py-4 font-mono">${product.price}</td>
                                <td className="px-6 py-4 text-zinc-400">{Math.floor(Math.random() * 50) + 10}</td>
                                <td className="px-6 py-4 text-right">
                                    <button className="p-2 hover:bg-white/10 rounded">
                                        <MoreHorizontal className="w-4 h-4" />
                                    </button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
