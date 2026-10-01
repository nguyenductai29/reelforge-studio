"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FieldLabel } from "@/components/reelforge/primitives";
import { FormError, FormNote, PublicShell } from "@/components/reelforge/public-shell";
import { api, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useAuthStatus } from "@/lib/queries";

/** The same answer whether or not the account exists (the API never says). */
export default function ForgotPasswordPage() {
  const { t } = useI18n();
  const p = t.publicPages.forgot;
  useDocumentTitle(p.title);
  const status = useAuthStatus();
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("account/password/forgot", jsonRequest("POST", { email: new FormData(event.currentTarget).get("email") }));
      setSent(true);
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PublicShell>
      <h1 className="text-2xl font-semibold">{p.title}</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">{p.subtitle}</p>
      {status.data && status.data.email_delivery === false && (
        <div className="mt-4"><FormNote tone="warning">{p.noDelivery}</FormNote></div>
      )}
      {sent ? (
        <div className="mt-6"><FormNote tone="success">{p.sent}</FormNote></div>
      ) : (
        <form onSubmit={submit} className="mt-6 space-y-4">
          <div>
            <FieldLabel htmlFor="email">{t.auth.email}</FieldLabel>
            <Input id="email" name="email" type="email" autoComplete="username" required className="bg-surface-2" />
          </div>
          <FormError message={error} />
          <Button type="submit" className="w-full" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {p.submit}
          </Button>
        </form>
      )}
      <Link href="/" className="mt-5 block text-center text-sm text-muted-foreground hover:text-foreground">
        {t.publicPages.backToSignIn}
      </Link>
    </PublicShell>
  );
}
