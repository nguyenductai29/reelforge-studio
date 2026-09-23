import type { Metadata } from "next";
import localFont from "next/font/local";
import "@xyflow/react/dist/style.css";
import "./style.css";
import "./control-room.css";
import "./workspace-layout.css";

const bebasNeue = localFont({
  src: "./fonts/BebasNeue-Regular.woff2",
  weight: "400",
  style: "normal",
  display: "swap",
  variable: "--font-bebas-neue",
  // Keep the following family in the CSS stack available for missing glyphs.
  adjustFontFallback: false,
});

const barlow = localFont({
  src: [
    { path: "./fonts/Barlow-Regular.woff2", weight: "400", style: "normal" },
    { path: "./fonts/Barlow-Medium.woff2", weight: "500", style: "normal" },
    { path: "./fonts/Barlow-SemiBold.woff2", weight: "600", style: "normal" },
    { path: "./fonts/Barlow-Bold.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
  variable: "--font-barlow",
});

export const metadata: Metadata = { title: "ReelForge Studio", description: "Your short video studio" };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="vi" className={`${bebasNeue.variable} ${barlow.variable}`}><body>{children}</body></html>;
}
