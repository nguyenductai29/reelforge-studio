"use client";

import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useReconciliation, useRefreshStudio } from "@/lib/queries";
import type { ReconciliationItem } from "@/lib/types";
import { DataTable, type Column } from "./data-table";
import { FieldLabel, StatusBadge } from "./primitives";

const PAGE_SIZE = 20;
type Decision = "confirm-charge" | "refund";

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 whitespace-pre-wrap break-words text-sm">{children || "—"}</dd>
    </div>
  );
}

/** Admin-only view of sanitized provider evidence and immutable credit decisions. */
export function Reconciliation() {
  const { t, formatDateTime, formatNumber } = useI18n();
  const r = t.admin.reconciliation;
  const client = useQueryClient();
  const refreshStudio = useRefreshStudio();
  const [status, setStatus] = useState<"pending" | "resolved">("pending");
  const [offset, setOffset] = useState(0);
  const query = useReconciliation(status, offset, PAGE_SIZE);
  const [selected, setSelected] = useState<ReconciliationItem | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const date = (value: string | null) => (value ? formatDateTime(value) : "—");
  const badge = (item: ReconciliationItem) => (
    <StatusBadge
      status={item.reconciliation_status === "pending" ? "needs_attention" : "completed"}
      label={r.status[item.reconciliation_status]}
    />
  );

  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: keys.admin }),
      client.invalidateQueries({ queryKey: keys.billing }),
      refreshStudio(),
    ]);
  }

  async function resolve() {
    if (!selected || !decision || busy) return;
    setBusy(true);
    try {
      await api<ReconciliationItem>(
        // One decision per paid job: each scene's image or clip is reconciled on its own.
        `admin/reconciliation/jobs/${encodeURIComponent(selected.job_id)}/${decision}`,
        jsonRequest("POST", { note: note.trim() || undefined }),
      );
      setSelected(null);
      setDecision(null);
      setNote("");
      await refresh();
      toast.success(r.saved);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // Another operator may have resolved this step while its dialog was open.
        setSelected(null);
        setDecision(null);
        setNote("");
        await refresh();
        toast.error(r.conflict);
      } else {
        toast.error(errorText(error, t));
      }
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<ReconciliationItem>[] = [
    { key: "studio", header: r.columns.studio, className: "max-w-[220px]",
      cell: (item) => (
        <div className="min-w-0">
          <p className="truncate font-medium" title={item.workspace_name}>{item.workspace_name}</p>
          <p className="truncate text-xs text-muted-foreground" title={item.user_email}>{item.user_email}</p>
        </div>
      ) },
    { key: "step", header: r.columns.step, className: "max-w-[240px]",
      cell: (item) => (
        <div className="min-w-0 text-xs">
          <p className="truncate">{item.workflow_name} · {item.node_type}
            {item.scene_index != null ? ` · ${r.fields.scene} ${item.scene_index}` : ""}</p>
          <p className="truncate text-muted-foreground">{item.provider} / {item.model}</p>
        </div>
      ) },
    { key: "error", header: r.columns.error, className: "max-w-[260px]",
      cell: (item) => (
        <span className="block truncate text-xs text-muted-foreground" title={item.error_message || item.error_category || undefined}>
          {item.error_message || item.error_category || "—"}
        </span>
      ) },
    { key: "credits", header: r.columns.credits, className: "whitespace-nowrap text-right tabular-nums",
      cell: (item) => t.common.credits(formatNumber(item.credits)) },
    { key: "date", header: r.columns.date, className: "whitespace-nowrap text-xs text-muted-foreground",
      cell: (item) => date(item.reconciled_at ?? item.created_at) },
    { key: "status", header: r.columns.status, className: "whitespace-nowrap", cell: (item) => badge(item) },
    { key: "actions", header: <span className="sr-only">{r.details}</span>, className: "text-right",
      cell: (item) => (
        <Button variant="outline" size="sm" className="h-7" onClick={() => setSelected(item)}>{r.details}</Button>
      ) },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={items}
        rowKey={(item) => item.job_id}
        loading={query.isFetching}
        error={query.isError ? errorText(query.error, t) : null}
        empty={status === "pending" ? r.emptyPending : r.emptyHistory}
        total={total}
        limit={PAGE_SIZE}
        offset={offset}
        onOffset={setOffset}
        minWidth={980}
        toolbar={
          <>
            <div className="inline-flex rounded-lg bg-muted p-1 text-sm">
              {(["pending", "resolved"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={status === value}
                  onClick={() => {
                    setStatus(value);
                    setOffset(0);
                  }}
                  className={
                    status === value
                      ? "rounded-md bg-background px-3 py-1 font-medium shadow"
                      : "rounded-md px-3 py-1 text-muted-foreground"
                  }
                >
                  {value === "pending" ? r.pending : r.history}
                </button>
              ))}
            </div>
            <Button variant="outline" size="sm" className="h-8" disabled={query.isFetching || busy} onClick={() => void query.refetch()}>
              {query.isFetching ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
              {r.refresh}
            </Button>
            <p className="min-w-0 flex-1 basis-64 truncate text-xs text-muted-foreground" title={r.description}>
              {r.description}
            </p>
          </>
        }
      />

      <Dialog open={Boolean(selected) && !decision} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{r.details}</DialogTitle>
            <DialogDescription>{selected?.workspace_name} · {selected?.user_email}</DialogDescription>
          </DialogHeader>
          {selected && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                {badge(selected)}
                <span className="text-sm font-medium">{t.common.credits(formatNumber(selected.credits))}</span>
              </div>
              <dl className="grid gap-4 sm:grid-cols-2">
                <Detail label={r.fields.workflow}>{selected.workflow_name}</Detail>
                <Detail label={r.fields.node}>{selected.node_type} · {selected.node_id}</Detail>
                {selected.scene_index != null && <Detail label={r.fields.scene}>{selected.scene_index}</Detail>}
                <Detail label={r.fields.workspaceId}>{selected.workspace_id}</Detail>
                <Detail label={r.fields.workflowId}>{selected.workflow_id}</Detail>
                <Detail label={r.fields.runId}>{selected.run_id}</Detail>
                <Detail label={r.fields.stepId}>{selected.step_id}</Detail>
                <Detail label={r.fields.jobId}>{selected.job_id}</Detail>
                <Detail label={r.fields.provider}>{selected.provider} / {selected.model}</Detail>
                <Detail label={r.fields.remoteRequestId}>{selected.remote_request_id}</Detail>
                <Detail label={r.fields.stage}>{selected.stage}</Detail>
                <Detail label={r.fields.createdAt}>{date(selected.created_at)}</Detail>
                <Detail label={r.fields.submittedAt}>{date(selected.submitted_at)}</Detail>
                <Detail label={r.fields.lastPolledAt}>{date(selected.last_polled_at)}</Detail>
                <Detail label={r.fields.providerStatus}>{selected.last_provider_status}</Detail>
                <Detail label={r.fields.submissionSucceeded}>{selected.submission_succeeded ? r.yes : r.no}</Detail>
                <Detail label={r.fields.asset}>{selected.has_asset ? selected.asset_ids.join(", ") || r.yes : r.no}</Detail>
                <Detail label={r.fields.errorCategory}>{selected.error_category}</Detail>
                <Detail label={r.fields.errorMessage}>{selected.error_message}</Detail>
                {selected.reconciliation_status !== "pending" && (
                  <>
                    <Detail label={r.fields.reconciledAt}>{date(selected.reconciled_at)}</Detail>
                    <Detail label={r.fields.reconciledBy}>{selected.reconciled_by_email ?? selected.reconciled_by}</Detail>
                    <div className="sm:col-span-2"><Detail label={r.fields.note}>{selected.note}</Detail></div>
                  </>
                )}
              </dl>
              {selected.reconciliation_status === "pending" && (
                <DialogFooter className="gap-2">
                  <Button variant="outline" onClick={() => { setNote(""); setDecision("refund"); }}>{r.refund}</Button>
                  <Button onClick={() => { setNote(""); setDecision("confirm-charge"); }}>{r.confirmCharge}</Button>
                </DialogFooter>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={Boolean(decision)} onOpenChange={(open) => !open && !busy && setDecision(null)}>
        <AlertDialogContent className="max-h-[85vh] overflow-y-auto">
          <AlertDialogHeader>
            <AlertDialogTitle>{decision === "refund" ? r.refundTitle : r.chargeTitle}</AlertDialogTitle>
            <AlertDialogDescription>{decision === "refund" ? r.refundDescription : r.chargeDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          {selected && (
            <p className="break-words rounded-lg bg-surface-2 p-3 text-sm">
              {selected.workspace_name} · {selected.user_email}<br />
              {selected.workflow_name} · {selected.node_id}<br />
              <span className="font-medium">{t.common.credits(formatNumber(selected.credits))}</span>
            </p>
          )}
          <div>
            <FieldLabel htmlFor="reconciliation-note">{r.note}</FieldLabel>
            <Textarea id="reconciliation-note" rows={3} maxLength={1000} value={note} disabled={busy} onChange={(event) => setNote(event.target.value)} />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction disabled={busy} onClick={(event) => { event.preventDefault(); void resolve(); }}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {decision === "refund" ? r.refund : r.confirmCharge}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
