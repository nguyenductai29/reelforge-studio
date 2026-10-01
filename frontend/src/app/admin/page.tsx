"use client";

import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, FieldLabel } from "@/components/reelforge/primitives";
import { AdminPayments } from "@/components/reelforge/admin/admin-payments";
import { AdminStudios } from "@/components/reelforge/admin/admin-studios";
import { AdminUsers } from "@/components/reelforge/admin/admin-users";
import { Operations } from "@/components/reelforge/operations";
import { Reconciliation } from "@/components/reelforge/reconciliation";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, useAdmin, useDashboard } from "@/lib/queries";
import { GIB, formatBytes } from "@/lib/studio";
import type { Plan } from "@/lib/types";
import { cn } from "@/lib/utils";

const TABS = ["users", "studios", "plans", "payments", "reconciliation", "operations"] as const;
type Tab = (typeof TABS)[number];

function PlanForm({ plan }: { plan: Plan }) {
  const { t } = useI18n();
  const p = t.admin.plans;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [active, setActive] = useState(plan.is_active);
  const [busy, setBusy] = useState(false);
  const number = (value: FormDataEntryValue | null) => (value ? Number(value) : null);
  // Entered in GB (GiB); empty uses the server default.
  const gigabytes = (value: FormDataEntryValue | null) => (value ? Math.round(Number(value) * GIB) : null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await api(`admin/plans/${plan.code}`, jsonRequest("PUT", {
        name: form.get("name"),
        project_limit: number(form.get("project_limit")),
        workflow_limit: number(form.get("workflow_limit")),
        monthly_credits: Number(form.get("monthly_credits")),
        price_vnd: plan.code === "trial" ? null : number(form.get("price_vnd")),
        storage_limit_bytes: gigabytes(form.get("storage_limit_gb")),
        is_active: active,
      }));
      await client.invalidateQueries({ queryKey: keys.admin });
      toast.success(t.admin.saved);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  const field = (name: string, label: string, props: Record<string, unknown>) => (
    <div>
      <FieldLabel htmlFor={`${plan.code}-${name}`}>{label}</FieldLabel>
      <Input id={`${plan.code}-${name}`} name={name} className="h-8 bg-surface-2" {...props} />
    </div>
  );

  return (
    <form className="panel space-y-3 p-4" onSubmit={save}>
      <div className="flex items-center justify-between gap-2">
        <span className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {plan.code}
        </span>
        <label className="flex items-center gap-2 text-xs">
          {p.active}
          <Switch checked={active} onCheckedChange={setActive} />
        </label>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {field("name", p.name, { defaultValue: plan.name, required: true, maxLength: 80 })}
        {field("price_vnd", p.price, {
          type: "number", min: 2000, max: 2000000000, defaultValue: plan.price_vnd ?? "",
          placeholder: plan.code === "trial" ? t.billing.free : t.billing.notForSale, disabled: plan.code === "trial",
        })}
        {field("project_limit", p.projectLimit, {
          type: "number", min: 1, defaultValue: plan.project_limit ?? "", placeholder: t.common.unlimited,
          required: plan.code === "trial",
        })}
        {field("workflow_limit", p.workflowLimit, {
          type: "number", min: 1, defaultValue: plan.workflow_limit ?? "", placeholder: t.common.unlimited,
        })}
        {field("monthly_credits", p.monthlyCredits, { type: "number", min: 0, defaultValue: plan.monthly_credits, required: true })}
        {field("storage_limit_gb", p.storageLimit, {
          type: "number", min: 0.1, max: 102400, step: 0.1,
          defaultValue: plan.storage_limit_bytes ? Math.round((plan.storage_limit_bytes / GIB) * 10) / 10 : "",
          placeholder: plan.storage_quota_bytes ? p.storageDefault(formatBytes(plan.storage_quota_bytes)) : "",
        })}
        <div className="flex items-end justify-end">
          <Button type="submit" size="sm" disabled={busy}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            {p.save}
          </Button>
        </div>
      </div>
    </form>
  );
}

/**
 * The admin console fits the window: the header, tabs, toolbars and pagination stay put, and only each
 * table's body scrolls. Every collection is loaded one page at a time from the server.
 */
export default function AdminPage() {
  const { t, formatNumber } = useI18n();
  useDocumentTitle(t.admin.title);
  const { data: dashboard } = useDashboard();
  const admin = useAdmin(Boolean(dashboard?.is_admin));
  const [tab, setTab] = useState<Tab>("users");
  const a = t.admin;

  if (!dashboard?.is_admin) {
    return <EmptyState icon={ShieldCheck} title={a.title} description={t.errors.server["System admin required"] ?? ""} />;
  }
  const overview = admin.data;
  if (!overview) return <Loader2 className="mx-auto mt-10 size-5 animate-spin text-muted-foreground" />;
  const counts = overview.counts;
  const stats: [string, number, boolean][] = [
    [a.stats.users, counts.users, false],
    [a.stats.studios, counts.workspaces, false],
    [a.stats.plans, counts.plans, false],
    [a.stats.pendingPayments, counts.pending_payments, false],
    [a.stats.pendingReconciliation, counts.pending_reconciliation, counts.pending_reconciliation > 0],
    [a.stats.storageAlerts, counts.storage_alerts, counts.storage_alerts > 0],
    [a.stats.stuckJobs, counts.stuck_jobs, counts.stuck_jobs > 0],
  ];

  return (
    // 100dvh minus the 3.5rem top bar and the 3rem of page padding: the page itself never scrolls.
    <div className="flex h-[calc(100dvh-6.5rem)] min-h-0 flex-col gap-3 overflow-hidden">
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold">{a.title}</h1>
          <p className="truncate text-xs text-muted-foreground">{a.subtitle}</p>
          {overview.api_outdated && <p className="text-xs text-warning">{a.apiOutdated}</p>}
        </div>
        <dl className="flex flex-wrap gap-2">
          {stats.map(([label, value, warn]) => (
            <div key={label} className="rounded-lg border border-border bg-surface px-2.5 py-1">
              <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
              <dd className={cn("text-sm font-semibold tabular-nums", warn && "text-warning")}>{formatNumber(value)}</dd>
            </div>
          ))}
        </dl>
      </header>

      <Tabs value={tab} onValueChange={(value) => setTab(value as Tab)} className="flex min-h-0 flex-1 flex-col">
        <TabsList className="scrollbar-thin h-auto w-full shrink-0 justify-start overflow-x-auto sm:w-fit">
          {TABS.map((key) => (
            <TabsTrigger key={key} value={key}>
              {a.tabs[key]}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="users" className="mt-3 flex min-h-0 flex-1 flex-col">
          <AdminUsers plans={overview.plans} selfEmail={dashboard.user.email} />
        </TabsContent>
        <TabsContent value="studios" className="mt-3 flex min-h-0 flex-1 flex-col">
          <AdminStudios plans={overview.plans} />
        </TabsContent>
        {/* relative: the switches' hidden form inputs are absolutely positioned and must not stretch the page. */}
        <TabsContent value="plans" className="scrollbar-thin relative mt-3 min-h-0 flex-1 overflow-y-auto">
          <p className="mb-3 text-xs text-muted-foreground">{a.plans.hint}</p>
          <div className="grid gap-3 lg:grid-cols-3">
            {overview.plans.map((plan) => (
              <PlanForm
                key={`${plan.code}-${plan.name}-${plan.price_vnd}-${plan.is_active}-${plan.monthly_credits}-${plan.storage_limit_bytes}`}
                plan={plan}
              />
            ))}
          </div>
        </TabsContent>
        <TabsContent value="payments" className="mt-3 flex min-h-0 flex-1 flex-col">
          <AdminPayments providers={overview.payment_providers} />
        </TabsContent>
        <TabsContent value="reconciliation" className="mt-3 flex min-h-0 flex-1 flex-col">
          <Reconciliation />
        </TabsContent>
        <TabsContent value="operations" className="mt-3 flex min-h-0 flex-1 flex-col">
          <Operations />
        </TabsContent>
      </Tabs>
    </div>
  );
}
