"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Clapperboard, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { LoginResult } from "@/lib/types";
import { cn } from "@/lib/utils";
import { LanguageSwitcher } from "./language-switcher";
import { FieldLabel } from "./primitives";

/** Pages reachable without signing in (Phase 22/26): a centered card, the legal links, the language switcher. */
export function PublicShell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <main className="flex min-h-screen flex-col items-center bg-background px-4 py-10">
      <div className={cn("w-full", wide ? "max-w-3xl" : "max-w-md")}>
        <Link href="/" className="flex items-center justify-center gap-2.5">
          <span className="brand-fill flex size-9 items-center justify-center rounded-lg">
            <Clapperboard className="size-4" />
          </span>
          <span className="font-display text-lg font-semibold tracking-tight">
            ReelForge <span className="text-muted-foreground">Studio</span>
          </span>
        </Link>
        <div className="panel stage-glow mt-6 p-6 sm:p-8">{children}</div>
        <LegalFooter className="mt-6" />
        <LanguageSwitcher className="mt-3 flex justify-center" />
      </div>
    </main>
  );
}

export function LegalFooter({ className }: { className?: string }) {
  const { t } = useI18n();
  return (
    <nav aria-label={t.legal.links.terms} className={cn("flex justify-center gap-4 text-xs text-muted-foreground", className)}>
      <Link href="/terms" className="hover:text-foreground">{t.legal.links.terms}</Link>
      <Link href="/privacy" className="hover:text-foreground">{t.legal.links.privacy}</Link>
    </nav>
  );
}

/** The one-time token of an emailed link: it travels in the fragment (#token=…), so no server log ever sees it.
 * ``undefined`` until the browser has read it, then the token or ``null``. */
export function useHashToken(): string | null | undefined {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    const found = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("token");
    setToken(found && found.length >= 10 ? found : null);
  }, []);
  return token;
}

export function FormError({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-lg border border-destructive/40 bg-[color-mix(in_oklab,var(--destructive)_10%,transparent)] px-3 py-2 text-sm text-destructive">
      {message}
    </p>
  );
}

export function FormNote({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "success" | "warning" }) {
  return (
    <p role="status" className={cn("rounded-lg border px-3 py-2 text-sm",
      tone === "success" && "border-success/40 bg-success/10",
      tone === "warning" && "border-warning/40 bg-warning/10",
      tone === "muted" && "border-border bg-surface-2 text-muted-foreground")}>
      {children}
    </p>
  );
}

/** "I agree to the Terms of Service and Privacy Policy" (links open in a new tab); required to register. */
export function TermsCheckbox({ checked, onChange }: { checked: boolean; onChange: (value: boolean) => void }) {
  const { t } = useI18n();
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" required checked={checked} onChange={(e) => onChange(e.target.checked)}
             className="mt-0.5 size-4 shrink-0" name="accept_terms" />
      <span>
        {t.auth.agreePrefix}{" "}
        <Link href="/terms" target="_blank" className="text-primary underline-offset-2 hover:underline">{t.auth.termsLink}</Link>{" "}
        {t.auth.and}{" "}
        <Link href="/privacy" target="_blank" className="text-primary underline-offset-2 hover:underline">{t.auth.privacyLink}</Link>
      </span>
    </label>
  );
}

/** The second sign-in step: an authenticator code or a recovery code (POST /api/login/2fa). */
export function TwoFactorStep({ onDone, onBack }: { onDone: (result: LoginResult) => void | Promise<void>; onBack: () => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = String(new FormData(event.currentTarget).get("code") ?? "").trim();
    setBusy(true);
    setError("");
    try {
      await onDone(await api<LoginResult>("login/2fa", jsonRequest("POST", { code })));
    } catch (e) {
      setError(errorText(e, t));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">{t.auth.twoFactorTitle}</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">{t.auth.twoFactorSubtitle}</p>
      </div>
      <div>
        <FieldLabel htmlFor="code">{t.auth.code}</FieldLabel>
        <Input id="code" name="code" autoComplete="one-time-code" inputMode="text" autoFocus required minLength={6}
               maxLength={20} aria-invalid={Boolean(error)} className="bg-surface-2 font-mono tracking-widest" />
      </div>
      <FormError message={error} />
      <Button type="submit" className="w-full" disabled={busy}>
        {busy && <Loader2 className="size-4 animate-spin" />}
        {t.auth.verify}
      </Button>
      <button type="button" onClick={onBack} className="w-full text-center text-sm text-muted-foreground hover:text-foreground">
        {t.auth.back}
      </button>
    </form>
  );
}
