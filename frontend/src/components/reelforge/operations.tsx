"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useI18n } from "@/lib/i18n";
import { useAdminJobs, useAdminStorage, useAdminWorkers } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { StuckJob, WorkerHealth } from "@/lib/types";
import { StatusBadge } from "./primitives";

const ALL = "__all__";
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
      <ul className="space-y-1 text-xs text-muted-foreground">
        {jobs.map((job) => (
          <li key={job.id} className="flex flex-wrap gap-x-3 font-mono">
            <span>{job.queue}</span>
            <span>{job.id.slice(0, 8)}</span>
            <span>×{job.attempt_count}</span>
            {job.worker_id && <span>{job.worker_id}</span>}
            {(job.lease_expires_at ?? job.available_at) && (
              <span>{formatRelative((job.lease_expires_at ?? job.available_at)!)}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * System operations for administrators: worker heartbeats, recent jobs (identifiers and timing only,
 * never payloads), a stuck-work audit and storage per studio.
 */
export function Operations() {
  const { t, formatRelative, formatDateTime } = useI18n();
  const o = t.admin.operations;
  const [state, setState] = useState("");
  const [queue, setQueue] = useState("");
  const workers = useAdminWorkers(true);
  const jobs = useAdminJobs(true, state, queue);
  const storage = useAdminStorage(true);
  const stuck = jobs.data?.stuck;
  const nothingStuck = stuck && !stuck.expired_leases.length && !stuck.overdue.length && !stuck.orphan_steps.length;

  return (
    <div className="space-y-6">
      <section className="panel space-y-3 p-5">
        <div>
          <h2 className="text-sm font-semibold">{o.workers}</h2>
          {workers.data && (
            <p className="text-xs text-muted-foreground">{o.workersHint(workers.data.stale_after_seconds)}</p>
          )}
        </div>
        {workers.isPending && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {workers.data?.workers.map((worker) => (
            <div key={worker.worker} className="flex items-start justify-between gap-2 rounded-lg bg-surface-2 p-3">
              <div className="min-w-0">
                <p className="font-mono text-xs">{worker.worker}</p>
                <p className="truncate text-[11px] text-muted-foreground">
                  {worker.last_seen_at ? o.lastSeen(formatRelative(worker.last_seen_at)) : "—"}
                  {worker.host ? ` · ${worker.host}` : ""}
                  {worker.detail ? ` · ${worker.detail}` : ""}
                </p>
              </div>
              <StatusBadge status={WORKER_TONE[worker.status]} label={o.workerStatus[worker.status]} />
            </div>
          ))}
        </div>
      </section>

      <section className="panel space-y-3 p-5">
        <h2 className="text-sm font-semibold">{o.stuck}</h2>
        {nothingStuck && <p className="text-xs text-muted-foreground">{o.nothingStuck}</p>}
        {stuck && (
          <>
            <StuckList title={o.expiredLeases} jobs={stuck.expired_leases} />
            <StuckList title={o.overdue} jobs={stuck.overdue} />
            {stuck.orphan_steps.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs font-medium">{o.orphanSteps}</p>
                <ul className="space-y-1 font-mono text-xs text-muted-foreground">
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
      </section>

      <section className="panel space-y-3 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">{o.jobs}</h2>
          <div className="flex flex-wrap gap-2">
            <Select value={queue || ALL} onValueChange={(value) => setQueue(value === ALL ? "" : value)}>
              <SelectTrigger className="h-8 w-36 bg-surface" aria-label={o.allQueues}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{o.allQueues}</SelectItem>
                {QUEUES.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                    {jobs.data?.counts[item] ? ` (${Object.values(jobs.data.counts[item]!).reduce((a, b) => a + (b ?? 0), 0)})` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={state || ALL} onValueChange={(value) => setState(value === ALL ? "" : value)}>
              <SelectTrigger className="h-8 w-36 bg-surface" aria-label={o.allStates}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{o.allStates}</SelectItem>
                {STATES.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" onClick={() => void jobs.refetch()}>
              {t.admin.reconciliation.refresh}
            </Button>
          </div>
        </div>
        {jobs.isPending && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1.5 pr-3 font-medium">{o.columns.queue}</th>
                <th className="py-1.5 pr-3 font-medium">{o.columns.state}</th>
                <th className="py-1.5 pr-3 font-medium">{o.columns.attempts}</th>
                <th className="py-1.5 pr-3 font-medium">{o.columns.updated}</th>
                <th className="py-1.5 font-medium">{o.columns.error}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {jobs.data?.jobs.map((job) => (
                <tr key={job.id}>
                  <td className="py-1.5 pr-3 font-mono">
                    {job.queue}
                    {job.channel ? `:${job.channel}` : ""} · {job.id.slice(0, 8)}
                  </td>
                  <td className="py-1.5 pr-3">{job.state}</td>
                  <td className="py-1.5 pr-3">{job.attempt_count}</td>
                  <td className="py-1.5 pr-3">{job.updated_at ? formatDateTime(job.updated_at) : "—"}</td>
                  <td className="max-w-[240px] truncate py-1.5 text-muted-foreground" title={job.last_error ?? ""}>
                    {job.last_error ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel space-y-3 p-5">
        <h2 className="text-sm font-semibold">{o.storage}</h2>
        {storage.isPending && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
        <ul className="divide-y divide-border text-sm">
          {storage.data?.workspaces.map((item) => (
            <li key={item.workspace_id} className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0 truncate">{item.name}</span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {formatBytes(item.bytes)} / {formatBytes(storage.data!.quota_bytes)} · {o.files(item.files)}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
