"use client";
import CategoryView from "@/components/store/CategoryView";
import { useParams } from "next/navigation";

export default function DynamicCategoryPage() {
    const params = useParams();
    const category = params?.category as string || "all";
    return <CategoryView category={category} />;
}
