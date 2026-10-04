"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Copy, Download, KeyRound, Loader2, LogOut, MonitorSmartphone, ShieldAlert, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useAccountActivity, useAccountSecurity } from "@/lib/queries";
import type { AccountSecurity, TotpSetup } from "@/lib/types";
import { cn } from "@/lib/utils";
import { FieldLabel, StatusBadge } from "./primitives";
import { FormError } from "./public-shell";

function Card({ title, icon: Icon, children, aside, className }: {
  title: string;
  icon: typeof KeyRound;
  children: ReactNode;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("panel space-y-4 p-5", className)} aria-labelledby={`security-${title}`}>
      <div className="flex items-center justify-between gap-3">
        <h3 id={`security-${title}`} className="flex items-center gap-2 text-sm font-semibold">
          <Icon className="size-4 text-muted-foreground" aria-hidden />
          {title}
        </h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function download(name: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useI18n();
  const s = t.account.twoFactor;
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-3 rounded-lg border border-warning/40 bg-warning/10 p-4">
      <p className="text-sm font-medium">{s.recoveryTitle}</p>
      <p className="text-xs text-muted-foreground">{s.recoveryHint}</p>
      <ul className="grid grid-cols-2 gap-1.5 font-mono text-sm" aria-label={s.recoveryTitle}>
        {codes.map((code) => <li key={code} className="rounded bg-surface-2 px-2 py-1 text-center">{code}</li>)}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => {
          void navigator.clipboard?.writeText(codes.join("\n"));
          setCopied(true);
        }}>
          <Copy className="size-3.5" />{copied ? s.copied : s.copy}
        </Button>
        <Button size="sm" variant="outline" onClick={() => download("reelforge-recovery-codes.txt", `${codes.join("\n")}\n`)}>
          <Download className="size-3.5" />{s.download}
        </Button>
        <Button size="sm" onClick={onDone}><CheckCircle2 className="size-3.5" />{s.saved}</Button>
      </div>
    </div>
  );
}

function TwoFactorCard({ data }: { data: AccountSecurity }) {
  const { t, formatDateTime } = useI18n();
  const s = t.account.twoFactor;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [step, setStep] = useState<"idle" | "password" | "scan" | "change">("idle");
  const [change, setChange] = useState<"disable" | "regenerate">("disable");
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const enabled = data.two_factor.enabled;

  async function refresh() {
    await Promise.all([client.invalidateQueries({ queryKey: keys.account }),
                       client.invalidateQueries({ queryKey: keys.dashboard })]);
  }

  async function run(event: FormEvent<HTMLFormElement>, action: () => Promise<void>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  }

  const reset = () => {
    setStep("idle");
    setError("");
    setSetup(null);
  };

  return (
    <Card title={s.title} icon={ShieldCheck}
          aside={<StatusBadge status={enabled ? "completed" : "draft"} label={enabled ? s.on : s.off} />}>
      <p className="text-sm text-muted-foreground">{s.hint}</p>
      {data.is_admin && !enabled && (
        <p className="flex items-center gap-2 text-sm text-warning"><ShieldAlert className="size-4" />{s.adminRecommended}</p>
      )}
      {enabled && (
        <p className="text-xs text-muted-foreground">
          {data.two_factor.enabled_at ? s.enabledAt(formatDateTime(data.two_factor.enabled_at)) : null}
          {" · "}
          {s.remaining(data.two_factor.recovery_codes_remaining)}
        </p>
      )}
      {codes && <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />}

      {step === "idle" && !codes && (
        <div className="flex flex-wrap gap-2">
          {enabled ? (
            <>
              <Button size="sm" variant="outline" onClick={() => { setChange("regenerate"); setStep("change"); }}>{s.regenerate}</Button>
              <Button size="sm" variant="outline" onClick={() => { setChange("disable"); setStep("change"); }}>{s.disable}</Button>
            </>
          ) : (
            <Button size="sm" onClick={() => setStep("password")}>{s.enable}</Button>
          )}
        </div>
      )}

      {step === "password" && (
        <form className="max-w-sm space-y-3" onSubmit={(e) => void run(e, async () => {
          const password = new FormData(e.currentTarget).get("password");
          setSetup(await api<TotpSetup>("account/2fa/setup", jsonRequest("POST", { password })));
          setStep("scan");
        })}>
          <div>
            <FieldLabel htmlFor="totp-password">{s.passwordPrompt}</FieldLabel>
            <Input id="totp-password" name="password" type="password" autoComplete="current-password" required className="bg-surface-2" />
          </div>
          <FormError message={error} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={busy}>{busy && <Loader2 className="size-3.5 animate-spin" />}{s.start}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={reset}>{s.cancel}</Button>
          </div>
        </form>
      )}

      {step === "scan" && setup && (
        <form className="space-y-3" onSubmit={(e) => void run(e, async () => {
          const code = new FormData(e.currentTarget).get("code");
          const result = await api<{ recovery_codes: string[] }>("account/2fa/enable", jsonRequest("POST", { code }));
          setCodes(result.recovery_codes);
          reset();
          toast.success(s.enabled);
          await refresh();
        })}>
          <p className="text-sm">{s.scan}</p>
          <div className="flex flex-wrap items-start gap-4">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={setup.qr} alt={s.qrAlt} width={180} height={180} className="rounded-lg bg-white p-1" />
            <div className="min-w-0 space-y-1 text-xs">
              <p className="text-muted-foreground">{s.manual}</p>
              <code className="block break-all rounded bg-surface-2 px-2 py-1 font-mono">{setup.secret}</code>
            </div>
          </div>
          <div className="max-w-xs">
            <FieldLabel htmlFor="totp-code">{s.code}</FieldLabel>
            <Input id="totp-code" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}"
                   required className="bg-surface-2 font-mono tracking-widest" />
          </div>
          <FormError message={error} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={busy}>{busy && <Loader2 className="size-3.5 animate-spin" />}{s.confirm}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={reset}>{s.cancel}</Button>
          </div>
        </form>
      )}

      {step === "change" && (
        <form className="max-w-sm space-y-3" onSubmit={(e) => void run(e, async () => {
          const form = new FormData(e.currentTarget);
          const body = { password: form.get("password"), code: form.get("code") };
          if (change === "disable") {
            await api("account/2fa/disable", jsonRequest("POST", body));
            toast.success(s.disabled);
          } else {
            setCodes((await api<{ recovery_codes: string[] }>("account/2fa/recovery-codes", jsonRequest("POST", body))).recovery_codes);
          }
          reset();
          await refresh();
        }).catch(showError)}>
          <p className="text-sm font-medium">{s.confirmTitle}</p>
          <div>
            <FieldLabel htmlFor="change-password">{t.account.password.current}</FieldLabel>
            <Input id="change-password" name="password" type="password" autoComplete="current-password" required className="bg-surface-2" />
          </div>
          <div>
            <FieldLabel htmlFor="change-code">{s.codeOrRecovery}</FieldLabel>
            <Input id="change-code" name="code" autoComplete="one-time-code" required minLength={6} maxLength={20}
                   className="bg-surface-2 font-mono" />
          </div>
          <FormError message={error} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant={change === "disable" ? "destructive" : "default"} disabled={busy}>
              {busy && <Loader2 className="size-3.5 animate-spin" />}
              {change === "disable" ? s.disable : s.regenerate}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={reset}>{s.cancel}</Button>
          </div>
        </form>
      )}
    </Card>
  );
}

export function AccountSecurityPanel() {
  const { t, formatDateTime } = useI18n();
  const a = t.account;
  const client = useQueryClient();
  const showError = useErrorToast();
  const security = useAccountSecurity();
  const activity = useAccountActivity();
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [closure, setClosure] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  if (security.isPending) return <Loader2 className="size-4 animate-spin text-muted-foreground" />;
  if (security.isError) return <p role="alert" className="text-sm text-destructive">{errorText(security.error, t)}</p>;
  const data = security.data;

  async function act(name: string, action: () => Promise<void>) {
    setBusy(name);
    try {
      await action();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const body = { current_password: form.get("current"), new_password: form.get("next"), confirm_password: form.get("confirm") };
    if (body.new_password !== body.confirm_password) {
      setPasswordError(a.password.mismatch);
      return;
    }
    setPasswordBusy(true);
    setPasswordError("");
    try {
      const result = await api<{ sessions_revoked: number }>("account/password", jsonRequest("POST", body));
      toast.success(a.password.changed(result.sessions_revoked));
      formElement.reset();
      await client.invalidateQueries({ queryKey: keys.account });
    } catch (e) {
      setPasswordError(errorText(e, t));
    } finally {
      setPasswordBusy(false);
    }
  }

  const others = data.sessions.filter((session) => !session.current);
  return (
    // Two columns on a laptop; three from 1536 px: email, password and 2FA side by side, the sessions across.
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
      <Card title={a.email.title} icon={KeyRound}
            aside={<StatusBadge status={data.email_verified ? "completed" : "blocked"}
                                label={data.email_verified ? a.email.verified : a.email.unverified} />}>
        <p className="break-all text-sm">{data.email}</p>
        {!data.email_verified && (data.email_delivery ? (
          <Button size="sm" variant="outline" disabled={busy === "resend"} onClick={() => void act("resend", async () => {
            await api("account/verify-email/resend", { method: "POST" });
            toast.success(a.email.resent);
          })}>
            {busy === "resend" && <Loader2 className="size-3.5 animate-spin" />}{a.email.resend}
          </Button>
        ) : <p className="text-xs text-muted-foreground">{a.email.noDelivery}</p>)}
      </Card>

      <Card title={a.password.title} icon={KeyRound}>
        {data.password_changed_at && (
          <p className="text-xs text-muted-foreground">{a.password.lastChanged(formatDateTime(data.password_changed_at))}</p>
        )}
        <form className="space-y-3" onSubmit={changePassword}>
          <div>
            <FieldLabel htmlFor="current">{a.password.current}</FieldLabel>
            <Input id="current" name="current" type="password" autoComplete="current-password" required className="bg-surface-2" />
          </div>
          <div>
            <FieldLabel htmlFor="next">{a.password.next}</FieldLabel>
            <Input id="next" name="next" type="password" autoComplete="new-password" minLength={12} required
                   aria-describedby="next-hint" className="bg-surface-2" />
            <p id="next-hint" className="mt-1 text-xs text-muted-foreground">{a.password.hint}</p>
          </div>
          <div>
            <FieldLabel htmlFor="confirm">{a.password.confirm}</FieldLabel>
            <Input id="confirm" name="confirm" type="password" autoComplete="new-password" minLength={12} required className="bg-surface-2" />
          </div>
          <FormError message={passwordError} />
          <Button type="submit" size="sm" disabled={passwordBusy}>
            {passwordBusy && <Loader2 className="size-3.5 animate-spin" />}{a.password.submit}
          </Button>
        </form>
      </Card>

      {/* A grid of one, so the card stretches to the row like the email and password cards beside it. */}
      <div className="grid lg:col-span-2 2xl:col-span-1"><TwoFactorCard data={data} /></div>

      <div className="lg:col-span-2 2xl:col-span-3">
        <Card title={a.sessions.title} icon={MonitorSmartphone}
              aside={others.length > 0 && (
                <Button size="sm" variant="outline" disabled={busy === "others"} onClick={() => void act("others", async () => {
                  const result = await api<{ revoked: number }>("account/sessions/revoke-others", { method: "POST" });
                  toast.success(a.sessions.revoked(result.revoked));
                  await client.invalidateQueries({ queryKey: keys.account });
                })}>
                  <LogOut className="size-3.5" />{a.sessions.revokeOthers}
                </Button>
              )}>
          <ul className="divide-y divide-border">
            {data.sessions.map((session) => (
              <li key={session.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                <div className="min-w-0 text-sm">
                  <p className="truncate">
                    {session.user_agent || a.sessions.unknownDevice}
                    {session.current && <span className="ml-2 rounded bg-primary/15 px-1.5 py-0.5 text-[11px] text-primary">{a.sessions.current}</span>}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {session.ip ?? "—"}
                    {session.last_seen_at && ` · ${a.sessions.lastSeen(formatDateTime(session.last_seen_at))}`}
                    {session.created_at && ` · ${a.sessions.created(formatDateTime(session.created_at))}`}
                  </p>
                </div>
                {!session.current && (
                  <Button size="sm" variant="ghost" disabled={busy === session.id} onClick={() => void act(session.id, async () => {
                    await api(`account/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
                    toast.success(a.sessions.revoked(1));
                    await client.invalidateQueries({ queryKey: keys.account });
                  })}>
                    {a.sessions.revoke}
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {others.length === 0 && <p className="text-xs text-muted-foreground">{a.sessions.none}</p>}
        </Card>
      </div>

      <Card title={a.activity.title} icon={ShieldCheck} className="2xl:col-span-2">
        {activity.isPending ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> :
          activity.isError ? <p className="text-sm text-destructive">{errorText(activity.error, t)}</p> :
          activity.data.length === 0 ? <p className="text-sm text-muted-foreground">{a.activity.empty}</p> : (
            // No scrolling box of its own: the Security tab already scrolls, and one scrollbar is enough.
            <ul className="space-y-2 text-sm" aria-label={a.activity.title}>
              {activity.data.map((item, index) => (
                <li key={`${item.at}-${index}`} className="flex items-start justify-between gap-3">
                  <span className="min-w-0">
                    <span className={cn(item.outcome !== "success" && "text-destructive")}>
                      {t.auditActions[item.action] ?? item.action}
                    </span>
                    <span className="block text-xs text-muted-foreground">{formatDateTime(item.at)}{item.ip ? ` · ${item.ip}` : ""}</span>
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">{a.activity.outcomes[item.outcome]}</span>
                </li>
              ))}
            </ul>
          )}
      </Card>

      <Card title={a.data.title} icon={Download}>
        <div className="space-y-2">
          <Button size="sm" variant="outline" disabled={busy === "export"} onClick={() => void act("export", async () => {
            const exported = await api<Record<string, unknown>>("account/export");
            download(`reelforge-account-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(exported, null, 2), "application/json");
          })}>
            {busy === "export" ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}{a.data.export}
          </Button>
          <p className="text-xs text-muted-foreground">{a.data.exportHint}</p>
        </div>
        <div className="space-y-2 border-t border-border pt-3">
          <p className="text-sm font-medium">{a.data.closure}</p>
          <p className="text-xs text-muted-foreground">{a.data.closureHint}</p>
          <FieldLabel htmlFor="closure-reason">{a.data.closureReason}</FieldLabel>
          <Textarea id="closure-reason" value={closure} onChange={(e) => setClosure(e.target.value)} maxLength={2000}
                    className="min-h-[70px] bg-surface-2" />
          <Button size="sm" variant="outline" disabled={busy === "closure"} onClick={() => void act("closure", async () => {
            await api("account/closure-request", jsonRequest("POST", { reason: closure || null }));
            setClosure("");
            toast.success(a.data.closureSent);
          })}>
            {busy === "closure" && <Loader2 className="size-3.5 animate-spin" />}{a.data.send}
          </Button>
        </div>
        <div className="space-y-2 border-t border-border pt-3 text-sm">
          <p className="font-medium">{a.terms.title}</p>
          {data.terms.version === data.terms.current_version && data.terms.accepted_at ? (
            <p className="text-xs text-muted-foreground">{a.terms.accepted(data.terms.version, formatDateTime(data.terms.accepted_at))}</p>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-warning">{a.terms.notAccepted}</p>
              <Button size="sm" variant="outline" disabled={busy === "terms"} onClick={() => void act("terms", async () => {
                await api("account/terms", { method: "POST" });
                await Promise.all([client.invalidateQueries({ queryKey: keys.account }),
                                   client.invalidateQueries({ queryKey: keys.dashboard })]);
              })}>{a.terms.accept}</Button>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
