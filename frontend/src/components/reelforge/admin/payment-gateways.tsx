"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Copy, Eraser, Loader2, ShieldCheck, Undo2 } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, api, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, usePaymentSetup } from "@/lib/queries";
import { TransferCard } from "../bank-transfer";
import type { BankTransfer, PaymentCheck, PaymentField, PaymentMode, PaymentSetup, SecretUpdate } from "@/lib/types";
import { cn } from "@/lib/utils";

const FIELD_ORDER = {
  payos: ["client_id", "api_key", "checksum_key"],
  onepay: ["merchant_id", "access_code", "hash_key", "query_user", "query_password"],
} as const;
const MODES: PaymentMode[] = ["sandbox", "production", "custom"];
const CHECK_TONE: Record<string, string> = {
  ok: "text-success",
  warning: "text-warning",
  error: "text-destructive",
  skipped: "text-muted-foreground",
  unsupported: "text-muted-foreground",
};

function CopyUrl({ url }: { url: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-1.5">
      <code className="min-w-0 flex-1 truncate rounded bg-surface-2 px-2 py-1 text-xs" title={url}>{url}</code>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-7 shrink-0"
        aria-label={t.admin.gateways.copy}
        onClick={() => {
          void navigator.clipboard?.writeText(url);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
      </Button>
    </div>
  );
}

/** The persistent mode badge: SANDBOX or PRODUCTION is always visible on the card gateway. */
export function ModeBadge({ mode }: { mode: PaymentMode | null }) {
  const { t } = useI18n();
  if (!mode) return null;
  return (
    <span
      className={cn(
        "rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider",
        mode === "production" ? "bg-destructive/15 text-destructive" : mode === "sandbox" ? "bg-info/15 text-info"
          : "bg-warning/15 text-warning",
      )}
    >
      {t.admin.gateways.modeBadge[mode]}
    </span>
  );
}

/**
 * A credential input that never shows a saved value: empty means "keep what is saved". Typing replaces it;
 * optional fields can be cleared explicitly.
 */
function SecretField({ id, label, field, update, onChange, adminSource }: {
  id: string;
  label: string;
  field: PaymentField;
  update: SecretUpdate;
  onChange: (update: SecretUpdate) => void;
  adminSource: boolean;
}) {
  const { t } = useI18n();
  const g = t.admin.gateways;
  const cleared = update.action === "clear";
  const placeholder = field.configured && adminSource
    ? (field.secret ? g.savedSecret : g.savedIdentifier(field.masked ?? ""))
    : field.configured ? g.legacyValue(field.masked ?? "••••••••") : g.notSet;
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="flex items-center justify-between gap-2 text-xs font-medium">
        <span>
          {label}
          {field.required ? <span className="text-destructive"> *</span> : <span className="text-muted-foreground"> ({g.optional})</span>}
        </span>
        <span className={cn("text-[11px] font-normal", field.status === "configured" ? "text-success"
          : field.status === "invalid" ? "text-destructive" : "text-muted-foreground")}>
          {g.fieldStatus[field.status]}
        </span>
      </label>
      <div className="flex items-center gap-1.5">
        <Input
          id={id}
          type={field.secret ? "password" : "text"}
          autoComplete={field.secret ? "new-password" : "off"}
          spellCheck={false}
          maxLength={512}
          disabled={cleared}
          value={update.action === "replace" ? update.value : ""}
          onChange={(e) => onChange(e.target.value ? { action: "replace", value: e.target.value } : { action: "keep" })}
          placeholder={cleared ? g.willClear : placeholder}
          className="h-8 bg-surface-2 font-mono text-xs"
        />
        {!field.required && field.configured && adminSource && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-8 shrink-0"
            aria-label={cleared ? g.undoClear : g.clear}
            title={cleared ? g.undoClear : g.clear}
            onClick={() => onChange(cleared ? { action: "keep" } : { action: "clear" })}
          >
            {cleared ? <Undo2 className="size-3.5" /> : <Eraser className="size-3.5" />}
          </Button>
        )}
      </div>
      {!adminSource && field.configured && <p className="text-[11px] text-muted-foreground">{g.legacyHint(field.legacy)}</p>}
    </div>
  );
}

function GatewayPanel({ setup, encryption, useForVietqr = false }: {
  setup: PaymentSetup;
  encryption: { available: boolean; variable: string };
  /** In the VietQR tab: saving payOS also makes it the VietQR mode. */
  useForVietqr?: boolean;
}) {
  const { t, formatDateTime } = useI18n();
  const g = t.admin.gateways;
  const client = useQueryClient();
  const provider = setup.provider as "payos" | "onepay";
  const fieldNames = FIELD_ORDER[provider];
  const adminSource = setup.source === "admin";
  const initialMode: PaymentMode = setup.mode ?? "sandbox";
  const [enabled, setEnabled] = useState(setup.enabled);
  const [mode, setMode] = useState<PaymentMode>(initialMode);
  const [urls, setUrls] = useState({
    payment_url: setup.mode === "custom" ? setup.urls?.payment_url ?? "" : "",
    query_url: setup.mode === "custom" ? setup.urls?.query_url ?? "" : "",
  });
  const [updates, setUpdates] = useState<Record<string, SecretUpdate>>(
    () => Object.fromEntries(fieldNames.map((name) => [name, { action: "keep" } as SecretUpdate])),
  );
  const [busy, setBusy] = useState<"save" | "local" | "remote" | "switch" | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<PaymentCheck | null>(null);
  const [confirming, setConfirming] = useState<null | (() => Promise<void>)>(null);
  const wasLiveProduction = setup.enabled && setup.mode === "production";

  const describe = (error: unknown) => {
    if (error instanceof ApiError && error.error) {
      const message = g.errors[error.error.code as keyof typeof g.errors];
      const field = error.error.field ? g.fields[error.error.field as keyof typeof g.fields] ?? error.error.field : null;
      if (message) return field ? `${message} — ${field}` : message;
    }
    return errorText(error, t);
  };

  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: keys.paymentSetup }),
      client.invalidateQueries({ queryKey: keys.admin }),
      client.invalidateQueries({ queryKey: keys.billing }),
    ]);
  }

  async function send(confirmProduction: boolean) {
    setBusy("save");
    setFailure(null);
    try {
      const body: Record<string, unknown> = { enabled, ...updates };
      if (provider === "payos" && useForVietqr) body.use_for_vietqr = true;
      if (provider === "onepay") {
        Object.assign(body, { mode, confirm_production: confirmProduction });
        if (mode === "custom") Object.assign(body, urls);
      }
      await api(`admin/payment-config/${provider}`, jsonRequest("PUT", body));
      // The panel remounts from the saved state: every secret input is empty again.
      await refresh();
      toast.success(g.saved);
    } catch (error) {
      setFailure(describe(error));
    } finally {
      setBusy(null);
    }
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (provider === "onepay" && enabled && mode === "production" && !wasLiveProduction) {
      setConfirming(() => () => send(true));
      return;
    }
    void send(false);
  }

  async function toggle(on: boolean, confirmProduction = false) {
    setBusy("switch");
    setFailure(null);
    try {
      await api(`admin/payment-config/${provider}/${on ? "enable" : "disable"}`,
                jsonRequest("POST", on ? { confirm_production: confirmProduction } : {}));
      await refresh();
      toast.success(on ? g.enabledToast : g.disabledToast);
    } catch (error) {
      setFailure(describe(error));
    } finally {
      setBusy(null);
    }
  }

  async function check(remote: boolean) {
    setBusy(remote ? "remote" : "local");
    try {
      setResult(await api<PaymentCheck>(`admin/payment-config/${provider}/check`, jsonRequest("POST", { remote })));
      await client.invalidateQueries({ queryKey: keys.paymentSetup });
    } catch (error) {
      setFailure(describe(error));
    } finally {
      setBusy(null);
    }
  }

  const status = setup.available ? "available" : !setup.configured ? "unavailable" : "disabled";
  const errors = setup.issues.filter((issue) => issue.level === "error");
  const warnings = setup.issues.filter((issue) => issue.level === "warning" && issue.code !== "disabled");
  const issueText = (issue: PaymentSetup["issues"][number]) => {
    const text = g.issues[issue.code as keyof typeof g.issues] ?? issue.code;
    return issue.field ? `${text}: ${g.fields[issue.field as keyof typeof g.fields] ?? issue.field}` : text;
  };

  return (
    <form className="space-y-4" onSubmit={save} autoComplete="off">
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        <span className={cn("rounded-md px-2 py-0.5 font-medium", status === "available" ? "bg-success/15 text-success"
          : status === "disabled" ? "bg-surface-2 text-muted-foreground" : "bg-warning/15 text-warning")}>
          {g.status[status]}
        </span>
        <span className="rounded-md bg-surface-2 px-2 py-0.5 text-muted-foreground">{g.sourceLabel(g.sources[setup.source])}</span>
        {provider === "onepay" && <ModeBadge mode={setup.mode} />}
        {setup.updated_at && (
          <span className="text-muted-foreground">
            {g.updated(formatDateTime(setup.updated_at), setup.updated_by ?? "—")}
          </span>
        )}
      </div>

      {(errors.length > 0 || warnings.length > 0) && (
        <ul className="space-y-1 rounded-lg border border-border bg-surface-2/50 px-3 py-2 text-xs">
          {errors.map((issue, index) => (
            <li key={`e${index}`} className="text-destructive">{issueText(issue)}</li>
          ))}
          {warnings.map((issue, index) => (
            <li key={`w${index}`} className="text-warning">{issueText(issue)}</li>
          ))}
        </ul>
      )}

      {!adminSource && setup.source !== "missing" && (
        <p className="rounded-lg border border-info/40 bg-info/10 px-3 py-2 text-xs">
          {g.legacyNotice(g.sources[setup.source])}
        </p>
      )}
      {!encryption.available && (
        <p className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs">{g.keyMissing(encryption.variable)}</p>
      )}

      <label className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm">
        <span>
          <span className="block font-medium">{g.enabled}</span>
          <span className="block text-xs text-muted-foreground">{g.enabledHint}</span>
        </span>
        <Switch checked={enabled} onCheckedChange={setEnabled} aria-label={g.enabled} />
      </label>

      {provider === "onepay" && (
        <fieldset className="space-y-2">
          <legend className="text-xs font-medium">{g.mode}</legend>
          <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label={g.mode}>
            {MODES.map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={mode === value}
                onClick={() => setMode(value)}
                className={cn("rounded-lg border p-2 text-left text-xs",
                  mode === value ? "border-primary bg-primary/10" : "border-border bg-surface hover:border-border-strong")}
              >
                <span className="block font-medium">{g.modes[value]}</span>
                <span className="block text-muted-foreground">{g.modeHints[value]}</span>
              </button>
            ))}
          </div>
          {mode === "custom" && (
            <div className="grid gap-2 sm:grid-cols-2">
              {(["payment_url", "query_url"] as const).map((name) => (
                <div key={name} className="space-y-1">
                  <label htmlFor={`onepay-${name}`} className="text-xs font-medium">{g.fields[name]}</label>
                  <Input id={`onepay-${name}`} type="url" value={urls[name]} placeholder="https://"
                         onChange={(e) => setUrls((current) => ({ ...current, [name]: e.target.value }))}
                         className="h-8 bg-surface-2 font-mono text-xs" />
                </div>
              ))}
            </div>
          )}
          {mode !== "custom" && setup.urls && setup.mode === mode && (
            <p className="truncate text-[11px] text-muted-foreground" title={setup.urls.payment_url}>{setup.urls.payment_url}</p>
          )}
        </fieldset>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {fieldNames.map((name) => (
          <SecretField
            key={name}
            id={`${provider}-${name}`}
            label={g.fields[name]}
            field={setup.fields[name] ?? { configured: false, status: "missing", secret: true, required: false, legacy: "" }}
            update={updates[name] ?? { action: "keep" }}
            onChange={(update) => setUpdates((current) => ({ ...current, [name]: update }))}
            adminSource={adminSource}
          />
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">{adminSource ? g.keepHint : g.replaceHint}</p>

      {failure && <p className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">{failure}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="sm" disabled={busy !== null || !encryption.available && !Object.values(updates).every((u) => u.action === "keep")}>
          {busy === "save" && <Loader2 className="size-3.5 animate-spin" />}
          {g.save}
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={busy !== null} onClick={() => void check(false)}>
          {busy === "local" ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
          {g.test}
        </Button>
        {provider === "onepay" && (
          <Button type="button" variant="outline" size="sm" disabled={busy !== null || !setup.query_configured}
                  onClick={() => void check(true)}>
            {busy === "remote" && <Loader2 className="size-3.5 animate-spin" />}
            {g.testRemote}
          </Button>
        )}
        {setup.enabled ? (
          <Button type="button" variant="ghost" size="sm" className="text-destructive" disabled={busy !== null}
                  onClick={() => void toggle(false)}>
            {busy === "switch" && <Loader2 className="size-3.5 animate-spin" />}
            {g.disable}
          </Button>
        ) : (
          <Button type="button" variant="ghost" size="sm" disabled={busy !== null || !setup.configured}
                  onClick={() => provider === "onepay" && setup.mode === "production"
                    ? setConfirming(() => () => toggle(true, true)) : void toggle(true)}>
            {busy === "switch" && <Loader2 className="size-3.5 animate-spin" />}
            {g.enable}
          </Button>
        )}
      </div>
      {result && (
        <p className="text-xs">
          {g.localResult}: <span className={CHECK_TONE[result.local.status]}>{g.checkStatus[result.local.status]}</span>
          {result.local.code ? ` (${g.issues[result.local.code as keyof typeof g.issues] ?? result.local.code})` : ""}
          {" · "}{g.remoteResult}: <span className={CHECK_TONE[result.remote.status]}>{g.checkStatus[result.remote.status]}</span>
          {result.remote.code ? ` (${g.issues[result.remote.code as keyof typeof g.issues] ?? result.remote.code})` : ""}
        </p>
      )}

      <div className="space-y-2 border-t border-border pt-3">
        {setup.endpoints.map((endpoint) => (
          <div key={endpoint.key}>
            <p className="mb-0.5 text-[11px] text-muted-foreground">{g.endpoints[endpoint.key]}</p>
            <CopyUrl url={endpoint.url} />
          </div>
        ))}
        {provider === "onepay" && (
          <p className="text-[11px] text-muted-foreground">
            {g.querydr}: <span className={setup.query_configured ? "text-success" : "text-warning"}>
              {setup.query_configured ? g.fieldStatus.configured : g.fieldStatus.missing}
            </span>
          </p>
        )}
        <p className="text-[11px] text-muted-foreground">
          {(["webhook", "ipn", "query", "check"] as const)
            .filter((key) => setup.activity[key])
            .map((key) => g.activity(g.activityKinds[key], formatDateTime(setup.activity[key]!)))
            .join(" · ") || g.noActivity}
        </p>
        {setup.history.length > 0 && (
          <details className="text-[11px] text-muted-foreground">
            <summary className="cursor-pointer select-none">{g.history}</summary>
            <ul className="mt-1 space-y-0.5">
              {setup.history.map((entry, index) => (
                <li key={index}>
                  {formatDateTime(entry.at)} · {g.actions[entry.action]} · {entry.by ?? "—"}
                  {entry.metadata.changed?.length
                    ? ` · ${entry.metadata.changed.map((name) => g.fields[name as keyof typeof g.fields] ?? name).join(", ")}` : ""}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-destructive" /> {g.productionTitle}
            </AlertDialogTitle>
            <AlertDialogDescription>{g.productionDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const action = confirming;
                setConfirming(null);
                if (action) void action();
              }}
            >
              {g.productionConfirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  );
}

const BANK_FIELDS = ["account_number", "account_name", "transfer_prefix"] as const;

/** Manual VietQR: the studio's bank account; buyers transfer and a system admin confirms each payment. */
function BankQRPanel({ setup, banks }: { setup: PaymentSetup; banks: { bin: string; name: string }[] }) {
  const { t, formatDateTime } = useI18n();
  const g = t.admin.gateways;
  const q = g.bankQr;
  const client = useQueryClient();
  const saved = setup.values ?? { bank_bin: "", bank_name: "", account_number: "", account_name: "",
                                  transfer_prefix: "RF", note: "", sla_message: "" };
  const [enabled, setEnabled] = useState(setup.enabled || !setup.configured);
  const [values, setValues] = useState(saved);
  // A bank outside the list: the admin types its NAPAS BIN and name.
  const [otherBank, setOtherBank] = useState(Boolean(saved.bank_bin) && !banks.some((b) => b.bin === saved.bank_bin));
  const [preview, setPreview] = useState<BankTransfer | null>(setup.preview ?? null);
  const [busy, setBusy] = useState<"save" | "switch" | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const set = (name: keyof typeof values, value: string) => setValues((current) => ({ ...current, [name]: value }));

  // A live sample QR for what is on screen (nothing is saved or sent anywhere but our API).
  useEffect(() => {
    const timer = setTimeout(async () => {
      try {
        setPreview(await api<BankTransfer>("admin/payment-config/bank_qr/preview", jsonRequest("POST", {
          bank_bin: values.bank_bin, account_number: values.account_number, account_name: values.account_name,
          transfer_prefix: values.transfer_prefix,
        })));
      } catch {
        setPreview(null);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [values.bank_bin, values.account_number, values.account_name, values.transfer_prefix]);

  const describe = (error: unknown) => {
    if (error instanceof ApiError && error.error) {
      const field = error.error.field?.replace("payments.bank_qr.", "") ?? "";
      const label = q.fields[field as keyof typeof q.fields];
      const message = g.errors[error.error.code as keyof typeof g.errors];
      if (message) return label ? `${message} — ${label}` : message;
    }
    return errorText(error, t);
  };

  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: keys.paymentSetup }),
      client.invalidateQueries({ queryKey: keys.admin }),
      client.invalidateQueries({ queryKey: keys.billing }),
    ]);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("save");
    setFailure(null);
    try {
      await api("admin/payment-config/bank_qr", jsonRequest("PUT", { enabled, ...values, use_for_vietqr: true }));
      await refresh();
      toast.success(g.saved);
    } catch (error) {
      setFailure(describe(error));
    } finally {
      setBusy(null);
    }
  }

  async function toggle(on: boolean) {
    setBusy("switch");
    setFailure(null);
    try {
      await api(`admin/payment-config/bank_qr/${on ? "enable" : "disable"}`, jsonRequest("POST", {}));
      await refresh();
      toast.success(on ? g.enabledToast : g.disabledToast);
    } catch (error) {
      setFailure(describe(error));
    } finally {
      setBusy(null);
    }
  }

  const status = setup.available ? "available" : !setup.configured ? "unavailable" : "disabled";
  return (
    <form className="space-y-4" onSubmit={save} autoComplete="off">
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        <span className={cn("rounded-md px-2 py-0.5 font-medium", status === "available" ? "bg-success/15 text-success"
          : status === "disabled" ? "bg-surface-2 text-muted-foreground" : "bg-warning/15 text-warning")}>
          {setup.active === false && setup.configured ? q.notActive : g.status[status]}
        </span>
        {setup.updated_at && (
          <span className="text-muted-foreground">{g.updated(formatDateTime(setup.updated_at), setup.updated_by ?? "—")}</span>
        )}
      </div>
      <p className="rounded-lg border border-info/40 bg-info/10 px-3 py-2 text-xs">{q.explain}</p>

      <label className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm">
        <span>
          <span className="block font-medium">{g.enabled}</span>
          <span className="block text-xs text-muted-foreground">{g.enabledHint}</span>
        </span>
        <Switch checked={enabled} onCheckedChange={setEnabled} aria-label={g.enabled} />
      </label>

      <div className="grid gap-4 md:grid-cols-[1fr_auto]">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1 sm:col-span-2">
            <label htmlFor="bank_qr-bank" className="text-xs font-medium">{q.fields.bank}</label>
            <select id="bank_qr-bank" value={otherBank ? "other" : values.bank_bin}
                    onChange={(e) => {
                      const choice = e.target.value;
                      setOtherBank(choice === "other");
                      setValues((current) => choice === "other" ? { ...current, bank_bin: "", bank_name: "" }
                        : { ...current, bank_bin: choice, bank_name: banks.find((b) => b.bin === choice)?.name ?? "" });
                    }}
                    className="h-8 w-full rounded-md border border-border bg-surface-2 px-2 text-sm">
              <option value="">{q.chooseBank}</option>
              {banks.map((bank) => (
                <option key={bank.bin} value={bank.bin}>{bank.name}</option>
              ))}
              <option value="other">{q.otherBank}</option>
            </select>
          </div>
          {otherBank && (
            <>
              <div className="space-y-1">
                <label htmlFor="bank_qr-bank_name" className="text-xs font-medium">{q.fields.bank_name}</label>
                <Input id="bank_qr-bank_name" value={values.bank_name} maxLength={80}
                       onChange={(e) => set("bank_name", e.target.value)} className="h-8 bg-surface-2 text-xs" />
              </div>
              <div className="space-y-1">
                <label htmlFor="bank_qr-bank_bin" className="text-xs font-medium">{q.fields.bank_bin}</label>
                <Input id="bank_qr-bank_bin" value={values.bank_bin} inputMode="numeric" maxLength={6}
                       onChange={(e) => set("bank_bin", e.target.value.replace(/\D/g, ""))} className="h-8 bg-surface-2 font-mono text-xs" />
              </div>
            </>
          )}
          {BANK_FIELDS.map((name) => (
            <div key={name} className="space-y-1">
              <label htmlFor={`bank_qr-${name}`} className="text-xs font-medium">{q.fields[name]}</label>
              <Input id={`bank_qr-${name}`} value={values[name]} maxLength={name === "transfer_prefix" ? 8 : 50}
                     onChange={(e) => set(name, name === "account_number" ? e.target.value.replace(/\s/g, "")
                       : name === "account_name" || name === "transfer_prefix" ? e.target.value.toUpperCase() : e.target.value)}
                     className={cn("h-8 bg-surface-2 text-xs", name !== "account_name" && "font-mono")} />
            </div>
          ))}
          <div className="space-y-1 sm:col-span-2">
            <label htmlFor="bank_qr-note" className="text-xs font-medium">{q.fields.note}</label>
            <Input id="bank_qr-note" value={values.note} maxLength={300} onChange={(e) => set("note", e.target.value)}
                   placeholder={q.notePlaceholder} className="h-8 bg-surface-2 text-xs" />
          </div>
          <div className="space-y-1 sm:col-span-2">
            <label htmlFor="bank_qr-sla_message" className="text-xs font-medium">{q.fields.sla_message}</label>
            <Input id="bank_qr-sla_message" value={values.sla_message} maxLength={300}
                   onChange={(e) => set("sla_message", e.target.value)} placeholder={q.slaPlaceholder}
                   className="h-8 bg-surface-2 text-xs" />
          </div>
          <p className="text-[11px] text-muted-foreground sm:col-span-2">{q.contentHint(values.transfer_prefix || "RF")}</p>
        </div>
        <div className="w-full space-y-1 md:w-56">
          <p className="text-xs font-medium">{q.preview}</p>
          {preview ? <TransferCard transfer={preview} compact /> : (
            <p className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-muted-foreground">{q.previewMissing}</p>
          )}
        </div>
      </div>

      {failure && <p className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">{failure}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="sm" disabled={busy !== null}>
          {busy === "save" && <Loader2 className="size-3.5 animate-spin" />}
          {q.save}
        </Button>
        {setup.enabled ? (
          <Button type="button" variant="ghost" size="sm" className="text-destructive" disabled={busy !== null}
                  onClick={() => void toggle(false)}>{g.disable}</Button>
        ) : (
          <Button type="button" variant="ghost" size="sm" disabled={busy !== null || !setup.configured}
                  onClick={() => void toggle(true)}>{g.enable}</Button>
        )}
      </div>
      {setup.history.length > 0 && (
        <details className="border-t border-border pt-3 text-[11px] text-muted-foreground">
          <summary className="cursor-pointer select-none">{g.history}</summary>
          <ul className="mt-1 space-y-0.5">
            {setup.history.map((entry, index) => (
              <li key={index}>{formatDateTime(entry.at)} · {entry.by ?? "—"}
                {entry.metadata.changed?.length ? ` · ${entry.metadata.changed.map((key) => {
                  const name = key.replace("payments.bank_qr.", "").replace("payments.", "");
                  return q.fields[name as keyof typeof q.fields] ?? name;
                }).join(", ")}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </form>
  );
}

/** System admins only (enforced by the API): VietQR (manual bank QR or payOS) and cards (OnePAY). */
export function PaymentGatewaysDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useI18n();
  const g = t.admin.gateways;
  const setup = usePaymentSetup(open);
  const data = setup.data;
  const byProvider = Object.fromEntries((data?.providers ?? []).map((item) => [item.provider, item])) as
    Partial<Record<PaymentSetup["provider"], PaymentSetup>>;
  const savedMode = data?.vietqr_mode ?? "payos";
  const [mode, setMode] = useState<"manual" | "payos" | null>(null);
  const vietqrMode = mode ?? savedMode;
  const vietqr = byProvider[vietqrMode === "manual" ? "bank_qr" : "payos"];
  const card = byProvider.onepay;
  return (
    <Dialog open={open} onOpenChange={(next) => { onOpenChange(next); if (!next) setMode(null); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{g.title}</DialogTitle>
          <DialogDescription>{g.description}</DialogDescription>
        </DialogHeader>
        {setup.isPending ? (
          <Loader2 className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
        ) : data ? (
          <>
            {!data.any_available && (
              <p className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs">{g.noneEnabled}</p>
            )}
            <Tabs defaultValue="vietqr">
              <TabsList>
                <TabsTrigger value="vietqr" className="gap-1.5">
                  <span className={cn("size-1.5 rounded-full", byProvider[savedMode === "manual" ? "bank_qr" : "payos"]?.available
                    ? "bg-success" : "bg-warning")} />
                  {g.tabs.vietqr}
                  <span className="rounded bg-surface-2 px-1.5 text-[10px] text-muted-foreground">{g.vietqrModes[savedMode]}</span>
                </TabsTrigger>
                {card && (
                  <TabsTrigger value="card" className="gap-1.5">
                    <span className={cn("size-1.5 rounded-full", card.available ? "bg-success" : card.configured
                      ? "bg-muted-foreground" : "bg-warning")} />
                    {g.tabs.card}
                    {card.mode && <ModeBadge mode={card.mode} />}
                  </TabsTrigger>
                )}
              </TabsList>
              <TabsContent value="vietqr" className="mt-4 space-y-4">
                <fieldset className="space-y-2">
                  <legend className="text-xs font-medium">{g.vietqrMode}</legend>
                  <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label={g.vietqrMode}>
                    {(["manual", "payos"] as const).map((value) => (
                      <button key={value} type="button" role="radio" aria-checked={vietqrMode === value}
                              onClick={() => setMode(value)}
                              className={cn("rounded-lg border p-2 text-left text-xs", vietqrMode === value
                                ? "border-primary bg-primary/10" : "border-border bg-surface hover:border-border-strong")}>
                        <span className="block font-medium">{g.vietqrModes[value]}</span>
                        <span className="block text-muted-foreground">{g.vietqrModeHints[value]}</span>
                      </button>
                    ))}
                  </div>
                  {vietqrMode !== savedMode && <p className="text-[11px] text-warning">{g.vietqrModePending}</p>}
                </fieldset>
                {vietqr && (vietqrMode === "manual" ? (
                  <BankQRPanel key={`${vietqr.updated_at}-${vietqr.enabled}`} setup={vietqr} banks={data.banks ?? []} />
                ) : (
                  <GatewayPanel key={`${vietqr.updated_at}-${vietqr.enabled}-${savedMode}`} setup={vietqr}
                                encryption={data.encryption} useForVietqr />
                ))}
              </TabsContent>
              {card && (
                <TabsContent value="card" className="mt-4">
                  {/* Remount after every save, so no secret ever lingers in an input. */}
                  <GatewayPanel key={`${card.updated_at}-${card.enabled}-${card.mode}`} setup={card}
                                encryption={data.encryption} />
                </TabsContent>
              )}
            </Tabs>
          </>
        ) : (
          <p className="text-sm text-destructive">{errorText(setup.error, t)}</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
