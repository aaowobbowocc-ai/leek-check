import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Capacitor 包 App 時用 static export
  output: process.env.NEXT_BUILD_MODE === "capacitor" ? "export" : undefined,
  images: { unoptimized: process.env.NEXT_BUILD_MODE === "capacitor" },
  typedRoutes: true,
};

export default nextConfig;
