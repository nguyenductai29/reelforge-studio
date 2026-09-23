import type { Metadata } from "next";
import "./style.css";
export const metadata: Metadata = { title: "ReelForge Studio", description: "Your short video studio" };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="vi"><body>{children}</body></html>;
}
