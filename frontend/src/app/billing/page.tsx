"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CreditCard, Loader2, QrCode, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DataTable, type Column } from "@/components/reelforge/data-table";
import { Progress } from "@/components/ui/progress";
import { PageHeader, StatCard, StatusBadge } from "@/components/reelforge/primitives";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle, useSearchParam } from "@/lib/hooks";
import { toDate, useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useBilling, useBillingOrders, useUsage } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { Order, PaymentMethod } from "@/lib/types";
import { cn } from "@/lib/utils";

const rank = (code: string) => ["trial", "standard", "pro"].indexOf(code);
const ORDER_PAGE = 10;
const METHOD_ICON = { vietqr: QrCode, card: CreditCard } as const;

function reasonText(reason: string, t: Dictionary) {
  if (reason.startsWith("admin: ")) return t.billing.reasons.admin(reason.slice(7));
  const known = t.billing.reasons[reason as Exclude<keyof Dictionary["billing"]["reasons"], "admin">];
  return typeof known === "string" ? known : reason;
}

export default function BillingPage() {
  const { t, formatNumber, formatMoney, formatDate, formatDateTime } = useI18n();
  useDocumentTitle(t.billing.title);
  const client = useQueryClient();
  const showError = useErrorToast();
  const billing = useBilling();
  const usage = useUsage();
  const [busy, setBusy] = useState<string | null>(null);
  const [choosing, setChoosing] = useState<string | null>(null);
  const [method, setMethod] = useState<PaymentMethod | null>(null);
  const [orderOffset, setOrderOffset] = useState(0);
  const orders = useBillingOrders(orderOffset, ORDER_PAGE);
  const payment = useSearchParam("payment");

  const refreshAll = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys.billing }),
      client.invalidateQueries({ queryKey: keys.billingOrders }),
      client.invalidateQueries({ queryKey: keys.usage }),
      client.invalidateQueries({ queryKey: keys.dashboard }),
    ]);

  // The provider sends the buyer back here; its signed callback or a server-side check, not this visit, confirms payment.
  useEffect(() => {
    if (!payment) return;
    if (payment === "cancelled") toast(t.billing.paymentCancelled);
    else if (payment === "failed") toast.error(t.billing.paymentFailed);
    else if (payment === "invalid") toast.error(t.billing.paymentInvalid);
    else toast(t.billing.paymentReturned);
    void refreshAll();
    window.history.replaceState(null, "", window.location.pathname);
    // Run once per return visit.
  }, [payment]);

  /** Pay for a plan: straight to checkout with one payment method, else ask which one. */
  function choose(code: string) {
    const methods = billing.data?.methods ?? [];
    if (methods.length === 1) void checkout(code, methods[0]!.id);
    else {
      setMethod(methods[0]?.id ?? null);
      setChoosing(code);
    }
  }

  async function checkout(code: string, chosen: PaymentMethod) {
    setBusy(code);
    try {
      const result = await api<{ checkout_url: string }>(
        "billing/checkout",
        jsonRequest("POST", { plan_code: code, method: chosen }),
      );
      const url = new URL(result.checkout_url);
      if (url.protocol !== "https:") throw new Error(t.billing.invalidCheckout);
      window.location.assign(url.toString());
    } catch (error) {
      showError(error);
      setBusy(null);
    }
  }

  async function checkOrder(id: string) {
    setBusy(id);
    try {
      await api(`billing/orders/${encodeURIComponent(id)}/refresh`, { method: "POST" });
      await refreshAll();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
    }
  }

  const data = billing.data;
  if (!data) return <Loader2 className="mx-auto mt-10 size-5 animate-spin text-muted-foreground" />;

  const subscription = data.subscription;
  const plan = data.plans.find((p) => p.code === subscription.plan_code);
  const planName = plan?.name ?? subscription.plan_code.toUpperCase();
  const statusLabel = t.status.subscription[subscription.status as keyof Dictionary["status"]["subscription"]] ?? subscription.status;
  const now = new Date();
  const events = usage.data?.events ?? [];
  const thisMonth = events.filter((e) => {
    const d = toDate(e.created_at);
    return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
  });
  const used = thisMonth.reduce((sum, e) => sum + e.credits, 0);
  const byTool = [...thisMonth.reduce((map, e) => map.set(e.tool, (map.get(e.tool) ?? 0) + e.credits), new Map<string, number>())]
    .sort((a, b) => b[1] - a[1]);
  const limitText = (n: number | null) => (n === null ? t.common.unlimited : formatNumber(n));
  const canPay = data.methods.length > 0;
  const orderStatus = (status: string) => t.status.order[status as keyof Dictionary["status"]["order"]] ?? status;
  const orderColumns: Column<Order>[] = [
    { key: "plan", header: t.billing.history.plan, cell: (order) => order.plan_code.toUpperCase() },
    { key: "method", header: t.billing.history.method, className: "whitespace-nowrap",
      cell: (order) => (order.method ? t.billing.methods[order.method].name : order.provider) },
    { key: "amount", header: t.billing.history.amount, className: "whitespace-nowrap text-right tabular-nums",
      cell: (order) => formatMoney(order.amount_vnd) },
    { key: "status", header: t.billing.history.status,
      cell: (order) => <StatusBadge status={order.status} label={orderStatus(order.status)} /> },
    { key: "created", header: t.billing.history.created, className: "whitespace-nowrap text-muted-foreground",
      cell: (order) => formatDateTime(order.created_at) },
    { key: "paid", header: t.billing.history.paid, className: "whitespace-nowrap text-muted-foreground",
      cell: (order) => (order.paid_at ? formatDateTime(order.paid_at) : "—") },
    { key: "reference", header: t.billing.history.reference, className: "font-mono text-xs text-muted-foreground",
      cell: (order) => order.reference },
    { key: "actions", header: <span className="sr-only">{t.billing.check}</span>, className: "text-right",
      cell: (order) =>
        ["pending", "expired", "failed"].includes(order.status) ? (
          <Button variant="outline" size="sm" className="h-7" disabled={Boolean(busy)} onClick={() => void checkOrder(order.id)}>
            {busy === order.id && <Loader2 className="size-3.5 animate-spin" />}
            {t.billing.check}
          </Button>
        ) : null },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.billing.title}
        subtitle={t.billing.subtitle(planName, statusLabel)}
        actions={
          <Button variant="outline" onClick={() => document.getElementById("plans")?.scrollIntoView({ behavior: "smooth" })}>
            {t.billing.upgrade}
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label={t.billing.stats.plan} value={planName} hint={statusLabel} />
        <StatCard label={t.billing.stats.available} value={formatNumber(usage.data?.balance ?? 0)} />
        <StatCard label={t.billing.stats.used} value={formatNumber(used)} />
        <StatCard
          label={t.billing.stats.renewal}
          value={subscription.ends_at ? formatDate(subscription.ends_at, { day: "numeric", month: "short" }) : t.billing.noEnd}
          hint={subscription.ends_at ? formatDate(subscription.ends_at, { year: "numeric" }) : undefined}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="panel p-5">
          <p className="text-sm font-medium">{t.billing.usageBreakdown}</p>
          <div className="mt-4 space-y-4">
            {byTool.length === 0 && <p className="text-sm text-muted-foreground">{t.billing.noUsage}</p>}
            {byTool.map(([tool, credits]) => (
              <div key={tool}>
                <div className="flex justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate">{tool}</span>
                  <span className="shrink-0 text-muted-foreground">{t.common.credits(formatNumber(credits))}</span>
                </div>
                <Progress value={used ? (credits / used) * 100 : 0} className="mt-2 h-1.5" />
              </div>
            ))}
          </div>
        </div>
        <div className="panel p-5">
          <p className="text-sm font-medium">{t.billing.transactions}</p>
          <div className="scrollbar-thin mt-3 max-h-80 divide-y divide-border overflow-y-auto">
            {!usage.data?.ledger.length && <p className="py-3 text-sm text-muted-foreground">{t.billing.noTransactions}</p>}
            {usage.data?.ledger.map((row) => (
              <div key={row.id} className="flex items-center justify-between gap-3 py-3 text-sm">
                <div className="min-w-0">
                  <p className="truncate">{reasonText(row.reason, t)}</p>
                  <p className="text-xs text-muted-foreground">{formatDateTime(row.created_at)}</p>
                </div>
                <span className={cn("shrink-0 font-medium", row.delta > 0 ? "text-success" : "text-muted-foreground")}>
                  {row.delta > 0 ? "+" : ""}
                  {formatNumber(row.delta)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <section id="plans" className="scroll-mt-20 space-y-4">
        <div>
          <h2 className="text-base font-semibold">{t.billing.plans}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t.billing.plansHint}</p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {[...data.plans].sort((a, b) => rank(a.code) - rank(b.code)).map((p) => {
            const current = p.code === subscription.plan_code;
            const canBuy = rank(p.code) > rank(subscription.plan_code) || (current && p.code !== "trial");
            return (
              <div key={p.code} className={cn("panel flex flex-col gap-3 p-5", current && "border-primary/50")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    {p.code}
                  </span>
                  {current && <StatusBadge status="ready" label={t.billing.current} />}
                </div>
                <p className="text-lg font-semibold">{p.name}</p>
                <p className="text-2xl font-semibold">
                  {p.code === "trial" ? t.billing.free : p.price_vnd ? formatMoney(p.price_vnd) : t.billing.notForSale}
                  {p.price_vnd && p.code !== "trial" ? (
                    <span className="ml-1 text-sm font-normal text-muted-foreground">{t.billing.perPeriod}</span>
                  ) : null}
                </p>
                <div className="space-y-1 text-sm text-muted-foreground">
                  <p>{t.billing.limits(limitText(p.project_limit), limitText(p.workflow_limit))}</p>
                  <p>{t.billing.monthlyCredits(formatNumber(p.monthly_credits))}</p>
                  {p.storage_quota_bytes ? <p>{t.billing.storage(formatBytes(p.storage_quota_bytes))}</p> : null}
                </div>
                {/* Without a configured payment method there is nothing to press; the notice below explains why. */}
                {canBuy && canPay && (
                  <Button
                    className="mt-auto"
                    variant={current ? "outline" : "default"}
                    disabled={Boolean(busy) || !p.price_vnd || !["active", "expired"].includes(subscription.status)}
                    onClick={() => choose(p.code)}
                  >
                    {busy === p.code && <Loader2 className="size-4 animate-spin" />}
                    {busy === p.code ? t.billing.creatingOrder : current ? t.billing.renew : t.billing.pay}
                  </Button>
                )}
              </div>
            );
          })}
        </div>
        {!canPay && (
          <p className="rounded-lg border border-warning/40 bg-[color-mix(in_oklab,var(--warning)_10%,transparent)] px-3 py-2 text-sm">
            {t.billing.noPaymentMethod}
          </p>
        )}
        {canPay && (
          <p className="text-xs text-muted-foreground">
            {t.billing.acceptedMethods(data.methods.map((m) => t.billing.methods[m.id].name).join(" · "))}
          </p>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium">{t.billing.orders}</p>
            <p className="mt-1 text-xs text-muted-foreground">{t.billing.ordersHint}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => void refreshAll()}>
            <RefreshCw className="size-3.5" /> {t.common.refresh}
          </Button>
        </div>
        <DataTable
          className="max-h-[520px]"
          columns={orderColumns}
          rows={orders.data?.items ?? []}
          rowKey={(order) => order.id}
          loading={orders.isFetching}
          empty={t.billing.noOrders}
          total={orders.data?.total ?? 0}
          limit={ORDER_PAGE}
          offset={orderOffset}
          onOffset={setOrderOffset}
          minWidth={820}
        />
      </section>

      <Dialog open={Boolean(choosing)} onOpenChange={(open) => !open && !busy && setChoosing(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t.billing.chooseMethod}</DialogTitle>
            <DialogDescription>{t.billing.chooseMethodHint}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2" role="radiogroup" aria-label={t.billing.chooseMethod}>
            {data.methods.map((item) => {
              const Icon = METHOD_ICON[item.id];
              return (
                <button
                  key={item.id}
                  type="button"
                  role="radio"
                  aria-checked={method === item.id}
                  onClick={() => setMethod(item.id)}
                  className={cn(
                    "flex items-start gap-3 rounded-lg border p-3 text-left",
                    method === item.id ? "border-primary bg-primary/10" : "border-border bg-surface hover:border-border-strong",
                  )}
                >
                  <Icon className="mt-0.5 size-5 shrink-0 text-primary" />
                  <span>
                    <span className="block text-sm font-medium">{t.billing.methods[item.id].name}</span>
                    <span className="block text-xs text-muted-foreground">{t.billing.methods[item.id].hint}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setChoosing(null)} disabled={Boolean(busy)}>
              {t.common.cancel}
            </Button>
            <Button disabled={!method || Boolean(busy)} onClick={() => choosing && method && void checkout(choosing, method)}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {busy ? t.billing.creatingOrder : t.billing.continueToPay}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
