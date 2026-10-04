"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { useAudit } from "@/lib/queries";
import type { AuditEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DataTable, useDebounced, type Column } from "../data-table";
import { FilterSelect } from "./shared";

const LIMIT = 50;
const OUTCOMES = ["success", "failure", "denied"] as const;

function details(event: AuditEvent): string {
  const parts = Object.entries(event.details).map(([key, value]) =>
    `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`);
  if (event.target_type && event.target_id) parts.unshift(`${event.target_type} ${event.target_id.slice(0, 12)}`);
  return parts.join(" · ");
}

/** Admin → Audit log (Phase 24): security and administration events, filtered and paginated on the server. */
export function AdminAudit() {
  const { t, formatDateTime } = useI18n();
  const a = t.auditLog;
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState("");
  const [outcome, setOutcome] = useState("");
  const [actor, setActor] = useState("");
  const debounced = useDebounced(actor);
  const audit = useAudit({ action: action || undefined, outcome: outcome || undefined,
                           actor: debounced.trim() || undefined, limit: LIMIT, offset });
  const filtered = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };
  const actions = audit.data?.actions ?? [];
  const groups = Object.entries(a.groups);

  const columns: Column<AuditEvent>[] = [
    { key: "time", header: a.columns.time, className: "whitespace-nowrap text-muted-foreground", cell: (event) => formatDateTime(event.at) },
    { key: "action", header: a.columns.action, className: "max-w-[240px] 2xl:max-w-[320px]",
      cell: (event) => <span className="block truncate font-medium">{t.auditActions[event.action] ?? event.action}</span> },
    { key: "outcome", header: a.columns.outcome, className: "whitespace-nowrap",
      cell: (event) => (
        <span className={cn(event.outcome === "success" ? "text-success" : event.outcome === "denied" ? "text-warning" : "text-destructive")}>
          {a.outcomes[event.outcome]}
        </span>
      ) },
    { key: "actor", header: a.columns.actor, className: "max-w-[220px] 2xl:max-w-[320px]",
      cell: (event) => (
        <span className="block truncate">
          {event.actor?.email ?? a.system}
          {event.workspace?.name && <span className="block truncate text-xs text-muted-foreground">{event.workspace.name}</span>}
        </span>
      ) },
    { key: "target", header: a.columns.target, className: "max-w-[340px] 2xl:max-w-[520px]",
      cell: (event) => <span className="block truncate text-xs text-muted-foreground" title={details(event)}>{details(event) || "—"}</span> },
    { key: "ip", header: a.columns.ip, className: "whitespace-nowrap font-mono text-xs text-muted-foreground",
      cell: (event) => event.ip ?? "—" },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <p className="text-xs text-muted-foreground">{a.hint}</p>
      <DataTable
        columns={columns}
        rows={audit.data?.items ?? []}
        rowKey={(event) => String(event.id)}
        loading={audit.isFetching}
        error={audit.isError ? errorText(audit.error, t) : null}
        empty={a.empty}
        minWidth={1050}
        total={audit.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffset={setOffset}
        toolbar={
          <>
            <div className="relative min-w-[200px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={actor} onChange={(e) => { setActor(e.target.value); setOffset(0); }}
                     placeholder={a.search} aria-label={a.search} className="h-8 bg-surface pl-8" />
            </div>
            <FilterSelect value={action} onChange={filtered(setAction)} all={a.allActions}
                          options={[...groups.map(([prefix, label]) => [prefix, label] as [string, string]),
                                    ...actions.map((name) => [name, t.auditActions[name] ?? name] as [string, string])]} />
            <FilterSelect value={outcome} onChange={filtered(setOutcome)} all={a.allOutcomes}
                          options={OUTCOMES.map((value) => [value, a.outcomes[value]])} />
          </>
        }
      />
    </div>
  );
}
