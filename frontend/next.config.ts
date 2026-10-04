import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";
import fs from "node:fs";
import path from "node:path";

// Local configuration is a JSON file, never a committed .env file.
const file = path.join(process.cwd(), "instance", "config.json");
const local = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : {};
const apiBase = local.api_base_url ?? "http://127.0.0.1:8000";
if (!/^https?:\/\//.test(apiBase)) throw new Error("api_base_url must be an absolute HTTP URL");

/**
 * Security headers of the pages (Phase 24). The API sends its own, stricter ones on /api/* (app/http_security.py),
 * so these skip /api/. Next.js inlines its bootstrap scripts, hence 'unsafe-inline' for scripts; everything else
 * the pages load comes from this origin: images (also data: QR codes and blob: previews), media, the event stream.
 * No page is ever framed. Payment and OAuth redirects are navigations, which this policy does not restrict.
 */
function securityHeaders(development: boolean) {
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${development ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "font-src 'self' data:",
    `connect-src 'self'${development ? " ws: wss:" : ""}`,
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'self'",
    "object-src 'none'",
  ].join("; ");
  const headers = [
    { key: "Content-Security-Policy", value: csp },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Permissions-Policy", value: "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ];
  // Production is served over HTTPS (Cloudflare); browsers ignore HSTS on plain HTTP anyway.
  if (!development) headers.push({ key: "Strict-Transport-Security", value: "max-age=31536000" });
  return headers;
}

const config = (phase: string): NextConfig => ({
  // A production build must not overwrite chunks used by a running dev server.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  poweredByHeader: false,
  async rewrites() { return [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }]; },
  async headers() {
    return [{ source: "/((?!api/).*)", headers: securityHeaders(phase === PHASE_DEVELOPMENT_SERVER) }];
  },
});
export default config;
