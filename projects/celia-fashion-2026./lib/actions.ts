import { supabase } from './supabase';
import { ALL_PRODUCTS, ALL_POSTS, Product, BlogPost } from './seed';

/**
 * Fetch products from Supabase with fallback to local seed data.
 */
export async function getProducts(): Promise<Product[]> {
    try {
        if (!supabase) {
            console.log('Supabase client not initialized, using seed data.');
            return ALL_PRODUCTS;
        }

        const { data, error } = await supabase
            .from('products')
            .select('*');

        if (error || !data || data.length === 0) {
            console.log('Supabase products fetch failed or empty, using seed data.');
            return ALL_PRODUCTS;
        }

        // Map Supabase fields (snake_case) to Frontend fields (camelCase)
        return data.map(p => ({
            id: p.id,
            name: p.name,
            price: Number(p.price),
            category: p.category,
            image: p.image,
            description: p.description,
            rating: p.rating,
            stock: p.stock,
            isTrend: p.is_trend,
            ai_summary: p.ai_summary
        }));
    } catch (err) {
        console.error('Error fetching products from Supabase:', err);
        return ALL_PRODUCTS;
    }
}

/**
 * Fetch blog posts from Supabase with fallback.
 */
export async function getBlogPosts(): Promise<BlogPost[]> {
    try {
        if (!supabase) {
            return ALL_POSTS;
        }

        const { data, error } = await supabase
            .from('blog_posts')
            .select('*');

        if (error || !data || data.length === 0) {
            console.log('Supabase blog fetch failed or empty, using seed data.');
            return ALL_POSTS;
        }

        return data.map(p => ({
            id: p.id,
            title: p.title,
            excerpt: p.excerpt,
            content: p.content,
            author: p.author,
            date: p.date,
            readTime: p.read_time,
            image: p.image,
            tags: p.tags
        }));
    } catch (err) {
        console.error('Error fetching blog posts from Supabase:', err);
        return ALL_POSTS;
    }
}

export async function getProductById(id: string): Promise<Product | undefined> {
    const products = await getProducts();
    return products.find(p => p.id === id || p.id.replace('prod_', '') === id);
}
