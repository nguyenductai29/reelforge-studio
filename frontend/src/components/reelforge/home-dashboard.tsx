"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Coins,
  Film,
  FolderKanban,
  HardDrive,
  Loader2,
  Recycle,
  Send,
  Smartphone,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { MiniDiagram } from "@/components/workflow/mini-diagram";
import { assetUrl } from "@/lib/api";
import { useCreateFromTemplate } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { formatBytes, tintFor } from "@/lib/studio";
import type { ChannelId, HomeAttention, HomeProject, HomeRunItem, HomeSummary, Workflow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { previewKinds, templateById, type TemplateId } from "@/lib/workflow";
import { PlatformIcon, platformLabel, SectionLink, StatusBadge } from "./primitives";

type Translate = ReturnType<typeof useI18n>["t"];

const CARD_LINK =
  "panel group min-w-0 transition-colors hover:border-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const STORAGE_WARNING = new Set(["warning", "critical", "full"]);

const runHref = (item: { workflow_id: string; run_id: string }) =>
  `/workflows/${encodeURIComponent(item.workflow_id)}?run=${encodeURIComponent(item.run_id)}`;

/** A titled block of Home: its heading (naming the region) and an optional link to the full page. */
export function HomeSection({ id, title, action, children, className }: {
  id: string;
  title: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section aria-labelledby={id} className={cn("flex min-w-0 flex-col", className)}>
      <div className="mb-3 flex min-h-7 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h2 id={id} className="text-base font-semibold tracking-tight">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function ViewAll({ href, children }: { href: string; children: ReactNode }) {
  return (
    <SectionLink href={href}>
      {children} <ArrowRight className="size-3.5" aria-hidden />
    </SectionLink>
  );
}

/** A section's own empty state: compact, with the next step when the member can take it. */
function Empty({ title, hint, action, className }: { title: string; hint: string; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("panel flex flex-col items-start gap-1 p-4", className)}>
      <p className="text-sm font-medium">{title}</p>
      <p className="text-xs text-muted-foreground">{hint}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

// --- Overview -----------------------------------------------------------------------------------------------

function StatTile({ href, icon: Icon, label, value, hint, warning, className, children }: {
  href: string;
  icon: LucideIcon;
  label: string;
  value: string;
  hint: string;
  warning?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <Link href={href} className={cn(CARD_LINK, "flex flex-col gap-1 p-3.5", className)}>
      <span className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-muted-foreground">{label}</span>
        <Icon className={cn("size-4 shrink-0 text-muted-foreground group-hover:text-primary", warning && "text-warning")} aria-hidden />
      </span>
      <span className={cn("text-xl font-semibold leading-tight tabular-nums", warning && "text-warning")}>{value}</span>
      <span className="truncate text-[11px] text-muted-foreground">{hint}</span>
      {children}
    </Link>
  );
}

// Five tiles without a hole: two columns on a phone (storage spans both), 3 + 2 on a tablet, one row on a desktop.
const TILE_SPANS = ["sm:col-span-2", "sm:col-span-2", "sm:col-span-2", "sm:col-span-3", "col-span-2 sm:col-span-3"];

export function OverviewStats({ data }: { data?: HomeSummary }) {
  const { t, formatNumber } = useI18n();
  const s = t.home.stats;
  if (!data) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-6 lg:grid-cols-5" aria-busy="true">
        {TILE_SPANS.map((span, index) => (
          <Skeleton key={index} className={cn("h-[5.5rem] rounded-xl lg:col-span-1", span)} />
        ))}
      </div>
    );
  }
  const o = data.overview;
  const low = o.credits_low_threshold > 0 && o.credits < o.credits_low_threshold;
  const crowded = STORAGE_WARNING.has(o.storage_level);
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-6 lg:grid-cols-5">
      <StatTile href="/billing" icon={Coins} label={s.credits} value={formatNumber(o.credits)} warning={low}
                hint={o.credits_monthly ? s.creditsMonthly(formatNumber(o.credits_monthly)) : s.creditsAvailable}
                className={cn(TILE_SPANS[0], "lg:col-span-1")} />
      <StatTile href="/projects" icon={FolderKanban} label={s.projects} value={formatNumber(o.projects)}
                hint={o.projects_limit !== null ? s.projectsLimit(formatNumber(o.projects_limit)) : s.projectsAll}
                className={cn(TILE_SPANS[1], "lg:col-span-1")} />
      <StatTile href="/workflows" icon={Activity} label={s.runs(data.period_days)} value={formatNumber(o.runs_30d)}
                hint={o.runs_active > 0 ? s.runsActive(o.runs_active) : s.runsIdle}
                className={cn(TILE_SPANS[2], "lg:col-span-1")} />
      <StatTile href="/publishing" icon={Send} label={s.published(data.period_days)} value={formatNumber(o.published_30d)}
                hint={s.publishedHint} className={cn(TILE_SPANS[3], "lg:col-span-1")} />
      <StatTile href="/settings?tab=storage" icon={HardDrive} label={s.storage} value={formatBytes(o.storage_used_bytes)}
                hint={s.storageOf(formatBytes(o.storage_quota_bytes), `${formatNumber(o.storage_percent)}%`)} warning={crowded}
                className={cn(TILE_SPANS[4], "lg:col-span-1")}>
        <span className="mt-1 block h-1 overflow-hidden rounded-full bg-surface-2" aria-hidden>
          <span className={cn("block h-full rounded-full bg-primary", crowded && "bg-warning")}
                style={{ width: `${Math.min(100, Math.max(o.storage_percent, o.storage_used_bytes > 0 ? 2 : 0))}%` }} />
        </span>
      </StatTile>
    </div>
  );
}

// --- Needs attention ----------------------------------------------------------------------------------------

/** What a row says, the action it offers and where it leads; null for a kind this version does not know. */
function attentionRow(item: HomeAttention, t: Translate, formatNumber: (n: number) => string) {
  const a = t.home.attention;
  const run = (entry: HomeRunItem) => runHref(entry);
  switch (item.kind) {
    case "plan_inactive":
      return { text: a.planInactive, action: a.actions.plan, href: "/billing" };
    case "review":
      return { text: a.review(item.count), action: a.actions.review, href: run(item) };
    case "failed_runs":
      return { text: a.failedRuns(item.count), action: a.actions.open, href: run(item) };
    case "blocked_runs":
      return { text: a.blockedRuns(item.count), action: a.actions.open, href: run(item) };
    case "publish_failed":
      return { text: a.publishFailed(item.count), action: a.actions.open, href: "/publishing" };
    case "channel_reconnect": {
      const name = platformLabel[item.channel] ?? item.channel;
      return {
        text: item.scheduled > 0 ? a.reconnectScheduled(name, item.scheduled) : a.reconnect(name),
        action: a.actions.reconnect,
        href: "/channels",
      };
    }
    case "credits_low":
      return { text: a.creditsLow(formatNumber(item.balance)), action: a.actions.plan, href: "/billing" };
    case "storage":
      return { text: a.storage(`${formatNumber(item.percent)}%`), action: a.actions.clean, href: "/settings?tab=storage" };
    case "support_reply":
      return {
        text: a.supportReply(item.count),
        action: a.actions.reply,
        href: item.count === 1 ? `/support/${encodeURIComponent(item.ticket_id)}` : "/support",
      };
    case "reconciliation":
      return { text: a.reconciliation(item.count), action: a.actions.view, href: run(item) };
    case "generating":
      return { text: a.generating(item.count), action: a.actions.view, href: run(item) };
    default:
      return null;
  }
}

export function AttentionPanel({ data }: { data?: HomeSummary }) {
  const { t, formatNumber } = useI18n();
  const a = t.home.attention;
  if (!data) {
    return (
      <div className="panel divide-y divide-border" aria-busy="true">
        {[0, 1, 2].map((index) => (
          <div key={index} className="flex items-center gap-3 px-4 py-3">
            <Skeleton className="size-4 rounded-full" />
            <Skeleton className="h-4 flex-1" />
          </div>
        ))}
      </div>
    );
  }
  const rows = data.attention.flatMap((item) => {
    const row = attentionRow(item, t, formatNumber);
    return row ? [{ item, row }] : [];
  });
  if (rows.length === 0) {
    return (
      <div className="panel flex items-center gap-3 p-4">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-[color-mix(in_oklab,var(--success)_15%,transparent)]">
          <CheckCircle2 className="size-4 text-success" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium">{a.none}</p>
          <p className="text-xs text-muted-foreground">{a.noneHint}</p>
        </div>
      </div>
    );
  }
  return (
    <ul className="panel divide-y divide-border overflow-hidden">
      {rows.map(({ item, row }, index) => (
        <li key={`${item.kind}-${index}`}>
          <Link
            href={row.href}
            className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2/60 focus-visible:bg-surface-2/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            {/* The shape tells a warning from an update, not only the colour. */}
            {item.severity === "warning" ? (
              <AlertTriangle className="size-4 shrink-0 text-warning" aria-hidden />
            ) : (
              <CircleDot className="size-4 shrink-0 text-info" aria-hidden />
            )}
            <span className="min-w-0 flex-1 text-sm">{row.text}</span>
            <span className="hidden shrink-0 text-xs text-muted-foreground group-hover:text-foreground sm:inline">
              {row.action}
            </span>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </Link>
        </li>
      ))}
    </ul>
  );
}

// --- Quick create -------------------------------------------------------------------------------------------

// The shortcuts used most; every template is one click away on the Create page.
const QUICK: { id: TemplateId; platform?: ChannelId; icon?: LucideIcon; tint?: string }[] = [
  { id: "youtube-video", platform: "youtube" },
  { id: "youtube-short", icon: Smartphone, tint: "text-yt" },
  { id: "tiktok-video", platform: "tiktok" },
  { id: "facebook-reel", platform: "facebook" },
  { id: "movie-recap", icon: Film },
  { id: "repurpose", icon: Recycle },
];

export function QuickCreate() {
  const { t } = useI18n();
  const { create, pending } = useCreateFromTemplate();
  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-2">
      {QUICK.map((item) => {
        const template = templateById(item.id);
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            type="button"
            disabled={Boolean(pending)}
            onClick={() => void create(item.id)}
            className={cn(CARD_LINK, "flex items-center gap-3 p-3 text-left disabled:cursor-wait disabled:opacity-70")}
          >
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 group-hover:border-primary/40">
              {pending === item.id ? (
                <Loader2 className="size-4 animate-spin text-primary" aria-hidden />
              ) : item.platform ? (
                <PlatformIcon platform={item.platform} />
              ) : Icon ? (
                <Icon className={cn("size-4 text-primary", item.tint)} aria-hidden />
              ) : null}
            </span>
            <span className="min-w-0">
              <span className="line-clamp-2 text-sm font-medium leading-snug group-hover:text-primary">
                {t.templates[item.id].name}
              </span>
              {template.steps ? (
                <span className="block text-[11px] text-muted-foreground">{t.common.steps(template.steps)}</span>
              ) : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// --- Recent workflows ---------------------------------------------------------------------------------------

export function RecentWorkflows({ data, workflows, canCreate }: {
  data?: HomeSummary;
  workflows: Workflow[];
  canCreate: boolean;
}) {
  const { t, formatRelative } = useI18n();
  if (!data) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-busy="true">
        {[0, 1, 2, 3].map((index) => <Skeleton key={index} className="h-[9.5rem] rounded-xl" />)}
      </div>
    );
  }
  if (data.recent_workflows.length === 0) {
    return (
      <Empty
        title={t.home.noWorkflows}
        hint={t.home.noWorkflowsHint}
        action={canCreate && (
          <Button asChild size="sm" variant="outline">
            <Link href="/create">{t.home.startTemplate}</Link>
          </Button>
        )}
      />
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {data.recent_workflows.map((item) => {
        const graph = workflows.find((workflow) => workflow.id === item.id)?.graph;
        const run = item.last_run;
        const meta = [graph ? t.common.steps(graph.nodes.length) : null,
                      run ? t.home.lastRun(formatRelative(run.created_at)) : null].filter(Boolean).join(" · ");
        return (
          <Link key={item.id} href={`/workflows/${encodeURIComponent(item.id)}`} className={cn(CARD_LINK, "flex flex-col gap-3 p-3.5")}>
            {graph ? <MiniDiagram kinds={previewKinds(graph)} className="h-14" /> : <Skeleton className="h-14 rounded-lg" />}
            <div className="min-w-0">
              <p className="truncate text-sm font-medium group-hover:text-primary" title={item.name}>{item.name}</p>
              <p className="truncate text-xs text-muted-foreground">{meta || t.home.notRun}</p>
            </div>
            <div className="mt-auto">
              {run ? (
                <StatusBadge status={run.status} label={t.status.run[run.status] ?? run.status} />
              ) : (
                <StatusBadge status="draft" label={t.workflows.neverRun} />
              )}
            </div>
          </Link>
        );
      })}
    </div>
  );
}

// --- Recent projects ----------------------------------------------------------------------------------------

/** The project's newest image, else its colour; a file removed since (410) falls back to the colour. */
function ProjectCover({ project }: { project: HomeProject }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={cn("relative h-24 w-full overflow-hidden bg-gradient-to-br", tintFor(project.id))}>
      {project.cover_asset_id && !failed && (
        // eslint-disable-next-line @next/next/no-img-element -- media is served by the API, not next/image
        <img src={assetUrl(project.cover_asset_id)} alt="" loading="lazy" onError={() => setFailed(true)}
             className="size-full object-cover" />
      )}
    </div>
  );
}

export function RecentProjects({ data, canCreate }: { data?: HomeSummary; canCreate: boolean }) {
  const { t, formatRelative } = useI18n();
  if (!data) {
    return (
      <div className="grid gap-3 sm:grid-cols-2" aria-busy="true">
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <Skeleton key={index} className={cn("h-[11.5rem] rounded-xl", index >= 4 && "hidden sm:block")} />
        ))}
      </div>
    );
  }
  if (data.recent_projects.length === 0) {
    return (
      <Empty
        title={t.home.noProjects}
        hint={t.home.noProjectsHint}
        action={canCreate && (
          <Button asChild size="sm" variant="outline">
            <Link href="/projects">{t.projects.newProject}</Link>
          </Button>
        )}
      />
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {data.recent_projects.map((project, index, all) => (
        // Six beside the usage and publishing panels (an odd last one spans both columns); four on a phone.
        <Link key={project.id} href={`/projects/${encodeURIComponent(project.id)}`}
              className={cn(CARD_LINK, "flex-col overflow-hidden", index < 4 ? "flex" : "hidden sm:flex",
                            all.length % 2 === 1 && index === all.length - 1 && "sm:col-span-2")}>
          <ProjectCover project={project} />
          <div className="flex flex-1 flex-col gap-1.5 p-3.5">
            <div className="flex items-start justify-between gap-2">
              <p className="line-clamp-1 min-w-0 font-medium group-hover:text-primary">{project.title}</p>
              <StatusBadge status={project.status} label={t.status.project[project.status]} />
            </div>
            <p className="line-clamp-1 text-xs text-muted-foreground">{project.topic.trim() || t.projects.noTopic}</p>
            <div className="mt-auto flex items-center justify-between gap-2 pt-1 text-[11px] text-muted-foreground">
              <span className="truncate">
                {[t.projects.videos(project.videos),
                  project.last_activity_at ? t.home.activity(formatRelative(project.last_activity_at)) : null]
                  .filter(Boolean).join(" · ")}
              </span>
              {project.channels.length > 0 && (
                <span className="flex shrink-0 items-center gap-1">
                  {project.channels.map((channel) => (
                    <span key={channel} title={platformLabel[channel]}>
                      <PlatformIcon platform={channel} className="size-3.5" />
                      <span className="sr-only">{platformLabel[channel]}</span>
                    </span>
                  ))}
                </span>
              )}
            </div>
          </div>
        </Link>
      ))}
    </div>
  );
}

// --- Usage and publishing -----------------------------------------------------------------------------------

export function UsagePanel({ data }: { data?: HomeSummary }) {
  const { t, formatNumber } = useI18n();
  const u = t.home.usage;
  if (!data) return <Skeleton className="h-48 rounded-xl" aria-busy="true" />;
  const tasks = data.usage.by_task;
  const top = Math.max(1, ...tasks.map((entry) => entry.credits));
  return (
    <div className="panel p-4">
      <dl className="grid grid-cols-2 gap-3">
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{u.used}</dt>
          <dd className="text-lg font-semibold tabular-nums">{u.credits(formatNumber(data.usage.credits_used))}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{u.remaining}</dt>
          <dd className="text-lg font-semibold tabular-nums">{u.credits(formatNumber(data.overview.credits))}</dd>
        </div>
      </dl>
      {tasks.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-border px-3 py-4 text-center">
          <p className="text-sm font-medium">{u.none}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{u.noneHint}</p>
        </div>
      ) : (
        <ul className="mt-4 space-y-2.5" aria-label={u.title}>
          {tasks.map((entry) => (
            <li key={entry.task}>
              <div className="flex items-center justify-between gap-2 text-xs">
                <span>{u.tasks[entry.task] ?? entry.task}</span>
                <span className="tabular-nums text-muted-foreground">{u.credits(formatNumber(entry.credits))}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
                <div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(3, (entry.credits / top) * 100)}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PublishingPanel({ data, canConnect }: { data?: HomeSummary; canConnect: boolean }) {
  const { t, formatNumber } = useI18n();
  const p = t.home.publishing;
  if (!data) return <Skeleton className="h-44 rounded-xl" aria-busy="true" />;
  const publishing = data.publishing;
  if (publishing.channels.length === 0) {
    return (
      <Empty
        title={p.none}
        hint={p.noneHint}
        action={canConnect && (
          <Button asChild size="sm" variant="outline">
            <Link href="/channels">{p.connect}</Link>
          </Button>
        )}
      />
    );
  }
  const published = p.published(data.period_days);
  return (
    <div className="panel p-4">
      <p className="mb-2 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground" aria-hidden>
        {published}
      </p>
      <ul className="space-y-2.5">
        {publishing.channels.map((channel) => (
          <li key={channel.channel} className="flex items-center gap-2.5 text-sm">
            <PlatformIcon platform={channel.channel} />
            <span className="min-w-0 flex-1 truncate">{platformLabel[channel.channel] ?? channel.channel}</span>
            {channel.status === "authorization_required" ? (
              <span className="shrink-0 rounded-full bg-[color-mix(in_oklab,var(--warning)_18%,transparent)] px-2 py-0.5 text-[11px] text-warning">
                {p.reconnect}
              </span>
            ) : channel.status !== "connected" ? (
              <span className="shrink-0 text-[11px] text-muted-foreground">{p.notConnected}</span>
            ) : null}
            <span className="w-10 shrink-0 text-right font-semibold tabular-nums">
              <span className="sr-only">{published}: </span>
              {formatNumber(channel.published)}
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-3 grid grid-cols-2 gap-2 border-t border-border pt-3">
        <Link href="/calendar" className="rounded-lg bg-surface-2 px-3 py-2 transition-colors hover:bg-surface-2/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span className="block text-[11px] text-muted-foreground">{p.scheduled}</span>
          <span className="block text-base font-semibold tabular-nums">{formatNumber(publishing.scheduled)}</span>
        </Link>
        <Link href="/publishing" className="rounded-lg bg-surface-2 px-3 py-2 transition-colors hover:bg-surface-2/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span className="block text-[11px] text-muted-foreground">{p.failed}</span>
          <span className={cn("block text-base font-semibold tabular-nums", publishing.failed > 0 && "text-destructive")}>
            {formatNumber(publishing.failed)}
          </span>
        </Link>
      </div>
    </div>
  );
}

// --- Recent runs --------------------------------------------------------------------------------------------

export function RecentRuns({ data }: { data?: HomeSummary }) {
  const { t, formatRelative, formatDateTime } = useI18n();
  const c = t.home.runColumns;
  if (!data) {
    return (
      <div className="panel divide-y divide-border" aria-busy="true">
        {[0, 1, 2].map((index) => (
          <div key={index} className="flex items-center gap-4 px-4 py-3">
            <Skeleton className="h-4 flex-1" />
            <Skeleton className="h-5 w-24 rounded-full" />
          </div>
        ))}
      </div>
    );
  }
  if (data.recent_runs.length === 0) return <Empty title={t.home.noRuns} hint={t.home.noRunsHint} />;
  return (
    <div className="panel overflow-hidden">
      <table className="w-full table-fixed text-sm">
        <thead className="border-b border-border text-left text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th scope="col" className="px-4 py-2.5 font-medium">{c.project}</th>
            <th scope="col" className="hidden px-4 py-2.5 font-medium md:table-cell">{c.workflow}</th>
            <th scope="col" className="w-40 px-4 py-2.5 font-medium">{c.status}</th>
            <th scope="col" className="hidden w-36 px-4 py-2.5 text-right font-medium sm:table-cell">{c.started}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {data.recent_runs.map((run) => {
            const when = formatRelative(run.created_at);
            return (
              <tr key={run.id} className="transition-colors hover:bg-surface-2/40">
                <td className="px-4 py-2.5">
                  <Link href={runHref({ workflow_id: run.workflow_id, run_id: run.id })}
                        className="block truncate font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                    {run.project_title ?? t.editor.runs.unknownProject}
                  </Link>
                  <span className="block truncate text-xs text-muted-foreground md:hidden">
                    {[run.workflow_name, when].filter(Boolean).join(" · ")}
                  </span>
                </td>
                <td className="hidden truncate px-4 py-2.5 text-muted-foreground md:table-cell">{run.workflow_name ?? "—"}</td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={run.status} label={t.status.run[run.status] ?? run.status} />
                </td>
                <td className="hidden px-4 py-2.5 text-right text-xs text-muted-foreground sm:table-cell">
                  <time dateTime={run.created_at} title={formatDateTime(run.created_at)}>{when}</time>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
