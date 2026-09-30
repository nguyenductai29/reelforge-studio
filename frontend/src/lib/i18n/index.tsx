"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { INTL_LOCALE, LOCALE_COOKIE, type Locale } from "./config";
import { vi, type Dictionary } from "./vi";
import { en } from "./en";
import { ja } from "./ja";

const dictionaries: Record<Locale, Dictionary> = { vi, en, ja };

type DateInput = string | number | Date;

/** Some databases return UTC timestamps without an offset; read those as UTC, not local time. */
export function toDate(input: DateInput): Date {
  if (typeof input === "string" && /^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(input)) return new Date(`${input}Z`);
  return new Date(input);
}

type I18n = {
  locale: Locale;
  t: Dictionary;
  setLocale: (locale: Locale) => void;
  formatDate: (value: DateInput, options?: Intl.DateTimeFormatOptions) => string;
  formatDateTime: (value: DateInput) => string;
  formatNumber: (value: number) => string;
  formatMoney: (vnd: number) => string;
  formatRelative: (value: DateInput) => string;
};

const I18nContext = createContext<I18n | null>(null);

const RELATIVE_STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["second", 60],
  ["minute", 60],
  ["hour", 24],
  ["day", 7],
  ["week", 4.35],
  ["month", 12],
  ["year", Number.POSITIVE_INFINITY],
];

export function I18nProvider({ initialLocale, children }: { initialLocale: Locale; children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale) => {
    // The cookie lets the server render the next page load in the chosen language.
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax`;
    document.documentElement.lang = next;
    setLocaleState(next);
  }, []);

  const value = useMemo<I18n>(() => {
    const tag = INTL_LOCALE[locale];
    const dateTime = new Intl.DateTimeFormat(tag, { dateStyle: "medium", timeStyle: "short" });
    const number = new Intl.NumberFormat(tag);
    const money = new Intl.NumberFormat(tag, { style: "currency", currency: "VND" });
    const relative = new Intl.RelativeTimeFormat(tag, { numeric: "auto" });
    return {
      locale,
      t: dictionaries[locale],
      setLocale,
      formatDate: (input, options = { dateStyle: "medium" }) => new Intl.DateTimeFormat(tag, options).format(toDate(input)),
      formatDateTime: (input) => dateTime.format(toDate(input)),
      formatNumber: (n) => number.format(n),
      formatMoney: (vnd) => money.format(vnd),
      formatRelative: (input) => {
        let amount = (toDate(input).getTime() - Date.now()) / 1000;
        for (const [unit, size] of RELATIVE_STEPS) {
          if (Math.abs(amount) < size) return relative.format(Math.round(amount), unit);
          amount /= size;
        }
        return dateTime.format(toDate(input));
      },
    };
  }, [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}
