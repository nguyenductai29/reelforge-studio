"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Loader2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, usePaymentSetup } from "@/lib/queries";
import type { PaymentCheck, PaymentSetup, PaymentSetupField } from "@/lib/types";
import { cn } from "@/lib/utils";

const FIELD_TONE: Record<PaymentSetupField["status"], string> = {
  configured: "text-success",
  missing: "text-warning",
  invalid: "text-destructive",
};
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
        aria-label={t.admin.paymentSetup.copy}
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

function ProviderCard({ setup }: { setup: PaymentSetup }) {
  const { t, formatDateTime } = useI18n();
  const s = t.admin.paymentSetup;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [busy, setBusy] = useState<"local" | "remote" | null>(null);
  const [result, setResult] = useState<PaymentCheck | null>(null);

  async function check(remote: boolean) {
    setBusy(remote ? "remote" : "local");
    try {
      setResult(await api<PaymentCheck>("admin/payment-config/check",
                                        jsonRequest("POST", { provider: setup.provider, remote })));
      await client.invalidateQueries({ queryKey: keys.paymentSetup });
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-3 rounded-xl border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{t.admin.payments.providers[setup.provider]}</h3>
        <div className="flex flex-wrap gap-1.5 text-[11px]">
          {setup.mode && (
            <span className={cn("rounded-md px-2 py-0.5", setup.mode === "production" ? "bg-warning/15 text-warning"
              : "bg-info/15 text-info")}>
              {s.modes[setup.mode]}
            </span>
          )}
          <span className={cn("rounded-md bg-surface-2 px-2 py-0.5", setup.configured ? "text-success" : "text-warning")}>
            {setup.configured ? t.admin.payments.configured : t.admin.payments.missing}
          </span>
        </div>
      </div>
      <table className="w-full text-xs">
        <tbody>
          {setup.fields.map((field) => (
            <tr key={field.name} className="border-b border-border/60 last:border-0">
              <td className="py-1.5 pr-2 font-mono">{field.name}</td>
              <td className="py-1.5 pr-2 text-muted-foreground">
                {field.value ? <span className="break-all">{field.value}</span> : null}
                {field.default ? <span className="ml-1">({s.default})</span> : null}
              </td>
              <td className={cn("py-1.5 text-right", FIELD_TONE[field.status])}>{s.fieldStatus[field.status]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-muted-foreground">{setup.provider === "payos" ? s.payosWhere : s.onepayWhere}</p>
      <div className="space-y-1.5">
        {setup.endpoints.map((endpoint) => (
          <div key={endpoint.key}>
            <p className="mb-0.5 text-[11px] text-muted-foreground">{s.endpoints[endpoint.key]}</p>
            <CopyUrl url={endpoint.url} />
          </div>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        {(["webhook", "ipn", "query", "check"] as const)
          .filter((key) => setup.activity[key])
          .map((key) => s.activity(s.activityKinds[key], formatDateTime(setup.activity[key]!)))
          .join(" · ") || s.noActivity}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={busy !== null} onClick={() => void check(false)}>
          {busy === "local" ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
          {s.check}
        </Button>
        {setup.provider === "onepay" && (
          <Button variant="outline" size="sm" disabled={busy !== null || !setup.query_configured}
                  onClick={() => void check(true)}>
            {busy === "remote" && <Loader2 className="size-3.5 animate-spin" />}
            {s.checkRemote}
          </Button>
        )}
      </div>
      {result && (
        <p className="text-xs">
          {s.localResult}: <span className={CHECK_TONE[result.local.status]}>{s.checkStatus[result.local.status]}</span>
          {result.local.code ? ` (${result.local.code})` : ""} · {s.remoteResult}:{" "}
          <span className={CHECK_TONE[result.remote.status]}>{s.checkStatus[result.remote.status]}</span>
          {result.remote.code ? ` (${result.remote.code})` : ""}
        </p>
      )}
    </section>
  );
}

/** System admins only (enforced by the API): what each provider needs, never a secret value. */
export function PaymentSetupDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useI18n();
  const setup = usePaymentSetup(open);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t.admin.paymentSetup.title}</DialogTitle>
          <DialogDescription>{t.admin.paymentSetup.description}</DialogDescription>
        </DialogHeader>
        {setup.isPending ? (
          <Loader2 className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
        ) : (
          <div className="space-y-4">
            {(setup.data ?? []).map((item) => (
              <ProviderCard key={item.provider} setup={item} />
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
