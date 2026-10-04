import type { Metadata } from "next";
import { cookies } from "next/headers";
import "@fontsource-variable/dm-sans/opsz.css";
import "@fontsource-variable/space-grotesk/wght.css";
import "@fontsource/be-vietnam-pro/400.css";
import "@fontsource/be-vietnam-pro/500.css";
import "@fontsource/be-vietnam-pro/600.css";
import "@fontsource/be-vietnam-pro/700.css";
import "@xyflow/react/dist/style.css";
import "./globals.css";
import { DEFAULT_LOCALE, LOCALE_COOKIE, isLocale } from "@/lib/i18n/config";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "ReelForge Studio",
  description: "AI content production workspace for social media creators — script, generate, review and publish.",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const stored = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(stored) ? stored : DEFAULT_LOCALE;
  return (
    <html lang={locale} className="dark">
      <body>
        <Providers locale={locale}>{children}</Providers>
      </body>
    </html>
  );
}
