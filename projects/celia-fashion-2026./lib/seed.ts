import { Product } from "./data";

// Re-export Product type for use in components
export type { Product } from "./data";

export interface BlogPost {
    id: string;
    title: string;
    excerpt: string;
    content: string;
    author: string;
    date: string;
    readTime: string;
    image: string;
    tags: string[];
}

// Extended Product Categories for Celia Fashion
const CATEGORIES = ["فساتين", "بلوزات", "بنطال", "أحذية", "إكسسوارات"];

// Specific Products from User Request
const CUSTOM_PRODUCTS = [
    { name: "إكسسوارات سيليا 103", price: 80350, cat: "إكسسوارات", img: "1551488852-080175b989c9" },
    { name: "فساتين سيليا 105", price: 76600, cat: "فساتين", img: "1515886657613-9f3515b0c78f" },
    { name: "بلوزات سيليا 106", price: 36950, cat: "بلوزات", img: "1434389677669-e08b4cac3105" },
    { name: "فساتين سيليا 110", price: 114650, cat: "فساتين", img: "1496747611176-843222e1e57c" },
    { name: "فساتين سيليا 100", price: 108000, cat: "فساتين", img: "1594938298603-c8148c472f96" },
    { name: "بنطال سيليا 102", price: 92000, cat: "بنطال", img: "1550614000-4b9519e49a27" },
    { name: "أحذية سيليا 109", price: 45000, cat: "أحذية", img: "1525507119028-ed7863a52019" },
];

// Mock Data Generators
export const generateProducts = (count: number): Product[] => {
    return Array.from({ length: 8 }).map((_, i) => {
        const custom = CUSTOM_PRODUCTS[i] || {
            name: `منتج سيليا ${114 + i}`,
            price: 50000,
            cat: "إكسسوارات",
            img: "1539008835657-9e8e9680c956"
        };

        return {
            id: `prod_${i + 1}`,
            name: custom.name,
            price: custom.price,
            category: custom.cat,
            image: `https://images.unsplash.com/photo-${custom.img}?w=800&q=80`,
            description: "تصميم أنيق مصنوع من أجود الخامات. مثالي لنمط الحياة المصري العصري.",
            rating: (Math.random() * 2 + 3).toFixed(1),
            stock: Math.floor(Math.random() * 50),
            isTrend: i < 4,
            ai_summary: "مثالي للمناسبات الصيفية. تقييم عالي للخامات القطنية.",
            className: ""
        };
    });
};

export const generateBlogPosts = (): BlogPost[] => [
    {
        id: "blog_1",
        title: "The Rise of Smart Fabrics in 2026",
        excerpt: "How conductive thread and shape-shifting polymers are redefining our wardrobes.",
        content: `
# The Digital Silk Road

In the year 2026, the concept of static clothing is becoming obsolete. As we navigate the intersection of high fashion and high technology, our garments are evolving into high-bandwidth interfaces.

## Why Now?
Miniaturization of silicon and progress in **biological nanotech** have allowed us to weave transistors directly into cotton and silk fibers.

> "A jacket is no longer a shield; it's a sensor." — *Dr. Elena Vance, Future Textiles Lab*

### Key Transformations
1. **Dynamic Warming**: Fabrics that adjust their thermal conductivity based on local weather APIs.
2. **Biometric Sync**: Your heart rate and stress levels can change the transparency or color of certain 2026 collections.
3. **Sustainable Compute**: Solar-harvesting threads that charge your AR glasses while you walk.

The future of fashion is not just what you wear, but how your clothing communicates with the grid.
        `.trim(),
        author: "AL-AI MODA",
        date: "JAN 12, 2026",
        readTime: "5 min",
        image: "https://images.unsplash.com/photo-1558769132-cb1aea458c5e?w=800&q=80",
        tags: ["Tech", "Future", "AI"]
    },
    {
        id: "blog_2",
        title: "Sustainable Cyberpunk: A Contradiction?",
        excerpt: "Exploring the intersection of high-tech aesthetics and zero-waste production.",
        content: `
# Eco-Core: The 2026 Aesthetic

For decades, 'Cyberpunk' implied a dystopian, polluted future. But in 2026, the 'Cyber' aesthetic is merging with extreme sustainability.

## Lab-Grown Luxury
We are seeing a move away from traditional animal leather towards **Mycelium Leather** (mushroom-based) and **Lab-Grown Silk**. These materials are then treated with neon dyes that are 100% bio-degradable.

- **Zero-Waste Cutting**: Algorithms that layout patterns to ensure 0% fabric scraps.
- **Recycleable Electronics**: E-textiles that can be dissolved in eco-solvent at the end of their life cycle.

### The New Palette
The 2026 trend radar shows a shift toward *Luminescent Moss* and *Metallic Ash*. These colors represent a world where technology and nature have finally made peace.
        `.trim(),
        author: "ECO-AI BOT",
        date: "JAN 10, 2026",
        readTime: "4 min",
        image: "https://images.unsplash.com/photo-1496747611176-843222e1e57c?w=800&q=80",
        tags: ["Sustainability", "Style", "Eco"]
    },
    {
        id: "blog_3",
        title: "Neo-Minimalism: 2026 Fashion Trend Report",
        excerpt: "Why 'Less' is the ultimate high-tech flex this season.",
        content: `
# Minimalism as Utility

The 'Ultra-Minimalist' trend of 2026 isn't just about aesthetics; it's about reducing cognitive load in an AR-saturated world.

## The Bento Grid Concept
Just as we organize our digital interfaces into clean, geometric grids, we are beginning to organize our wardrobes the same way. Modularity is key.

1. **Modular Sleeves**: Magnetic attaches that work with any base layer.
2. **Hidden Tech**: No visible buttons, only haptic touch zones.
3. **Metallic Accents**: Used sparingly to signal status and connectivity.

### Conclusion
As we look forward to the mid-2020s, the best designs will be those that disappear into our lives while providing maximum functionality.
        `.trim(),
        author: "TREND-RADAR",
        date: "JAN 05, 2026",
        readTime: "3 min",
        image: "https://images.unsplash.com/photo-1534670007418-fbb7f6cf32c3?w=800&q=80",
        tags: ["Trends", "Retail", "Bento"]
    }
];

export const ALL_PRODUCTS = generateProducts(20);
export const ALL_POSTS = generateBlogPosts();
