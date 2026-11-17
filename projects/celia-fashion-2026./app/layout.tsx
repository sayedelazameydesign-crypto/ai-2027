import type { Metadata } from "next";
import { Inter, Space_Grotesk, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const spaceGrotesk = Space_Grotesk({
  variable: "--font-space-grotesk",
  subsets: ["latin"],
});

const mono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "CELIA 2026 | Future Core Fashion",
  description: "AI-Powered, Ultra-Minimalist Fashion Store for the 2026 Era. Designed with precision, worn with intent.",
};

import { AIChatAssistant } from "@/components/fashion/AIChatAssistant";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ar" dir="rtl" className="dark" style={{ colorScheme: 'dark' }}>
      <body
        className={`${inter.variable} ${spaceGrotesk.variable} ${mono.variable} antialiased transition-colors duration-500`}
      >
        {children}
        <AIChatAssistant />
      </body>
    </html>
  );
}
