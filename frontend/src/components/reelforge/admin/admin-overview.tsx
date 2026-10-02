"use client";

import { createContext, useContext, type MouseEvent, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Building2,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  CreditCard,
  Gauge,
  MinusCircle,
  UserCheck,
  Users,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { PlatformIcon, platformLabel } from "@/components/reelforge/primitives";
import { QueryError } from "@/components/reelforge/query-state";
import { useI18n } from "@/lib/i18n";
import { useAdminDashboard } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { AdminAttention, AdminDashboard, AdminHealthRow, AdminTab, HealthStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

type Translate = ReturnType<typeof useI18n>["t"];
type Open = (tab: AdminTab, filter?: string) => void;

// Each tab's filter, as a query parameter of a shareable link (the page reads it when it opens).
const FILTER_PARAM: Partial<Record<AdminTab, string>> = { payments: "status", support: "priority", system: "section" };
const OpenContext = createContext<Open>(() => undefined);

export function adminHref(tab: AdminTab, filter?: string) {
  const param = filter ? FILTER_PARAM[tab] : undefined;
  return `/admin?tab=${tab}${param ? `&${param}=${encodeURIComponent(filter!)}` : ""}`;
}

/** A link to another tab: a real address (a new browser tab works), switched in place on a plain click. */
function OpenLink({ tab, filter, className, label, children }: {
  tab: AdminTab;
  filter?: string;
  className?: string;
  label?: string;
  children: ReactNode;
}) {
  const open = useContext(OpenContext);
  function click(event: MouseEvent<HTMLAnchorElement>) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    open(tab, filter);
  }
  return (
    <a href={adminHref(tab, filter)} onClick={click} aria-label={label}
       className={cn("focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}>
      {children}
    </a>
  );
}

const STATUS_ICON: Record<HealthStatus, { icon: LucideIcon; tone: string }> = {
  healthy: { icon: CheckCircle2, tone: "text-success" },
  warning: { icon: AlertTriangle, tone: "text-warning" },
  critical: { icon: XCircle, tone: "text-destructive" },
  not_configured: { icon: MinusCircle, tone: "text-muted-foreground" },
};

/** A status in words (visible, or for screen readers) and a shape of its own, never only a colour. */
function StatusMark({ status, withLabel = false, large = false }: { status: HealthStatus; withLabel?: boolean; large?: boolean }) {
  const { t } = useI18n();
  const { icon: Icon, tone } = STATUS_ICON[status] ?? STATUS_ICON.warning;
  const label = t.admin.overview.status[status];
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5", tone)}>
      <Icon className={large ? "size-5" : "size-4"} aria-hidden />
      <span className={withLabel ? (large ? "text-lg font-semibold leading-7" : "text-xs font-medium") : "sr-only"}>{label}</span>
    </span>
  );
}

function Panel({ id, title, period, tab, filter, action, children, className }: {
  id: string;
  title: string;
  period?: string;
  tab?: AdminTab;
  filter?: string;
  action?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section aria-labelledby={id} className={cn("panel flex min-w-0 flex-col", className)}>
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border px-4 py-2.5">
        <h2 id={id} className="text-sm font-semibold">
          {title}
          {period && <span className="ml-1.5 text-xs font-normal text-muted-foreground">· {period}</span>}
        </h2>
        {tab && action && (
          <OpenLink tab={tab} filter={filter} className="inline-flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground">
            {action} <ArrowRight className="size-3" aria-hidden />
          </OpenLink>
        )}
      </header>
      <div className="flex-1 p-4">{children}</div>
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">{children}</p>;
}

/** A label and a figure, the figure right-aligned; ``tab`` makes the row open where it is handled. */
function Figure({ label, value, hint, tab, filter, tone }: {
  label: string;
  value: string;
  hint?: string;
  tab?: AdminTab;
  filter?: string;
  tone?: "warning" | "destructive";
}) {
  const body = (
    <>
      <span className="min-w-0">
        <span className="block truncate text-xs text-muted-foreground">{label}</span>
        {hint && <span className="block truncate text-[11px] text-muted-foreground/80">{hint}</span>}
      </span>
      <span className={cn("shrink-0 text-sm font-semibold tabular-nums", tone === "warning" && "text-warning",
                          tone === "destructive" && "text-destructive")}>
        {value}
      </span>
    </>
  );
  const className = "flex items-center justify-between gap-3 rounded-md px-2 py-1.5";
  return tab ? (
    <OpenLink tab={tab} filter={filter} className={cn(className, "transition-colors hover:bg-surface-2")}>{body}</OpenLink>
  ) : (
    <div className={className}>{body}</div>
  );
}

// --- top row --------------------------------------------------------------------------------------------------

function StatTile({ icon: Icon, label, value, hint, tab, status }: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint: string;
  tab: AdminTab;
  status?: HealthStatus;
}) {
  return (
    <OpenLink tab={tab} className="panel group flex min-w-0 flex-col gap-0.5 p-3 transition-colors hover:border-border-strong">
      <span className="flex items-center justify-between gap-2">
        <span className="truncate text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</span>
        <Icon className="size-3.5 shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden />
      </span>
      {status ? (
        <StatusMark status={status} withLabel large />
      ) : (
        <span className="text-lg font-semibold leading-7 tabular-nums">{value}</span>
      )}
      <span className="truncate text-[11px] text-muted-foreground">{hint}</span>
    </OpenLink>
  );
}

function Stats({ data }: { data: AdminDashboard }) {
  const { t, formatNumber, formatMoney } = useI18n();
  const s = t.admin.overview.stats;
  const o = data.overview;
  const days = data.periods.days;
  return (
    <section aria-labelledby="admin-overview-stats">
      <h2 id="admin-overview-stats" className="sr-only">{t.admin.overview.title}</h2>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 xl:grid-cols-7">
        <StatTile icon={Users} label={s.users} value={formatNumber(o.users)} hint={s.usersHint} tab="users" />
        <StatTile icon={UserCheck} label={s.activeUsers} value={formatNumber(o.active_users)} hint={s.activeUsersHint(days)} tab="users" />
        <StatTile icon={Building2} label={s.studios} value={formatNumber(o.workspaces)} hint={s.studiosHint} tab="studios" />
        <StatTile icon={Gauge} label={s.subscriptions} value={formatNumber(o.active_subscriptions)}
                  hint={s.subscriptionsHint(formatNumber(o.paid_subscriptions))} tab="studios" />
        <StatTile icon={CreditCard} label={s.paidVolume(days)} value={formatMoney(o.paid_amount_vnd)}
                  hint={s.paidOrders(formatNumber(o.paid_orders))} tab="payments" />
        <StatTile icon={Activity} label={s.jobs(data.periods.jobs_hours)} value={formatNumber(o.jobs_24h)} hint={s.jobsHint} tab="operations" />
        <StatTile icon={CheckCircle2} label={s.system} value="" hint={s.systemHint} tab="verification" status={o.system_status} />
      </div>
    </section>
  );
}

// --- needs attention ------------------------------------------------------------------------------------------

function attentionText(item: AdminAttention, t: Translate, formatNumber: (n: number) => string, formatRelative: (v: string) => string) {
  const a = t.admin.overview.attention.items;
  const n = formatNumber(item.count ?? 0);
  switch (item.key) {
    case "worker_stale": return a.workerStale(item.worker ?? "");
    case "worker_error": return a.workerError(item.worker ?? "");
    case "workers_missing": return a.workersMissing(n);
    case "disk": return a.disk(`${formatNumber(item.percent ?? 0)}%`);
    case "backup_failed": return a.backupFailed;
    case "backup_overdue": return a.backupOverdue(formatNumber(item.max_age_hours ?? 0));
    case "backup_never": return a.backupNever;
    case "master_key": return a.masterKey;
    case "database": return a.database;
    case "stuck_jobs": return a.stuckJobs(n);
    case "failed_jobs": return a.failedJobs(n);
    case "reconciliation": return a.reconciliation(n, formatNumber(item.credits ?? 0));
    case "transfers": return a.transfers(n);
    case "paid_unapplied": return a.paidUnapplied(n);
    case "callbacks_rejected": return a.callbacksRejected(n);
    case "studios_storage": return a.studiosStorage(n);
    case "email": return item.detail === "failures" ? (item.count ? a.emailFailures(n) : a.emailRetrying) : a.email;
    case "configuration": return a.configuration(n);
    case "support_high": return a.supportHigh(n);
    case "support_waiting": return item.since ? a.supportWaitingSince(n, formatRelative(item.since)) : a.supportWaiting(n);
    default: return null;
  }
}

const SEVERITY_ICON = { critical: XCircle, warning: AlertTriangle, info: CircleDot } as const;
const SEVERITY_TONE = { critical: "text-destructive", warning: "text-warning", info: "text-info" } as const;

function Attention({ data }: { data: AdminDashboard }) {
  const { t, formatNumber, formatRelative } = useI18n();
  const a = t.admin.overview.attention;
  const rows = data.attention.flatMap((item) => {
    const text = attentionText(item, t, formatNumber, formatRelative);
    return text ? [{ item, text }] : [];
  });
  return (
    <section aria-labelledby="admin-overview-attention" className="panel min-w-0">
      <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
        <h2 id="admin-overview-attention" className="text-sm font-semibold">{a.title}</h2>
        {rows.length > 0 && <span className="text-xs text-muted-foreground">{a.count(rows.length)}</span>}
      </header>
      {rows.length === 0 ? (
        <div className="flex items-center gap-3 px-4 py-3">
          <CheckCircle2 className="size-4 shrink-0 text-success" aria-hidden />
          <div>
            <p className="text-sm font-medium">{a.none}</p>
            <p className="text-xs text-muted-foreground">{a.noneHint}</p>
          </div>
        </div>
      ) : (
        <div className="divide-y divide-border">
          {(["critical", "warning", "info"] as const).map((severity) => {
            const group = rows.filter((row) => row.item.severity === severity);
            if (group.length === 0) return null;
            const Icon = SEVERITY_ICON[severity];
            return (
              <div key={severity} className="py-1">
                <p className={cn("px-4 pb-0.5 pt-1.5 text-[11px] font-semibold uppercase tracking-wider", SEVERITY_TONE[severity])}>
                  {a.severity[severity]}
                </p>
                <ul>
                  {group.map(({ item, text }, index) => (
                    <li key={`${item.key}-${index}`}>
                      <OpenLink tab={item.tab} filter={item.filter}
                                className="group flex items-center gap-3 px-4 py-2 transition-colors hover:bg-surface-2/60 focus-visible:ring-inset">
                        <Icon className={cn("size-4 shrink-0", SEVERITY_TONE[severity])} aria-hidden />
                        <span className="min-w-0 flex-1 text-sm">{text}</span>
                        <span className="hidden shrink-0 text-xs text-muted-foreground group-hover:text-foreground sm:inline">
                          {t.admin.tabs[item.tab]}
                        </span>
                        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                      </OpenLink>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// --- panels ---------------------------------------------------------------------------------------------------

const HEALTH_TAB: Record<AdminHealthRow["key"], [AdminTab, string?]> = {
  api: ["operations"], database: ["verification"], workers: ["operations"], storage: ["operations"],
  backups: ["system", "backups"], email: ["system", "email"], configuration: ["verification"],
};

function healthDetail(row: AdminHealthRow, t: Translate, formatNumber: (n: number) => string, formatRelative: (v: string) => string) {
  const h = t.admin.overview.health.details;
  switch (row.key) {
    case "api": return h.api;
    case "database":
      return row.detail === "not_postgresql" ? h.notPostgres : row.detail === "behind" ? h.migrationBehind
        : row.detail === "unreachable" ? h.unreachable : h.database(row.migration ?? "");
    case "workers":
      return row.stale.length ? h.workersStale(row.stale.join(", ")) : h.workers(formatNumber(row.healthy), formatNumber(row.total));
    case "storage":
      return row.percent === undefined || row.total_bytes === undefined ? h.diskUnavailable
        : h.disk(`${formatNumber(row.percent)}%`, formatBytes(row.total_bytes));
    case "backups":
      return row.detail === "failed" ? h.backupFailed : row.detail === "overdue" ? h.backupOverdue
        : row.last_success ? h.backup(formatRelative(row.last_success)) : h.backupNever;
    case "email":
      return row.detail === "disabled" ? h.emailOff : row.failed_24h ? h.emailFailures(formatNumber(row.failed_24h))
        : row.detail === "failures" ? h.emailRetrying : row.detail ? h.emailProblem : h.emailOk;
    case "configuration":
      return h.configuration(formatNumber(row.errors), formatNumber(row.advisories));
  }
}

function Health({ data }: { data: AdminDashboard }) {
  const { t, formatNumber, formatRelative } = useI18n();
  const h = t.admin.overview.health;
  return (
    <Panel id="admin-overview-health" title={h.title} tab="verification" action={t.admin.tabs.verification}>
      <ul className="-mx-2 space-y-0.5">
        {data.health.rows.map((row) => {
          const [tab, filter] = HEALTH_TAB[row.key] ?? ["verification"];
          return (
            <li key={row.key}>
              <OpenLink tab={tab} filter={filter} className="flex items-center gap-3 rounded-md px-2 py-1.5 transition-colors hover:bg-surface-2">
                <StatusMark status={row.status} />
                <span className="w-28 shrink-0 text-sm">{h.rows[row.key]}</span>
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {healthDetail(row, t, formatNumber, formatRelative)}
                </span>
              </OpenLink>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

function rate(succeeded: number, failed: number) {
  return succeeded + failed > 0 ? (succeeded * 100) / (succeeded + failed) : null;
}

function AiUsage({ data }: { data: AdminDashboard }) {
  const { t, formatNumber } = useI18n();
  const u = t.admin.overview.ai;
  const usage = data.ai_usage;
  const tasks = usage.tasks.filter((task) => task.jobs > 0 || task.credits > 0);
  const top = Math.max(1, ...tasks.map((task) => task.jobs));
  const overall = rate(usage.succeeded, usage.failed);
  return (
    <Panel id="admin-overview-ai" title={u.title} period={t.admin.overview.lastDays(data.periods.days)} tab="operations"
           action={t.admin.tabs.operations}>
      {tasks.length === 0 ? (
        <Empty>{u.none}</Empty>
      ) : (
        <>
          <dl className="mb-3 grid grid-cols-3 gap-2">
            <div><dt className="text-[11px] text-muted-foreground">{u.jobs}</dt><dd className="text-base font-semibold tabular-nums">{formatNumber(usage.jobs)}</dd></div>
            <div><dt className="text-[11px] text-muted-foreground">{u.success}</dt><dd className="text-base font-semibold tabular-nums">{overall === null ? "—" : `${formatNumber(Math.round(overall * 10) / 10)}%`}</dd></div>
            <div><dt className="text-[11px] text-muted-foreground">{u.credits}</dt><dd className="text-base font-semibold tabular-nums">{formatNumber(usage.credits)}</dd></div>
          </dl>
          <ul className="space-y-2" aria-label={u.title}>
            {tasks.map((task) => {
              const success = rate(task.succeeded, task.failed);
              return (
                <li key={task.task}>
                  <div className="flex items-baseline justify-between gap-2 text-xs">
                    <span className="font-medium">{u.tasks[task.task]}</span>
                    <span className="truncate text-muted-foreground">
                      {[u.jobCount(formatNumber(task.jobs)),
                        success === null ? null : u.successRate(`${formatNumber(Math.round(success * 10) / 10)}%`),
                        task.failed ? u.failedCount(formatNumber(task.failed)) : null,
                        task.credits ? u.creditCount(formatNumber(task.credits)) : null].filter(Boolean).join(" · ")}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
                    <div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(2, (task.jobs / top) * 100)}%` }} />
                  </div>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-[11px] text-muted-foreground">{u.definition}</p>
        </>
      )}
    </Panel>
  );
}

function Payments({ data }: { data: AdminDashboard }) {
  const { t, formatNumber, formatMoney } = useI18n();
  const p = t.admin.overview.payments;
  const pay = data.payments;
  const days = data.periods.days;
  return (
    <Panel id="admin-overview-payments" title={p.title} tab="payments" action={t.admin.tabs.payments}>
      <div className="mb-2 flex items-baseline justify-between gap-3 px-2">
        <span className="text-xs text-muted-foreground">{p.paidVolume(days)}</span>
        <span className="text-lg font-semibold tabular-nums">{formatMoney(pay.paid_amount_vnd)}</span>
      </div>
      <div className="-mx-2 grid gap-x-4 sm:grid-cols-2">
        <Figure label={p.paid(days)} value={formatNumber(pay.paid)} tab="payments" filter="paid" />
        <Figure label={p.pending} hint={p.now} value={formatNumber(pay.pending)} tab="payments" filter="pending" />
        <Figure label={p.awaiting} hint={p.now} value={formatNumber(pay.awaiting_confirmation)} tab="payments"
                filter="awaiting_confirmation" tone={pay.awaiting_confirmation ? "warning" : undefined} />
        <Figure label={p.unapplied} hint={p.now} value={formatNumber(pay.paid_unapplied)} tab="payments"
                filter="paid_unapplied" tone={pay.paid_unapplied ? "warning" : undefined} />
        <Figure label={p.failed(days)} value={formatNumber(pay.failed)} tab="payments" filter="failed"
                tone={pay.failed ? "destructive" : undefined} />
      </div>
      {pay.by_provider.length > 0 && (
        <div className="mt-3 border-t border-border pt-3">
          <p className="mb-1 px-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{p.byMethod}</p>
          <ul className="space-y-1 px-2 text-xs">
            {pay.by_provider.map((item) => (
              <li key={item.provider} className="flex items-center justify-between gap-3">
                <span>{t.admin.payments.providers[item.provider as keyof typeof t.admin.payments.providers] ?? item.provider}</span>
                <span className="tabular-nums text-muted-foreground">
                  {p.providerLine(formatNumber(item.paid), formatMoney(item.amount_vnd))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function Credits({ data }: { data: AdminDashboard }) {
  const { t, formatNumber } = useI18n();
  const c = t.admin.overview.credits;
  const credit = data.credits;
  const days = data.periods.days;
  return (
    <Panel id="admin-overview-credits" title={c.title} tab="studios" action={t.admin.tabs.studios}>
      <div className="-mx-2 grid gap-x-4 sm:grid-cols-2">
        <Figure label={c.addedByPlans} hint={t.admin.overview.lastDays(days)} value={formatNumber(credit.added_by_plans)} />
        <Figure label={c.admin} hint={c.adminHint(formatNumber(credit.admin_adjustments))}
                value={`+${formatNumber(credit.admin_added)} / −${formatNumber(credit.admin_removed)}`} />
        <Figure label={c.refunded} hint={t.admin.overview.lastDays(days)} value={formatNumber(credit.refunded)} />
        <Figure label={c.consumed} hint={c.consumedHint(days)} value={formatNumber(credit.consumed)} />
        <Figure label={c.available} hint={c.availableHint} value={formatNumber(credit.available)} />
        <Figure label={c.held} hint={c.heldHint(formatNumber(credit.held_jobs))} value={formatNumber(credit.held)}
                tab="reconciliation" tone={credit.held ? "warning" : undefined} />
        {credit.other_added > 0 && (
          <Figure label={c.other} hint={t.admin.overview.lastDays(days)} value={formatNumber(credit.other_added)} />
        )}
      </div>
    </Panel>
  );
}

function Growth({ data }: { data: AdminDashboard }) {
  const { t, formatNumber, formatDate } = useI18n();
  const g = t.admin.overview.growth;
  const growth = data.growth;
  const users = growth.users.reduce((sum, n) => sum + n, 0);
  const studios = growth.workspaces.reduce((sum, n) => sum + n, 0);
  const top = Math.max(1, ...growth.users, ...growth.workspaces);
  const summary = g.summary(formatNumber(users), formatNumber(studios), data.periods.days);
  return (
    <Panel id="admin-overview-growth" title={g.title} period={t.admin.overview.lastDays(data.periods.days)} tab="users"
           action={t.admin.tabs.users}>
      <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-xs">
        <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-sm bg-primary" aria-hidden />{g.users}: <b className="tabular-nums">{formatNumber(users)}</b></span>
        <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-sm bg-info" aria-hidden />{g.studios}: <b className="tabular-nums">{formatNumber(studios)}</b></span>
      </div>
      {users + studios === 0 ? (
        <Empty>{g.none}</Empty>
      ) : (
        // One small chart: a pair of bars per day, new users and new studios; the sentence above it says the totals.
        <div role="img" aria-label={summary} className="flex h-24 items-end gap-px">
          {growth.days.map((day, index) => (
            <div key={day} className="flex h-full min-w-0 flex-1 items-end gap-px"
                 title={g.dayTitle(formatDate(day), formatNumber(growth.users[index] ?? 0), formatNumber(growth.workspaces[index] ?? 0))}>
              <span className="w-1/2 rounded-t-sm bg-primary/80" style={{ height: `${((growth.users[index] ?? 0) / top) * 100}%` }} />
              <span className="w-1/2 rounded-t-sm bg-info/70" style={{ height: `${((growth.workspaces[index] ?? 0) / top) * 100}%` }} />
            </div>
          ))}
        </div>
      )}
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground" aria-hidden>
        <span>{formatDate(growth.days[0] ?? data.periods.growth_from)}</span>
        <span>{formatDate(growth.days[growth.days.length - 1] ?? data.generated_at)}</span>
      </div>
    </Panel>
  );
}

function Publishing({ data }: { data: AdminDashboard }) {
  const { t, formatNumber } = useI18n();
  const p = t.admin.overview.publishing;
  const pub = data.publishing;
  const days = data.periods.days;
  return (
    <Panel id="admin-overview-publishing" title={p.title} period={t.admin.overview.lastDays(days)}>
      {pub.channels.length === 0 ? (
        <Empty>{p.none}</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-wider text-muted-foreground">
            <tr>
              <th scope="col" className="pb-1.5 font-medium"><span className="sr-only">{p.channel}</span></th>
              <th scope="col" className="pb-1.5 text-right font-medium">{p.published}</th>
              <th scope="col" className="pb-1.5 text-right font-medium">{p.scheduled}</th>
              <th scope="col" className="pb-1.5 text-right font-medium">{p.failed}</th>
            </tr>
          </thead>
          <tbody>
            {pub.channels.map((channel) => (
              <tr key={channel.channel} className="border-t border-border">
                <th scope="row" className="py-1.5 text-left font-normal">
                  <span className="inline-flex items-center gap-2">
                    <PlatformIcon platform={channel.channel} />
                    {platformLabel[channel.channel] ?? channel.channel}
                    {!channel.configured && <span className="text-[11px] text-muted-foreground">({p.notConfigured})</span>}
                  </span>
                </th>
                <td className="py-1.5 text-right tabular-nums">{formatNumber(channel.published)}</td>
                <td className="py-1.5 text-right tabular-nums">{formatNumber(channel.scheduled)}</td>
                <td className={cn("py-1.5 text-right tabular-nums", channel.failed > 0 && "text-destructive")}>
                  {formatNumber(channel.failed)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">{p.definition(days)}</p>
    </Panel>
  );
}

function Storage({ data }: { data: AdminDashboard }) {
  const { t, formatNumber } = useI18n();
  const s = t.admin.overview.storage;
  const store = data.storage;
  const disk = store.disk;
  return (
    <Panel id="admin-overview-storage" title={s.title} tab="operations" action={t.admin.tabs.operations}>
      <div className="-mx-2">
        <Figure label={s.stored} hint={s.files(formatNumber(store.files))} value={formatBytes(store.stored_bytes)} />
        {disk ? (
          <div className="px-2 py-1.5">
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="text-muted-foreground">{s.disk}</span>
              <span className="font-semibold tabular-nums">
                {s.diskUsage(formatBytes(disk.used_bytes), formatBytes(disk.total_bytes))}
              </span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
              <div className={cn("h-full rounded-full bg-primary", disk.percent >= 80 && "bg-warning", disk.percent >= 90 && "bg-destructive")}
                   style={{ width: `${Math.min(100, disk.percent)}%` }} />
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">{s.diskFree(formatBytes(disk.free_bytes), `${formatNumber(disk.percent)}%`)}</p>
          </div>
        ) : (
          <Figure label={s.disk} value={s.diskUnavailable} />
        )}
        <div className="grid grid-cols-3 gap-x-2">
          <Figure label={s.levels.warning} value={formatNumber(store.levels.warning)} tab="operations"
                  tone={store.levels.warning ? "warning" : undefined} />
          <Figure label={s.levels.critical} value={formatNumber(store.levels.critical)} tab="operations"
                  tone={store.levels.critical ? "warning" : undefined} />
          <Figure label={s.levels.full} value={formatNumber(store.levels.full)} tab="operations"
                  tone={store.levels.full ? "destructive" : undefined} />
        </div>
      </div>
    </Panel>
  );
}

function Support({ data }: { data: AdminDashboard }) {
  const { t, formatNumber, formatRelative } = useI18n();
  const s = t.admin.overview.support;
  const sup = data.support;
  return (
    <Panel id="admin-overview-support" title={s.title} tab="support" action={t.admin.tabs.support}>
      <div className="-mx-2 grid gap-x-4 sm:grid-cols-2">
        <Figure label={s.awaiting} value={formatNumber(sup.awaiting_support)} tab="support"
                tone={sup.awaiting_support ? "warning" : undefined} />
        <Figure label={s.high} value={formatNumber(sup.high_priority)} tab="support" filter="high"
                tone={sup.high_priority ? "warning" : undefined} />
        <Figure label={s.waitingUser} value={formatNumber(sup.waiting_user)} tab="support" />
        <Figure label={s.oldest} value={sup.oldest_waiting_since ? formatRelative(sup.oldest_waiting_since) : "—"} tab="support" />
      </div>
    </Panel>
  );
}

function RecentActivity({ data }: { data: AdminDashboard }) {
  const { t, formatRelative, formatDateTime } = useI18n();
  const r = t.admin.overview.activity;
  return (
    <Panel id="admin-overview-activity" title={r.title} tab="audit" action={t.admin.tabs.audit}>
      {data.activity.length === 0 ? (
        <Empty>{r.none}</Empty>
      ) : (
        <ul className="divide-y divide-border text-sm">
          {data.activity.map((event) => (
            <li key={event.id} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1.5">
              <span className="min-w-0">
                <span className={cn("font-medium", event.outcome !== "success" && "text-warning")}>
                  {t.auditActions[event.action as keyof typeof t.auditActions] ?? event.action}
                </span>
                {event.actor && <span className="ml-2 text-xs text-muted-foreground">{event.actor}</span>}
              </span>
              {event.created_at && (
                <time className="text-xs text-muted-foreground" dateTime={event.created_at} title={formatDateTime(event.created_at)}>
                  {formatRelative(event.created_at)}
                </time>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Loading() {
  return (
    <div className="space-y-4" aria-busy="true">
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 xl:grid-cols-7">
        {Array.from({ length: 7 }, (_, index) => <Skeleton key={index} className="h-[4.75rem] rounded-xl" />)}
      </div>
      <Skeleton className="h-28 rounded-xl" />
      <div className="grid gap-4 xl:grid-cols-2">
        {Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-52 rounded-xl" />)}
      </div>
    </div>
  );
}

/**
 * Admin → Overview: the installation at a glance from one request (GET /api/admin/overview). Every card opens the
 * tab that handles it; nothing is managed here.
 */
export function AdminOverview({ onOpen }: { onOpen: Open }) {
  const { t, formatRelative } = useI18n();
  const query = useAdminDashboard(true);
  const data = query.data;
  if (query.isError && !data) return <QueryError error={query.error} onRetry={() => void query.refetch()} />;
  if (!data) return <Loading />;
  return (
    <OpenContext.Provider value={onOpen}>
      <div className="space-y-4 pb-1">
        <Stats data={data} />
        <Attention data={data} />
        <div className="grid gap-4 xl:grid-cols-2">
          <Health data={data} />
          <AiUsage data={data} />
          <Payments data={data} />
          <Credits data={data} />
          <Growth data={data} />
          <Publishing data={data} />
          <Storage data={data} />
          <Support data={data} />
        </div>
        <RecentActivity data={data} />
        <p className="text-right text-[11px] text-muted-foreground">{t.admin.overview.updated(formatRelative(data.generated_at))}</p>
      </div>
    </OpenContext.Provider>
  );
}
