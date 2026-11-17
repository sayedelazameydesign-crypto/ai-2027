import { GoogleGenerativeAI } from "@google/generative-ai";

const apiKey = process.env.NEXT_PUBLIC_GEMINI_API_KEY || "";
const genAI = new GoogleGenerativeAI(apiKey);

export const model = genAI.getGenerativeModel({ model: "gemini-2.0-flash" });

export async function getSmartDescription(productName: string) {
  const prompt = `أنت خبير مبيعات، اكتب وصفاً تسويقياً قصيراً وجذاباً باللغة العربية لمنتج اسمه: ${productName}. اجعل الوصف يحفز على الشراء.`;
  const result = await model.generateContent(prompt);
  return result.response.text();
}