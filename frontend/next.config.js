/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    optimizePackageImports: ["@assistant-ui/react"],
  },
  async rewrites() {
    const apiUrl = process.env.BACKEND_URL || "http://localhost:8001";
    return [
      {
        source: "/assistant/:path*",
        destination: `${apiUrl}/assistant/:path*`,
      },
    ];
  },
};

export default nextConfig;
