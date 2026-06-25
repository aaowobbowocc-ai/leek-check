import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Noto_Sans_TC, Inter } from "next/font/google";
import { Providers } from "@/components/providers";

const notoTC = Noto_Sans_TC({
  subsets: ["latin"],
  weight: ["400", "500", "700", "900"],
  variable: "--font-tc",
  display: "swap",
});

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-en",
  display: "swap",
});

export const metadata: Metadata = {
  title: "韭菜健檢 — Leek Check",
  description: "買進前,先做一次健檢 · 4 面客觀分析,不報明牌",
  manifest: "/manifest.json",
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "韭菜健檢" },
};

export const viewport: Viewport = {
  themeColor: "#0f766e",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-Hant" className={`dark ${inter.variable} ${notoTC.variable}`}>
      <body className="antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
