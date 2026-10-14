import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  outputFileTracingRoot: __dirname,
};
export default nextConfig;
