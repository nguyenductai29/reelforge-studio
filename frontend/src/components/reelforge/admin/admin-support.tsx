"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useAdminSupport, useAdminSupportTicket } from "@/lib/queries";
import type { SupportPriority, SupportStatus, SupportTicket, SupportTicketDetail } from "@/lib/types";
import { DataTable, useDebounced, type Column } from "../data-table";
import { FieldLabel } from "../primitives";
import { SUPPORT_CATEGORIES, SUPPORT_STATUSES, SupportReply, SupportStatusBadge, SupportThread } from "../support";
import { Detail, FilterSelect } from "./shared";

const LIMIT = 20;

function TicketDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { t, formatDateTime } = useI18n();
  const s = t.support;
  const client = useQueryClient();
  const showError = useErrorToast();
  const ticket = useAdminSupportTicket(id);
  const [resolve, setResolve] = useState(false);

  async function save(path: string, method: "POST" | "PATCH", body: unknown) {
    try {
      const updated = await api<SupportTicketDetail>(`admin/support/${encodeURIComponent(id!)}${path}`,
                                                     jsonRequest(method, body));
      client.setQueryData([...keys.adminSupport, "ticket", id], updated);
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.adminSupport }),
        client.invalidateQueries({ queryKey: keys.admin }),
      ]);
      toast.success(t.admin.saved);
      return true;
    } catch (error) {
      showError(error);
      return false;
    }
  }

  const data = ticket.data;
  return (
    <Dialog open={Boolean(id)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="break-words">{data?.subject ?? s.title}</DialogTitle>
          <DialogDescription>{data ? `#${data.id.slice(0, 8)} · ${data.created_by_email} · ${data.workspace_name}` : ""}</DialogDescription>
        </DialogHeader>
        {!data ? (
          <Loader2 className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
        ) : (
          <div className="space-y-4 text-sm">
            <dl className="grid gap-3 sm:grid-cols-3">
              <div>
                <FieldLabel>{t.admin.support.columns.status}</FieldLabel>
                <Select value={data.status} onValueChange={(value) => void save("", "PATCH", { status: value })}>
                  <SelectTrigger className="h-8 bg-surface" aria-label={t.admin.support.columns.status}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SUPPORT_STATUSES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {s.statuses[value]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <FieldLabel>{t.admin.support.columns.priority}</FieldLabel>
                <Select value={data.priority} onValueChange={(value) => void save("", "PATCH", { priority: value })}>
                  <SelectTrigger className="h-8 bg-surface" aria-label={t.admin.support.columns.priority}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(["normal", "high"] as SupportPriority[]).map((value) => (
                      <SelectItem key={value} value={value}>
                        {t.admin.support.priorities[value]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Detail label={t.admin.support.columns.category}>{s.categories[data.category]}</Detail>
            </dl>
            {Object.keys(data.context).length > 0 && (
              <p className="text-xs text-muted-foreground">
                {s.attached}:{" "}
                {(Object.entries(data.context) as [keyof typeof s.contextLabels, string][])
                  .map(([key, value]) => `${s.contextLabels[key]} ${value}`).join(" · ")}
              </p>
            )}
            <SupportThread messages={data.thread} viewer="admin" />
            <SupportReply
              ticket={data}
              onSend={(body) => save("/messages", "POST", resolve ? { body, status: "resolved" } : { body })}
              extra={
                <label className="mr-auto flex items-center gap-2 text-xs text-muted-foreground">
                  <input type="checkbox" checked={resolve} onChange={(e) => setResolve(e.target.checked)}
                         className="size-3.5 accent-[var(--primary)]" />
                  {t.admin.support.markResolved}
                </label>
              }
            />
            {data.closed_at && <p className="text-xs text-muted-foreground">{s.closedAt(formatDateTime(data.closed_at))}</p>}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Every studio's support requests, one page at a time from the server. */
/** ``initialPriority`` starts filtered (Admin → Overview links to high-priority tickets). */
export function AdminSupport({ initialTicket, initialPriority }: { initialTicket?: string | null; initialPriority?: string | null }) {
  const { t, formatRelative } = useI18n();
  const a = t.admin.support;
  const s = t.support;
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [priority, setPriority] = useState(initialPriority === "high" || initialPriority === "normal" ? initialPriority : "");
  const [offset, setOffset] = useState(0);
  const [viewing, setViewing] = useState<string | null>(initialTicket ?? null);
  const search = useDebounced(q.trim());
  const tickets = useAdminSupport({ q: search, status, category, priority, limit: LIMIT, offset });
  useEffect(() => {
    if (initialTicket) setViewing(initialTicket);
  }, [initialTicket]);
  const filtered = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };

  const columns: Column<SupportTicket>[] = [
    { key: "ticket", header: a.columns.ticket, className: "max-w-[280px]",
      cell: (ticket) => (
        <button type="button" className="block max-w-full text-left hover:text-primary" onClick={() => setViewing(ticket.id)}>
          <span className="block truncate font-medium">{ticket.subject}</span>
          <span className="block font-mono text-[11px] text-muted-foreground">#{ticket.id.slice(0, 8)}</span>
        </button>
      ) },
    { key: "user", header: a.columns.user, className: "max-w-[200px]",
      cell: (ticket) => <span className="block truncate">{ticket.created_by_email}</span> },
    { key: "studio", header: a.columns.studio, className: "max-w-[160px]",
      cell: (ticket) => <span className="block truncate text-muted-foreground">{ticket.workspace_name}</span> },
    { key: "category", header: a.columns.category, className: "whitespace-nowrap", cell: (ticket) => s.categories[ticket.category] },
    { key: "status", header: a.columns.status, className: "whitespace-nowrap",
      cell: (ticket) => <SupportStatusBadge status={ticket.status as SupportStatus} /> },
    { key: "updated", header: a.columns.updated, className: "whitespace-nowrap text-muted-foreground",
      cell: (ticket) => (ticket.updated_at ? formatRelative(ticket.updated_at) : "—") },
    { key: "priority", header: a.columns.priority, className: "whitespace-nowrap",
      cell: (ticket) => (
        <span className={ticket.priority === "high" ? "font-medium text-warning" : "text-muted-foreground"}>
          {a.priorities[ticket.priority]}
        </span>
      ) },
    { key: "actions", header: <span className="sr-only">{a.columns.actions}</span>, className: "w-16 text-right",
      cell: (ticket) => (
        <Button variant="ghost" size="sm" className="h-7" onClick={() => setViewing(ticket.id)}>
          {a.open}
        </Button>
      ) },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={tickets.data?.items ?? []}
        rowKey={(ticket) => ticket.id}
        loading={tickets.isFetching}
        error={tickets.isError ? errorText(tickets.error, t) : null}
        empty={a.empty}
        minWidth={1000}
        total={tickets.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffset={setOffset}
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
                placeholder={a.searchPlaceholder}
                aria-label={a.searchPlaceholder}
                className="h-8 bg-surface pl-8"
              />
            </div>
            <FilterSelect value={status} onChange={filtered(setStatus)} all={a.allStatuses}
                          options={SUPPORT_STATUSES.map((v) => [v, s.statuses[v]])} />
            <FilterSelect value={category} onChange={filtered(setCategory)} all={a.allCategories}
                          options={SUPPORT_CATEGORIES.map((v) => [v, s.categories[v]])} />
            <FilterSelect value={priority} onChange={filtered(setPriority)} all={a.allPriorities}
                          options={(["normal", "high"] as const).map((v) => [v, a.priorities[v]])} />
          </>
        }
      />
      <TicketDialog id={viewing} onClose={() => setViewing(null)} />
    </>
  );
}
