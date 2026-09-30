"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { PageHeader, StatCard, StatusBadge } from "@/components/reelforge/primitives";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle, useSearchParam } from "@/lib/hooks";
import { toDate, useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useBilling, useUsage } from "@/lib/queries";
import { cn } from "@/lib/utils";

const rank = (code: string) => ["trial", "standard", "pro"].indexOf(code);

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
  const payment = useSearchParam("payment");

  const refreshAll = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys.billing }),
      client.invalidateQueries({ queryKey: keys.usage }),
      client.invalidateQueries({ queryKey: keys.dashboard }),
    ]);

  // payOS sends the buyer back here; the webhook, not this visit, confirms payment.
  useEffect(() => {
    if (!payment) return;
    if (payment === "cancelled") toast(t.billing.paymentCancelled);
    else toast(t.billing.paymentReturned);
    void refreshAll();
    window.history.replaceState(null, "", window.location.pathname);
    // Run once per return visit.
  }, [payment]);

  async function checkout(code: string) {
    setBusy(code);
    try {
      const result = await api<{ checkout_url: string }>("billing/checkout", jsonRequest("POST", { plan_code: code }));
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
                </div>
                {canBuy && (
                  <Button
                    className="mt-auto"
                    variant={current ? "outline" : "default"}
                    disabled={
                      Boolean(busy) || !p.price_vnd || !data.payos_ready || !["active", "expired"].includes(subscription.status)
                    }
                    onClick={() => void checkout(p.code)}
                  >
                    {busy === p.code && <Loader2 className="size-4 animate-spin" />}
                    {busy === p.code ? t.billing.creatingOrder : current ? t.billing.renew : t.billing.pay}
                  </Button>
                )}
              </div>
            );
          })}
        </div>
        {!data.payos_ready && (
          <p className="rounded-lg border border-warning/40 bg-[color-mix(in_oklab,var(--warning)_10%,transparent)] px-3 py-2 text-sm">
            {t.billing.payosMissing}
          </p>
        )}
        <p className="text-xs text-muted-foreground">{t.billing.cardSoon}</p>
      </section>

      <section className="panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm font-medium">{t.billing.orders}</p>
          <Button variant="ghost" size="sm" onClick={() => void refreshAll()}>
            <RefreshCw className="size-3.5" /> {t.common.refresh}
          </Button>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{t.billing.ordersHint}</p>
        <div className="mt-3 divide-y divide-border">
          {data.orders.length === 0 && <p className="py-3 text-sm text-muted-foreground">{t.billing.noOrders}</p>}
          {data.orders.map((order) => (
            <div key={order.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-medium">
                  {order.plan_code.toUpperCase()} · {formatMoney(order.amount_vnd)}
                </p>
                <p className="text-xs text-muted-foreground">{formatDateTime(order.created_at)}</p>
              </div>
              <StatusBadge
                status={order.status}
                label={t.status.order[order.status as keyof Dictionary["status"]["order"]] ?? order.status}
              />
              {["pending", "expired", "failed"].includes(order.status) && data.payos_ready && (
                <Button variant="outline" size="sm" disabled={Boolean(busy)} onClick={() => void checkOrder(order.id)}>
                  {busy === order.id && <Loader2 className="size-3.5 animate-spin" />}
                  {t.billing.check}
                </Button>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
