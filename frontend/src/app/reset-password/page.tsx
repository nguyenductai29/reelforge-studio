"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FieldLabel } from "@/components/reelforge/primitives";
import { FormError, FormNote, PublicShell, useHashToken } from "@/components/reelforge/public-shell";
import { api, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

/** The emailed link (#token=…, 60 minutes, once): a new password; every session of the account ends. */
export default function ResetPasswordPage() {
  const { t } = useI18n();
  const p = t.publicPages.reset;
  useDocumentTitle(p.title);
  const token = useHashToken();
  const [valid, setValid] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (token === undefined) return;
    if (token === null) {
      setValid(false);
      return;
    }
    api<{ valid: boolean }>("account/password/reset/check", jsonRequest("POST", { token }))
      .then((result) => setValid(result.valid))
      .catch(() => setValid(false));
  }, [token]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const confirm = String(form.get("confirm") ?? "");
    if (password !== confirm) {
      setError(t.account.password.mismatch);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("account/password/reset", jsonRequest("POST", { token, password, confirm_password: confirm }));
      setDone(true);
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PublicShell>
      <h1 className="text-2xl font-semibold">{p.title}</h1>
      {valid === null ? (
        <p className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {p.checking}
        </p>
      ) : done ? (
        <div className="mt-6 space-y-4">
          <FormNote tone="success">{p.done}</FormNote>
          <Button asChild className="w-full"><Link href="/">{t.auth.signIn}</Link></Button>
        </div>
      ) : !valid ? (
        <div className="mt-6 space-y-4">
          <FormNote tone="warning">{p.invalid}</FormNote>
          <Button asChild variant="outline" className="w-full"><Link href="/forgot-password">{p.requestNew}</Link></Button>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-6 space-y-4">
          <div>
            <FieldLabel htmlFor="password">{p.newPassword}</FieldLabel>
            <Input id="password" name="password" type="password" autoComplete="new-password" minLength={12} required
                   className="bg-surface-2" aria-describedby="password-hint" />
            <p id="password-hint" className="mt-1 text-xs text-muted-foreground">{t.account.password.hint}</p>
          </div>
          <div>
            <FieldLabel htmlFor="confirm">{p.confirm}</FieldLabel>
            <Input id="confirm" name="confirm" type="password" autoComplete="new-password" minLength={12} required
                   className="bg-surface-2" />
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
