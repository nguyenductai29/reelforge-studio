"use client";

import { Languages } from "lucide-react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { LOCALES, LOCALE_NAMES } from "@/lib/i18n/config";

/** Compact segmented control for places without the account menu (sign-in, settings). */
export function LanguageSwitcher({ className }: { className?: string }) {
  const { t, locale, setLocale } = useI18n();
  return (
    <div role="group" aria-label={t.shell.language} className={cn("inline-flex items-center gap-1", className)}>
      <Languages className="mr-1 size-3.5 text-muted-foreground" aria-hidden />
      {LOCALES.map((code) => (
        <button
          key={code}
          type="button"
          lang={code}
          aria-pressed={locale === code}
          onClick={() => setLocale(code)}
          className={cn(
            "rounded-md px-2 py-1 text-xs transition-colors",
            locale === code ? "bg-surface-2 text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {LOCALE_NAMES[code]}
        </button>
      ))}
    </div>
  );
}
