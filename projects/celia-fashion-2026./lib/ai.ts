export async function analyzeFashionImage(imageUrl: string) {
    try {
        const response = await fetch(
            "https://api-inference.huggingface.co/models/microsoft/resnet-50",
            {
                headers: { Authorization: `Bearer ${process.env.HUGGINGFACE_API_TOKEN}` },
                method: "POST",
                body: JSON.stringify({ inputs: imageUrl }),
            }
        );
        const result = await response.json();
        return result;
    } catch (error) {
        console.error("HF Inference Error:", error);
        return null;
    }
}

export async function generateStylingAdvice(productName: string, category: string) {
    const prompt = `Act as a 2026 AI Fashion Stylist. Suggest a complete futuristic outfit for a person wearing ${productName} (${category}). include matching accessories and mood. Format as a 3-item list.`;

    try {
        const response = await fetch(
            "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2",
            {
                headers: {
                    Authorization: `Bearer ${process.env.HUGGINGFACE_API_TOKEN}`,
                    "Content-Type": "application/json"
                },
                method: "POST",
                body: JSON.stringify({
                    inputs: `<s>[INST] ${prompt} [/INST]`,
                    parameters: { max_new_tokens: 200, return_full_text: false }
                }),
            }
        );
        const result = await response.json();
        return result[0]?.generated_text || result.generated_text || "Wear with confidence and metallic accents.";
    } catch (error) {
        console.error("AI Generation Error:", error);
        return "Combine with metallic pieces for a 2026 tech-minimalist look.";
    }
}
