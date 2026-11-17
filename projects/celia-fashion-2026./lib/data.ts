export interface Product {
    id: string;
    name: string;
    price: number;
    category: string;
    image: string;
    className?: string; // For Bento Grid layout
    description?: string;
    rating?: string;
    stock?: number;
    isTrend?: boolean;
    ai_summary?: string;
}

export const products: Product[] = [
    {
        id: "1",
        name: "Cyber-V Vest",
        price: 120,
        category: "Outerwear",
        image: "https://images.unsplash.com/photo-1551488852-080175b989c9?w=800&q=80",
        className: "md:col-span-2 md:row-span-2",
    },
    {
        id: "2",
        name: "Neon Tech Runner",
        price: 180,
        category: "Footwear",
        image: "https://images.unsplash.com/photo-1620794341491-76be6eeb6946?w=800&q=80",
        className: "md:col-span-1 md:row-span-1",
    },
    {
        id: "3",
        name: "Smart Fabric Tee",
        price: 65,
        category: "Tops",
        image: "https://images.unsplash.com/photo-1576566588028-4147f3842f27?w=800&q=80",
        className: "md:col-span-1 md:row-span-1",
    },
    {
        id: "4",
        name: "Modular Cargo",
        price: 150,
        category: "Bottoms",
        image: "https://images.unsplash.com/photo-1559563458-52c69529db81?w=800&q=80",
        className: "md:col-span-1 md:row-span-2",
    },
    {
        id: "5",
        name: "Holo Visor",
        price: 45,
        category: "Accessories",
        image: "https://images.unsplash.com/photo-1511499767150-a48a237f0083?w=800&q=80",
        className: "md:col-span-2 md:row-span-1",
    },
];
