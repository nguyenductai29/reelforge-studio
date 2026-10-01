"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, MoreHorizontal, Search } from "lucide-react";
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
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useAdminPayments } from "@/lib/queries";
import type { AdminPayment, PaymentProviderStatus } from "@/lib/types";
import { DataTable, useDebounced, type Column } from "../data-table";
import { StatusBadge } from "../primitives";
import { PaymentGatewaysDialog } from "./payment-gateways";
import { Detail, FilterSelect } from "./shared";

const LIMIT = 20;
const STATUSES = ["awaiting_confirmation", "pending", "paid", "paid_unapplied", "failed", "cancelled", "expired"] as const;
const awaiting = (o: AdminPayment) => o.provider === "bank_qr" && o.status === "pending" && Boolean(o.transfer_reported_at);

/**
 * Every studio's payment orders. An admin can ask the provider again (server to server); only what the
 * provider confirms is applied. There is deliberately no "mark as paid".
 */
export function AdminPayments({ providers, initialStatus = "" }: { providers: PaymentProviderStatus[]; initialStatus?: string }) {
  const { t, formatDateTime, formatMoney } = useI18n();
  const p = t.admin.payments;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [q, setQ] = useState("");
  const [provider, setProvider] = useState("");
  const [status, setStatus] = useState(initialStatus);
  // Manual VietQR: an explicit confirmation of the amount received, or a rejection with an optional note.
  const [reviewing, setReviewing] = useState<{ order: AdminPayment; action: "confirm" | "reject" } | null>(null);
  const [note, setNote] = useState("");
  const [offset, setOffset] = useState(0);
  const [viewing, setViewing] = useState<AdminPayment | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const search = useDebounced(q.trim());
  const payments = useAdminPayments({ q: search, provider, status, limit: LIMIT, offset });
  const statusLabel = (value: string) =>
    value === "awaiting_confirmation" ? p.awaitingConfirmation : t.status.order[value as keyof Dictionary["status"]["order"]] ?? value;
  const providerLabel = (value: string) => p.providers[value as keyof typeof p.providers] ?? value;
  const filtered = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };

  async function refresh(order: AdminPayment) {
    setBusy(order.id);
    try {
      const result = await api<{ status: string }>(`admin/payments/${encodeURIComponent(order.id)}/refresh`, { method: "POST" });
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.adminPayments }),
        client.invalidateQueries({ queryKey: keys.admin }),
      ]);
      toast.success(p.refreshed(statusLabel(result.status)));
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
    }
  }

  async function review() {
    if (!reviewing) return;
    const { order, action } = reviewing;
    setBusy(order.id);
    try {
      const result = await api<{ status: string }>(`admin/payments/${encodeURIComponent(order.id)}/${action}`,
        jsonRequest("POST", action === "confirm" ? { amount_vnd: order.amount_vnd } : { note: note.trim() || null }));
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.adminPayments }),
        client.invalidateQueries({ queryKey: keys.admin }),
      ]);
      toast.success(p.refreshed(statusLabel(result.status)));
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
      setReviewing(null);
      setNote("");
    }
  }

  const columns: Column<AdminPayment>[] = [
    { key: "order", header: p.columns.order, className: "font-mono text-xs", cell: (o) => o.reference },
    { key: "user", header: p.columns.user, className: "max-w-[200px]",
      cell: (o) => <span className="block truncate" title={o.owner_email}>{o.owner_email}</span> },
    { key: "studio", header: p.columns.studio, className: "max-w-[160px]",
      cell: (o) => <span className="block truncate text-muted-foreground" title={o.workspace_name}>{o.workspace_name}</span> },
    { key: "provider", header: p.columns.provider, className: "whitespace-nowrap", cell: (o) => providerLabel(o.provider) },
    { key: "plan", header: p.columns.plan, className: "whitespace-nowrap", cell: (o) => o.plan_code.toUpperCase() },
    { key: "amount", header: p.columns.amount, className: "whitespace-nowrap text-right tabular-nums",
      cell: (o) => formatMoney(o.amount_vnd) },
    { key: "status", header: p.columns.status, className: "whitespace-nowrap", cell: (o) => awaiting(o)
      ? <StatusBadge status="needs_attention" label={p.awaitingConfirmation} />
      : <StatusBadge status={o.status} label={statusLabel(o.status)} /> },
    { key: "created", header: p.columns.created, className: "whitespace-nowrap text-muted-foreground",
      cell: (o) => formatDateTime(o.created_at) },
    { key: "paid", header: p.columns.paid, className: "whitespace-nowrap text-muted-foreground",
      cell: (o) => (o.paid_at ? formatDateTime(o.paid_at) : "—") },
    { key: "reference", header: p.columns.reference, className: "max-w-[140px] font-mono text-xs",
      cell: (o) => <span className="block truncate" title={o.provider_reference ?? ""}>{o.provider_reference ?? "—"}</span> },
    { key: "actions", header: <span className="sr-only">{p.columns.actions}</span>, className: "w-10 text-right",
      cell: (o) => (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7" aria-label={`${p.columns.actions} · ${o.reference}`}>
              {busy === o.id ? <Loader2 className="size-4 animate-spin" /> : <MoreHorizontal className="size-4" />}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setViewing(o)}>{p.view}</DropdownMenuItem>
            {o.provider === "bank_qr" ? (
              <>
                <DropdownMenuItem disabled={Boolean(busy) || o.status === "paid" || o.status === "paid_unapplied"}
                                  onClick={() => setReviewing({ order: o, action: "confirm" })}>
                  {p.confirmReceived}
                </DropdownMenuItem>
                <DropdownMenuItem disabled={Boolean(busy) || o.status !== "pending"} className="text-destructive"
                                  onClick={() => setReviewing({ order: o, action: "reject" })}>
                  {p.rejectTransfer}
                </DropdownMenuItem>
              </>
            ) : (
              <DropdownMenuItem
                disabled={Boolean(busy) || o.status === "paid" || o.status === "paid_unapplied"}
                onClick={() => void refresh(o)}
              >
                {p.refresh}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      ) },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={payments.data?.items ?? []}
        rowKey={(o) => o.id}
        loading={payments.isFetching}
        error={payments.isError ? errorText(payments.error, t) : null}
        empty={p.empty}
        total={payments.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffset={setOffset}
        minWidth={1240}
        toolbar={
          <>
            <div className="relative min-w-[200px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setOffset(0);
                }}
                placeholder={p.searchPlaceholder}
                aria-label={p.searchPlaceholder}
                className="h-8 bg-surface pl-8"
              />
            </div>
            <FilterSelect value={provider} onChange={filtered(setProvider)} all={p.allProviders}
                          options={(["payos", "bank_qr", "onepay"] as const).map((v) => [v, providerLabel(v)])} />
            <FilterSelect value={status} onChange={filtered(setStatus)} all={p.allStatuses}
                          options={STATUSES.map((v) => [v, statusLabel(v)])} />
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              {providers.map((item) => {
                // Older APIs report only "configured".
                const state = (item.available ?? item.configured) ? "available"
                  : item.configured && item.enabled === false ? "disabled" : "missing";
                return (
                  <span key={item.provider} className="rounded-md bg-surface-2 px-2 py-1 text-muted-foreground">
                    {providerLabel(item.provider)}:{" "}
                    <span className={state === "available" ? "text-success" : state === "disabled" ? "" : "text-warning"}>
                      {p.providerState[state]}
                    </span>
                  </span>
                );
              })}
            </div>
            <Button variant="outline" size="sm" className="h-8" onClick={() => setSetupOpen(true)}>
              <KeyRound className="size-3.5" /> {t.admin.gateways.open}
            </Button>
          </>
        }
      />
      <PaymentGatewaysDialog open={setupOpen} onOpenChange={setSetupOpen} />
      <Dialog open={Boolean(viewing)} onOpenChange={(open) => !open && setViewing(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{p.detailTitle}</DialogTitle>
            <DialogDescription className="font-mono">{viewing?.reference}</DialogDescription>
          </DialogHeader>
          {viewing && (
            <dl className="grid gap-3 sm:grid-cols-2">
              <Detail label={p.columns.user}>{viewing.owner_email}</Detail>
              <Detail label={p.columns.studio}>{viewing.workspace_name}</Detail>
              <Detail label={p.columns.provider}>{providerLabel(viewing.provider)}</Detail>
              <Detail label={p.columns.plan}>{viewing.plan_code.toUpperCase()}</Detail>
              <Detail label={p.columns.amount}>{formatMoney(viewing.amount_vnd)}</Detail>
              <Detail label={p.columns.status}>{statusLabel(viewing.status)}</Detail>
              <Detail label={p.columns.created}>{formatDateTime(viewing.created_at)}</Detail>
              <Detail label={p.columns.paid}>{viewing.paid_at ? formatDateTime(viewing.paid_at) : "—"}</Detail>
              <Detail label={p.columns.reference}>{viewing.provider_reference ?? "—"}</Detail>
              {viewing.provider === "bank_qr" && (
                <Detail label={p.reportedAt}>{viewing.transfer_reported_at ? formatDateTime(viewing.transfer_reported_at) : "—"}</Detail>
              )}
            </dl>
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog open={reviewing !== null} onOpenChange={(open) => !open && !busy && setReviewing(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{reviewing?.action === "confirm" ? p.confirmTitle : p.rejectTitle}</AlertDialogTitle>
            <AlertDialogDescription>
              {reviewing && (reviewing.action === "confirm"
                ? p.confirmText(formatMoney(reviewing.order.amount_vnd), reviewing.order.transfer_content ?? reviewing.order.reference,
                                reviewing.order.owner_email)
                : p.rejectText(reviewing.order.transfer_content ?? reviewing.order.reference))}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {reviewing?.action === "reject" && (
            <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder={p.rejectNote}
                   aria-label={p.rejectNote} className="h-8 bg-surface" />
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={Boolean(busy)}>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction disabled={Boolean(busy)} onClick={(event) => { event.preventDefault(); void review(); }}
                               className={reviewing?.action === "reject" ? "bg-destructive text-white hover:bg-destructive/90" : ""}>
              {busy && <Loader2 className="size-3.5 animate-spin" />}
              {reviewing?.action === "confirm" ? p.confirmReceived : p.rejectTransfer}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
