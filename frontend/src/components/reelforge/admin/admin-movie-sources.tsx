"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { useAdminMovieSources } from "@/lib/queries";
import { formatBytes, formatClock } from "@/lib/studio";
import type { MovieSource, MovieSourceStatus } from "@/lib/types";
import { DataTable, useDebounced, type Column } from "../data-table";
import { StatusBadge } from "../primitives";
import { FilterSelect } from "./shared";

const LIMIT = 20;
const STATUSES: MovieSourceStatus[] = ["importing", "ready", "processing", "completed", "failed", "delete_scheduled", "deleted"];

/** Every studio's movie sources with their Drive IDs and retry state (Admin → System settings → Movie sources). */
export function AdminMovieSources() {
  const { t, formatDateTime } = useI18n();
  const m = t.movieSources;
  const v = t.admin.system.movie;
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const term = useDebounced(q.trim());
  const page = useAdminMovieSources({ q: term, status, limit: LIMIT, offset });

  const columns: Column<MovieSource>[] = [
    { key: "name", header: m.columns.name, className: "max-w-[240px]",
      cell: (row) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={row.name}>{row.name}</span>
          <span className="block truncate text-xs text-muted-foreground">{row.workspace_name ?? row.workspace_id}</span>
        </span>
      ) },
    { key: "status", header: m.columns.status, className: "whitespace-nowrap",
      cell: (row) => (
        <span className="block">
          <StatusBadge status={row.status} label={m.statuses[row.status]} />
          {row.failure && (
            <span className="mt-1 block max-w-[220px] truncate text-[11px] text-destructive" title={row.failure.message ?? ""}>
              {row.failure.code}{row.attempt_count ? ` · ${v.attempts(row.attempt_count)}` : ""}
            </span>
          )}
        </span>
      ) },
    { key: "size", header: m.columns.size, className: "whitespace-nowrap text-right tabular-nums",
      cell: (row) => (row.bytes ? formatBytes(row.bytes) : "—") },
    { key: "duration", header: m.columns.duration, className: "whitespace-nowrap text-right tabular-nums",
      cell: (row) => (row.duration_seconds ? formatClock(row.duration_seconds) : "—") },
    { key: "drive", header: v.driveFile, className: "max-w-[160px]",
      cell: (row) => <span className="block truncate font-mono text-xs" title={row.drive_file_id ?? ""}>{row.drive_file_id ?? "—"}</span> },
    { key: "next", header: v.nextAttempt, className: "whitespace-nowrap text-xs text-muted-foreground",
      cell: (row) => (row.next_attempt_at ? formatDateTime(row.next_attempt_at) : "—") },
    { key: "expires", header: m.columns.expires, className: "whitespace-nowrap text-xs text-muted-foreground",
      cell: (row) => (row.deletion_due_at ? formatDateTime(row.deletion_due_at) : "—") },
  ];

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{v.allSources}</h3>
      <DataTable
        columns={columns}
        rows={page.data?.items ?? []}
        rowKey={(row) => row.id}
        loading={page.isFetching}
        error={page.isError ? errorText(page.error, t) : null}
        empty={m.empty}
        minWidth={860}
        total={page.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffset={setOffset}
        className="h-[28rem]"
        toolbar={
          <>
            <div className="relative min-w-[180px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} type="search" onChange={(e) => { setQ(e.target.value); setOffset(0); }}
                     placeholder={m.searchPlaceholder} aria-label={m.searchPlaceholder} className="h-8 bg-surface pl-8" />
            </div>
            <FilterSelect value={status} onChange={(value) => { setStatus(value); setOffset(0); }} all={m.allStatuses}
                          options={STATUSES.map((value) => [value, m.statuses[value]] as [string, string])} />
          </>
        }
      />
    </section>
  );
}
