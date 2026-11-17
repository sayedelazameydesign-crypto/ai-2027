export const dynamic = 'force-dynamic';

import { getProductById } from "@/lib/actions";
import { Metadata } from "next";
import ProductStory from "./ProductStory";
import { notFound } from "next/navigation";

type Props = {
    params: Promise<{ id: string }>
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
    const { id } = await params;
    const product = await getProductById(id);

    if (!product) return { title: "Product Not Found" };

    return {
        title: `${product.name} | CELIA 2026`,
        description: product.description,
        openGraph: {
            title: product.name,
            description: product.description,
            images: [
                {
                    url: product.image,
                    width: 800,
                    height: 600,
                    alt: product.name,
                },
            ],
            type: 'website',
        },
    };
}

export default async function ProductPage({ params }: Props) {
    const { id } = await params;
    const product = await getProductById(id);

    if (!product) {
        notFound();
    }

    // JSON-LD Schema
    const jsonLd = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        name: product.name,
        image: product.image,
        description: product.description,
        sku: product.id,
        offers: {
            '@type': 'Offer',
            price: product.price,
            priceCurrency: 'EGP',
            availability: (product.stock ?? 0) > 0 ? 'https://schema.org/InStock' : 'https://schema.org/OutOfStock',
        },
        aggregateRating: {
            '@type': 'AggregateRating',
            ratingValue: product.rating,
            reviewCount: 42
        }
    };

    return (
        <main className="min-h-screen bg-black">
            <script
                type="application/ld+json"
                dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
            />
            <ProductStory initialProduct={product} />
        </main>
    );
}
