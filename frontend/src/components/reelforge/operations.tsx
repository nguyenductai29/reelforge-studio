"use client";

import { useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { useAdminJobs, useAdminStorage, useAdminWorkers } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { AdminJob, StuckJob, WorkerHealth } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DataTable, type Column } from "./data-table";
import { FilterSelect } from "./admin/shared";
import { StatusBadge } from "./primitives";
import { STORAGE_TEXT, StorageBar } from "./storage";

const LIMIT = 25;
const STATES = ["queued", "leased", "succeeded", "failed"] as const;
const QUEUES = ["text", "image", "video", "voice", "render", "source", "publish"] as const;
const WORKER_TONE: Record<WorkerHealth["status"], string> = {
  ok: "connected",
  stale: "failed",
  error: "failed",
  missing: "not connected",
};

function StuckList({ title, jobs }: { title: string; jobs: StuckJob[] }) {
  const { formatRelative } = useI18n();
  if (!jobs.length) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium">{title}</p>
      <ul className="space-y-0.5 text-xs text-muted-foreground">
        {jobs.map((job) => (
          <li key={job.id} className="flex flex-wrap gap-x-3 font-mono">
            <span>{job.queue}</span>
            <span>{job.id.slice(0, 8)}</span>
            <span>×{job.attempt_count}</span>
            {job.worker_id && <span>{job.worker_id}</span>}
            {(job.lease_expires_at ?? job.available_at) && <span>{formatRelative((job.lease_expires_at ?? job.available_at)!)}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * System operations for administrators, fitted to the admin console: worker heartbeats, the stuck-work
 * audit and storage above, and every recent job (identifiers and timing only, never payloads) below.
 */
export function Operations() {
  const { t, formatRelative, formatDateTime } = useI18n();
  const o = t.admin.operations;
  const [state, setState] = useState("");
  const [queue, setQueue] = useState("");
  const [offset, setOffset] = useState(0);
  const workers = useAdminWorkers(true);
  const jobs = useAdminJobs(true, state, queue, offset, LIMIT);
  // The fullest studios first; the rest are in Studios & credits.
  const storage = useAdminStorage(true, 0, 8);
  const stuck = jobs.data?.stuck;
  const nothingStuck = stuck && !stuck.expired_leases.length && !stuck.overdue.length && !stuck.orphan_steps.length;

  const columns: Column<AdminJob>[] = [
    { key: "queue", header: o.columns.queue, className: "whitespace-nowrap font-mono text-xs",
      cell: (job) => `${job.queue}${job.channel ? `:${job.channel}` : ""} · ${job.id.slice(0, 8)}` },
    { key: "state", header: o.columns.state, cell: (job) => job.state },
    { key: "attempts", header: o.columns.attempts, className: "text-right tabular-nums", cell: (job) => job.attempt_count },
    { key: "worker", header: o.columns.worker, className: "font-mono text-xs text-muted-foreground",
      cell: (job) => job.worker_id ?? "—" },
    { key: "updated", header: o.columns.updated, className: "whitespace-nowrap text-muted-foreground",
      cell: (job) => (job.updated_at ? formatDateTime(job.updated_at) : "—") },
    { key: "error", header: o.columns.error, className: "max-w-[260px]",
      cell: (job) => <span className="block truncate text-muted-foreground" title={job.last_error ?? ""}>{job.last_error ?? ""}</span> },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="scrollbar-thin relative grid max-h-[42%] shrink-0 gap-3 overflow-y-auto lg:grid-cols-3">
        <section className="panel space-y-2 p-3 lg:col-span-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">{o.workers}</h2>
            {workers.data && <p className="text-[11px] text-muted-foreground">{o.workersHint(workers.data.stale_after_seconds)}</p>}
          </div>
          {workers.isPending && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
          <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
            {workers.data?.workers.map((worker) => (
              <div key={worker.worker} className="flex items-center justify-between gap-2 rounded-lg bg-surface-2 px-2.5 py-1.5">
                <div className="min-w-0">
                  <p className="font-mono text-xs">{worker.worker}</p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {worker.last_seen_at ? o.lastSeen(formatRelative(worker.last_seen_at)) : "—"}
                    {worker.detail ? ` · ${worker.detail}` : ""}
                  </p>
                </div>
                <StatusBadge status={WORKER_TONE[worker.status]} label={o.workerStatus[worker.status]} />
              </div>
            ))}
          </div>
        </section>
        <section className="panel space-y-2 p-3">
          <h2 className="text-sm font-semibold">{o.stuck}</h2>
          {nothingStuck && <p className="text-xs text-muted-foreground">{o.nothingStuck}</p>}
          {stuck && (
            <>
              <StuckList title={o.expiredLeases} jobs={stuck.expired_leases} />
              <StuckList title={o.overdue} jobs={stuck.overdue} />
              {stuck.orphan_steps.length > 0 && (
                <div className="space-y-1">
                  <p className="text-xs font-medium">{o.orphanSteps}</p>
                  <ul className="space-y-0.5 font-mono text-xs text-muted-foreground">
                    {stuck.orphan_steps.map((step) => (
                      <li key={step.step_id}>
                        {step.node_type} · {step.status} · {step.step_id.slice(0, 8)}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
          <h2 className="pt-2 text-sm font-semibold">{o.storage}</h2>
          {storage.data?.disk && (
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">
                {o.disk(formatBytes(storage.data.disk.used_bytes), formatBytes(storage.data.disk.total_bytes),
                        formatBytes(storage.data.disk.free_bytes))}
              </p>
              <StorageBar
                className="h-1.5"
                info={{ used_bytes: storage.data.disk.used_bytes, quota_bytes: storage.data.disk.total_bytes,
                        percent: storage.data.disk.percent,
                        level: storage.data.disk.percent >= 90 ? "critical" : storage.data.disk.percent >= 80 ? "warning" : "ok" }}
              />
            </div>
          )}
          {storage.data && (
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              {(["notice", "warning", "critical", "full"] as const).map((level) => (
                <span key={level} className={cn("rounded-md bg-surface-2 px-1.5 py-0.5", STORAGE_TEXT[level])}>
                  {o.storageLevel[level]}: {storage.data.levels[level]}
                </span>
              ))}
            </div>
          )}
          <ul className="space-y-1 text-xs">
            {storage.data?.workspaces.map((item) => (
              <li key={item.workspace_id} className="space-y-0.5">
                <div className="flex justify-between gap-2">
                  <span className="min-w-0 truncate">{item.name}</span>
                  <span className={cn("shrink-0 tabular-nums", STORAGE_TEXT[item.level])}>
                    {formatBytes(item.used_bytes)} / {formatBytes(item.quota_bytes)} · {item.percent}%
                  </span>
                </div>
                <StorageBar info={item} className="h-1" />
              </li>
            ))}
          </ul>
          {storage.data?.retention && (
            <p className="text-[11px] text-muted-foreground">
              {t.storage.retention(storage.data.retention.intermediate_days, storage.data.retention.temp_days)}
            </p>
          )}
        </section>
      </div>
      <DataTable
        columns={columns}
        rows={jobs.data?.jobs ?? []}
        rowKey={(job) => job.id}
        loading={jobs.isFetching}
        error={jobs.isError ? errorText(jobs.error, t) : null}
        empty={o.noJobs}
        total={jobs.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffset={setOffset}
        toolbar={
          <>
            <h2 className="mr-auto text-sm font-semibold">{o.jobs}</h2>
            <FilterSelect value={queue} onChange={(v) => { setQueue(v); setOffset(0); }} all={o.allQueues}
                          options={QUEUES.map((q) => [q, q])} />
            <FilterSelect value={state} onChange={(v) => { setState(v); setOffset(0); }} all={o.allStates}
                          options={STATES.map((s) => [s, s])} />
            <Button variant="outline" size="sm" className="h-8" onClick={() => void jobs.refetch()}>
              <RefreshCw className="size-3.5" /> {t.admin.reconciliation.refresh}
            </Button>
          </>
        }
      />
    </div>
  );
}
