import { ALL_PRODUCTS } from '@/lib/seed';
import type { MetadataRoute } from 'next';

export const baseUrl = 'https://fashion-store-2026.vercel.app'; // Replace with actual domain

export default function sitemap(): MetadataRoute.Sitemap {
    const products = ALL_PRODUCTS.map((product) => ({
        url: `${baseUrl}/product/${product.id.replace('prod_', '')}`,
        lastModified: new Date(),
        changeFrequency: 'weekly' as const,
        priority: 0.8,
    }));

    return [
        {
            url: baseUrl,
            lastModified: new Date(),
            changeFrequency: 'daily',
            priority: 1,
        },
        {
            url: `${baseUrl}/shop`,
            lastModified: new Date(),
            changeFrequency: 'daily',
            priority: 0.8,
        },
        {
            url: `${baseUrl}/about`,
            lastModified: new Date(),
            changeFrequency: 'monthly',
            priority: 0.5,
        },
        ...products,
    ];
}
