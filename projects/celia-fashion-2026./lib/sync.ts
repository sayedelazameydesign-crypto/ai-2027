import { supabase } from './supabase';
import { ALL_PRODUCTS, ALL_POSTS } from './seed';

/**
 * Script to sync seed data to Supabase
 * You can call this from an admin route or use it as a one-time setup
 */
export async function syncSeedToSupabase() {
    console.log('Starting sync...');
    if (!supabase) return { success: false, error: 'Supabase client missing' };

    // 1. Sync Products
    const productsToSync = ALL_PRODUCTS.map(p => ({
        id: p.id.includes('prod_') ? undefined : p.id, // Let Supabase handle UUIDs if it's mock id
        name: p.name,
        price: p.price,
        category: p.category,
        image: p.image,
        description: p.description,
        rating: p.rating,
        stock: p.stock,
        is_trend: p.isTrend,
        ai_summary: p.ai_summary
    }));

    const { error: pError } = await supabase
        .from('products')
        .upsert(productsToSync, { onConflict: 'name' });

    if (pError) console.error('Products Sync Error:', pError);
    else console.log('Products synced successfully!');

    // 2. Sync Blog Posts
    const postsToSync = ALL_POSTS.map(p => ({
        id: p.id.includes('blog_') ? undefined : p.id,
        title: p.title,
        excerpt: p.excerpt,
        content: p.content,
        author: p.author,
        date: p.date,
        read_time: p.readTime,
        image: p.image,
        tags: p.tags
    }));

    const { error: bError } = await supabase
        .from('blog_posts')
        .upsert(postsToSync, { onConflict: 'title' });

    if (bError) console.error('Blog Sync Error:', bError);
    else console.log('Blog posts synced successfully!');

    return { success: !pError && !bError };
}
