import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";
import fs from "node:fs";
import path from "node:path";

// Local configuration is a JSON file, never a committed .env file.
const file = path.join(process.cwd(), "instance", "config.json");
const local = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : {};
const apiBase = local.api_base_url ?? "http://127.0.0.1:8000";
if (!/^https?:\/\//.test(apiBase)) throw new Error("api_base_url must be an absolute HTTP URL");
const config = (phase: string): NextConfig => ({
  // A production build must not overwrite chunks used by a running dev server.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  async rewrites() { return [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }]; },
});
export default config;
