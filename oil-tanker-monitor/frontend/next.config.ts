import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.PLAYWRIGHT_TESTING ? '.next-playwright' : '.next',
};

export default nextConfig;
