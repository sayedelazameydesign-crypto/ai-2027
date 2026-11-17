
import { Navbar } from "@/components/layout/Navbar";
import { CartDrawer } from "@/components/layout/CartDrawer";
import { ReactNode } from "react";

export default function StoreLayout({ children }: { children: ReactNode }) {
    return (
        <div className="flex flex-col min-h-screen">
            <Navbar />
            <CartDrawer />
            <main className="flex-1">
                {children}
            </main>
            <footer className="py-12 border-t border-white/10 text-center text-muted-foreground text-sm">
                <p>© 2026 FASHION26. DESIGNED FOR THE FUTURE.</p>
            </footer>
        </div>
    );
}
