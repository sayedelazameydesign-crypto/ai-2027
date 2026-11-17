
import { Hero } from "@/components/sections/Hero";
import { BentoGrid } from "@/components/sections/BentoGrid";
import { AIPlaceholder } from "@/components/sections/AISection";

export default function Home() {
    return (
        <div className="bg-background text-foreground overflow-x-hidden selection:bg-[var(--accent)] selection:text-black">
            <Hero />
            <BentoGrid />
            <AIPlaceholder />

        </div>
    );
}
