export const LOCALES = ["vi", "en", "ja"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "vi";
export const LOCALE_COOKIE = "rf_locale";

/** Each language is listed by its own name so it can be found from any locale. */
export const LOCALE_NAMES: Record<Locale, string> = { vi: "Tiếng Việt", en: "English", ja: "日本語" };

export const INTL_LOCALE: Record<Locale, string> = { vi: "vi-VN", en: "en-US", ja: "ja-JP" };

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}
