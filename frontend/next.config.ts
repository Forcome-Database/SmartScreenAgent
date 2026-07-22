import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Needed by frontend/Dockerfile, which copies .next/standalone into the
  // final runtime image.
  output: "standalone",
};

export default nextConfig;
