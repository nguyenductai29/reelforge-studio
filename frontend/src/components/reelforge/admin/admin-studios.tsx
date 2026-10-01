"use client";

import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, MoreHorizontal, Search } from "lucide-react";
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
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useAdminWorkspaces } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { AdminWorkspace, AdminWorkspaceDetail, Plan } from "@/lib/types";
import { DataTable, useDebounced, type Column } from "../data-table";
import { FieldLabel, StatusBadge } from "../primitives";
import { ConfirmDialog, Detail, FilterSelect, type Confirm } from "./shared";

const LIMIT = 20;
type Dialogs = { kind: "view" | "plan" | "credits"; workspace: AdminWorkspace } | null;

function useRefresh() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys.adminWorkspaces }),
      client.invalidateQueries({ queryKey: keys.adminUsers }),
      client.invalidateQueries({ queryKey: keys.admin }),
    ]);
}

function DetailDialog({ workspace, onClose }: { workspace: AdminWorkspace | null; onClose: () => void }) {
  const { t, formatDateTime, formatMoney, formatNumber } = useI18n();
  const s = t.admin.studios;
  const detail = useQuery({
    queryKey: [...keys.adminWorkspaces, "detail", workspace?.id],
    queryFn: () => api<AdminWorkspaceDetail>(`admin/workspaces/${encodeURIComponent(workspace!.id)}`),
    enabled: Boolean(workspace),
  });
  const data = detail.data;
  return (
    <Dialog open={Boolean(workspace)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{workspace?.name}</DialogTitle>
          <DialogDescription>{workspace?.owner_email}</DialogDescription>
        </DialogHeader>
        {!data ? (
          <Loader2 className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
        ) : (
          <div className="space-y-4 text-sm">
            <dl className="grid gap-3 sm:grid-cols-3">
              <Detail label={s.columns.plan}>{(data.plan_code ?? "—").toUpperCase()}</Detail>
              <Detail label={s.columns.credits}>{formatNumber(data.credits)}</Detail>
              <Detail label={s.columns.expires}>{data.ends_at ? formatDateTime(data.ends_at) : s.noEnd}</Detail>
              <Detail label={s.projects}>{formatNumber(data.counts.projects)}</Detail>
              <Detail label={s.workflows}>{formatNumber(data.counts.workflows)}</Detail>
              <Detail label={s.storage}>{formatBytes(data.storage_bytes)}</Detail>
            </dl>
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">{s.members}</p>
              <p>{data.members.map((m) => `${m.email} (${m.role})`).join(", ") || "—"}</p>
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">{s.recentLedger}</p>
              <ul className="divide-y divide-border rounded-lg border border-border">
                {data.ledger.length === 0 && <li className="px-3 py-2 text-muted-foreground">—</li>}
                {data.ledger.map((entry) => (
                  <li key={entry.id} className="flex justify-between gap-3 px-3 py-1.5">
                    <span className="min-w-0 truncate">{entry.reason}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {entry.delta > 0 ? "+" : ""}
                      {formatNumber(entry.delta)} · {formatDateTime(entry.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">{s.recentOrders}</p>
              <ul className="divide-y divide-border rounded-lg border border-border">
                {data.orders.length === 0 && <li className="px-3 py-2 text-muted-foreground">—</li>}
                {data.orders.map((order) => (
                  <li key={order.id} className="flex justify-between gap-3 px-3 py-1.5">
                    <span>
                      {order.plan_code.toUpperCase()} · {formatMoney(order.amount_vnd)}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {order.status} · {formatDateTime(order.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function PlanDialog({ workspace, plans, onClose }: { workspace: AdminWorkspace | null; plans: Plan[]; onClose: () => void }) {
  const { t } = useI18n();
  const s = t.admin.studios;
  const refresh = useRefresh();
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState<string | null>(null);
  const current = plan ?? workspace?.plan_code ?? plans[0]?.code ?? "trial";

  async function save() {
    if (!workspace) return;
    setBusy(true);
    try {
      await api(`admin/workspaces/${workspace.id}/subscription`, jsonRequest("PUT", {
        plan_code: current, status: workspace.status === "paused" ? "paused" : "active",
      }));
      await refresh();
      toast.success(t.admin.saved);
      setPlan(null);
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={Boolean(workspace)}
      onOpenChange={(open) => {
        if (open || busy) return;
        setPlan(null);
        onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{s.changePlanTitle}</DialogTitle>
          <DialogDescription>
            {workspace ? s.changePlanDescription(workspace.name, (workspace.plan_code ?? "—").toUpperCase(), current.toUpperCase()) : ""}
          </DialogDescription>
        </DialogHeader>
        <div>
          <FieldLabel>{s.columns.plan}</FieldLabel>
          <Select value={current} onValueChange={setPlan}>
            <SelectTrigger className="bg-surface" aria-label={s.columns.plan}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {plans.filter((p) => p.is_active || p.code === workspace?.plan_code).map((p) => (
                <SelectItem key={p.code} value={p.code}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t.common.cancel}
          </Button>
          <Button onClick={() => void save()} disabled={busy || current === workspace?.plan_code}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {t.common.saveChanges}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CreditsDialog({ workspace, onClose }: { workspace: AdminWorkspace | null; onClose: () => void }) {
  const { t, formatNumber } = useI18n();
  const s = t.admin.studios;
  const refresh = useRefresh();
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspace) return;
    const values = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await api(`admin/workspaces/${workspace.id}/credits`, jsonRequest("POST", {
        delta: Number(values.get("delta")), reason: values.get("reason"),
      }));
      await refresh();
      toast.success(t.admin.saved);
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={Boolean(workspace)} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{s.creditTitle}</DialogTitle>
          <DialogDescription>{s.creditHint}</DialogDescription>
        </DialogHeader>
        {workspace && (
          <form id="adjust-credits" onSubmit={submit} className="space-y-4">
            <dl className="grid gap-3 sm:grid-cols-3">
              <Detail label={s.columns.studio}>{workspace.name}</Detail>
              <Detail label={s.columns.owner}>{workspace.owner_email}</Detail>
              <Detail label={s.current}>{formatNumber(workspace.credits)}</Detail>
            </dl>
            <div>
              <FieldLabel htmlFor="credit-delta">{s.delta}</FieldLabel>
              <Input id="credit-delta" name="delta" type="number" min={-1000000} max={1000000} required className="bg-surface" />
            </div>
            <div>
              <FieldLabel htmlFor="credit-reason">{s.reason}</FieldLabel>
              <Input id="credit-reason" name="reason" minLength={3} maxLength={80} required className="bg-surface" />
            </div>
          </form>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t.common.cancel}
          </Button>
          <Button type="submit" form="adjust-credits" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {s.adjust}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Studios with their owner, plan and credits; plan changes, credits and pausing use dialogs. */
export function AdminStudios({ plans }: { plans: Plan[] }) {
  const { t, formatDate, formatNumber } = useI18n();
  const s = t.admin.studios;
  const refresh = useRefresh();
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [dialog, setDialog] = useState<Dialogs>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const search = useDebounced(q.trim());
  const studios = useAdminWorkspaces({ q: search, plan, status, limit: LIMIT, offset });
  const statusLabel = (value: string) =>
    t.status.subscription[value as keyof Dictionary["status"]["subscription"]] ?? value;
  const filtered = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };

  const columns: Column<AdminWorkspace>[] = [
    { key: "studio", header: s.columns.studio, className: "max-w-[220px]",
      cell: (ws) => <span className="block truncate font-medium" title={ws.name}>{ws.name}</span> },
    { key: "owner", header: s.columns.owner, className: "max-w-[220px]",
      cell: (ws) => <span className="block truncate text-muted-foreground" title={ws.owner_email}>{ws.owner_email}</span> },
    { key: "plan", header: s.columns.plan, cell: (ws) => (ws.plan_code ?? "—").toUpperCase() },
    { key: "credits", header: s.columns.credits, className: "text-right tabular-nums", cell: (ws) => formatNumber(ws.credits) },
    { key: "expires", header: s.columns.expires, className: "whitespace-nowrap text-muted-foreground",
      cell: (ws) => (ws.ends_at ? formatDate(ws.ends_at) : s.noEnd) },
    { key: "status", header: s.columns.status, className: "whitespace-nowrap", cell: (ws) => <StatusBadge status={ws.status} label={statusLabel(ws.status)} /> },
    { key: "actions", header: <span className="sr-only">{s.columns.actions}</span>, className: "w-10 text-right",
      cell: (ws) => (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7" aria-label={`${s.columns.actions} · ${ws.name}`}>
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setDialog({ kind: "view", workspace: ws })}>{s.viewDetails}</DropdownMenuItem>
            <DropdownMenuItem onClick={() => setDialog({ kind: "plan", workspace: ws })}>{s.changePlan}</DropdownMenuItem>
            <DropdownMenuItem onClick={() => setDialog({ kind: "credits", workspace: ws })}>{s.adjustCredits}</DropdownMenuItem>
            <DropdownMenuItem
              onClick={() =>
                setConfirm({
                  title: ws.status === "paused" ? s.activateTitle : s.pauseTitle,
                  description: ws.status === "paused" ? s.activateDescription(ws.name) : s.pauseDescription(ws.name),
                  run: () => api(`admin/workspaces/${ws.id}/subscription`, jsonRequest("PUT", {
                    plan_code: ws.plan_code, status: ws.status === "paused" ? "active" : "paused",
                  })),
                  done: refresh,
                })
              }
            >
              {ws.status === "paused" ? s.activate : s.pause}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={studios.data?.items ?? []}
        rowKey={(ws) => ws.id}
        loading={studios.isFetching}
        error={studios.isError ? errorText(studios.error, t) : null}
        empty={s.empty}
        total={studios.data?.total ?? 0}
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
                placeholder={s.searchPlaceholder}
                aria-label={s.searchPlaceholder}
                className="h-8 bg-surface pl-8"
              />
            </div>
            <FilterSelect value={plan} onChange={filtered(setPlan)} all={s.allPlans}
                          options={plans.map((p) => [p.code, p.name] as [string, string])} />
            <FilterSelect value={status} onChange={filtered(setStatus)} all={s.allStatuses}
                          options={(["active", "expired", "paused", "canceled"] as const).map((v) => [v, statusLabel(v)])} />
          </>
        }
      />
      <DetailDialog workspace={dialog?.kind === "view" ? dialog.workspace : null} onClose={() => setDialog(null)} />
      <PlanDialog workspace={dialog?.kind === "plan" ? dialog.workspace : null} plans={plans} onClose={() => setDialog(null)} />
      <CreditsDialog workspace={dialog?.kind === "credits" ? dialog.workspace : null} onClose={() => setDialog(null)} />
      <ConfirmDialog confirm={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}
