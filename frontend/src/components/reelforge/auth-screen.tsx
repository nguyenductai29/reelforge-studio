"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Clapperboard, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useAuthStatus } from "@/lib/queries";
import type { LoginResult } from "@/lib/types";
import { FieldLabel } from "./primitives";
import { LanguageSwitcher } from "./language-switcher";
import { FormError, LegalFooter, TermsCheckbox, TwoFactorStep } from "./public-shell";

export function AuthScreen() {
  const { t, locale } = useI18n();
  const client = useQueryClient();
  const status = useAuthStatus();
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [terms, setTerms] = useState(false);
  const [secondStep, setSecondStep] = useState(false);

  const setup = status.data?.setup_required ?? false;
  const canRegister = !setup && (status.data?.registration_enabled ?? false);
  const mode = setup ? "setup" : register && canRegister ? "register" : "login";
  const copy = {
    setup: [t.auth.setupTitle, t.auth.setupSubtitle, t.auth.createStudio],
    register: [t.auth.registerTitle, t.auth.registerSubtitle, t.auth.createTrial],
    login: [t.auth.loginTitle, t.auth.loginSubtitle, t.auth.signIn],
  }[mode];

  async function signedIn() {
    await client.resetQueries({ queryKey: keys.dashboard });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await api<LoginResult>(
        mode,
        jsonRequest("POST", {
          email: fields.get("email"),
          password: fields.get("password"),
          ...(mode === "register" ? { workspace_name: fields.get("workspace_name") } : {}),
          ...(mode !== "login" ? { accept_terms: terms, locale } : {}),
        }),
      );
      if (result.two_factor_required) {
        setSecondStep(true);
        setBusy(false);
        return;
      }
      await signedIn();
    } catch (e) {
      setError(errorText(e, t));
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-md">
        <div className="flex items-center justify-center gap-2.5">
          <span className="brand-fill flex size-9 items-center justify-center rounded-lg">
            <Clapperboard className="size-4" />
          </span>
          <span className="font-display text-lg font-semibold tracking-tight">
            ReelForge <span className="text-muted-foreground">Studio</span>
          </span>
        </div>

        <div className="panel stage-glow mt-6 p-6 sm:p-8">
          {status.isPending ? (
            <div className="flex justify-center py-10">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : secondStep ? (
            <TwoFactorStep onDone={signedIn} onBack={() => setSecondStep(false)} />
          ) : setup && status.data?.setup_here === false ? (
            // First-run setup reaches the API only from the server itself; a public visitor gets the instructions.
            <div className="space-y-3">
              <h1 className="text-2xl font-semibold">{t.auth.setupTitle}</h1>
              <p className="text-sm text-muted-foreground">{t.auth.setupServerOnly}</p>
              <code className="block rounded-md bg-surface-2 px-3 py-2 font-mono text-xs">cd frontend &amp;&amp; npm run create-admin</code>
              <p className="text-xs text-muted-foreground">{t.auth.setupServerOnlyHint}</p>
            </div>
          ) : (
            <>
              <h1 className="text-2xl font-semibold">{copy[0]}</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">{copy[1]}</p>
              <form key={mode} onSubmit={submit} className="mt-6 space-y-4">
                {mode === "register" && (
                  <div>
                    <FieldLabel htmlFor="workspace_name">{t.auth.studioName}</FieldLabel>
                    <Input
                      id="workspace_name"
                      name="workspace_name"
                      maxLength={100}
                      required
                      placeholder={t.auth.studioPlaceholder}
                      className="bg-surface-2"
                    />
                  </div>
                )}
                <div>
                  <FieldLabel htmlFor="email">{t.auth.email}</FieldLabel>
                  <Input id="email" name="email" type="email" autoComplete="username" required
                         aria-invalid={Boolean(error)} className="bg-surface-2" />
                </div>
                {/* "Forgot password?" sits beside the label but comes after the field in the Tab order. */}
                <div className="grid grid-cols-[1fr_auto] items-baseline gap-x-2">
                  <div className="col-start-1 row-start-1">
                    <FieldLabel htmlFor="password">{t.auth.password}</FieldLabel>
                  </div>
                  <Input
                    id="password"
                    name="password"
                    type="password"
                    autoComplete={mode === "login" ? "current-password" : "new-password"}
                    minLength={mode === "login" ? undefined : 12}
                    required
                    aria-invalid={Boolean(error)}
                    className="col-span-2 row-start-2 bg-surface-2"
                  />
                  {mode === "login" && status.data?.email_delivery && (
                    <Link href="/forgot-password"
                          className="col-start-2 row-start-1 text-xs text-muted-foreground hover:text-foreground">
                      {t.auth.forgot}
                    </Link>
                  )}
                </div>
                {mode !== "login" && <TermsCheckbox checked={terms} onChange={setTerms} />}
                <FormError message={error} />
                <Button type="submit" className="w-full" disabled={busy || (mode !== "login" && !terms)}>
                  {busy ? <Loader2 className="size-4 animate-spin" /> : null}
                  {busy ? t.auth.processing : copy[2]}
                  {!busy && <ArrowRight className="size-4" />}
                </Button>
              </form>
              {canRegister && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setRegister(!register);
                    setError("");
                  }}
                  className="mt-4 w-full text-center text-sm text-muted-foreground hover:text-foreground"
                >
                  {mode === "register" ? t.auth.toLogin : t.auth.toRegister}
                </button>
              )}
              <p className="mt-6 text-center text-xs text-muted-foreground">
                {mode === "login" ? t.auth.selfHosted : t.auth.passwordHint}
              </p>
            </>
          )}
        </div>
        <LegalFooter className="mt-6" />
        <LanguageSwitcher className="mt-3 flex justify-center" />
      </div>
    </main>
  );
}
