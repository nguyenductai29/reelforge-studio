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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useReconciliation, useRefreshStudio } from "@/lib/queries";
import type { ReconciliationItem } from "@/lib/types";
import { FieldLabel, StatusBadge } from "./primitives";

const PAGE_SIZE = 50;
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

  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted-foreground">{r.description}</p>
      <Tabs
        value={status}
        onValueChange={(value) => {
          setStatus(value === "resolved" ? "resolved" : "pending");
          setOffset(0);
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="pending">{r.pending}</TabsTrigger>
            <TabsTrigger value="resolved">{r.history}</TabsTrigger>
          </TabsList>
          <Button variant="outline" size="sm" disabled={query.isFetching || busy} onClick={() => void query.refetch()}>
            {query.isFetching ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
            {r.refresh}
          </Button>
        </div>

        <TabsContent value={status} className="mt-4 space-y-4">
      {query.isPending ? (
        <p role="status" className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> {t.common.loading}
        </p>
      ) : query.isError ? (
        <p role="alert" className="panel p-4 text-sm text-destructive">{errorText(query.error, t)}</p>
      ) : items.length === 0 ? (
        <p className="panel p-6 text-sm text-muted-foreground">{status === "pending" ? r.emptyPending : r.emptyHistory}</p>
      ) : (
        <div className="panel divide-y divide-border">
          {items.map((item) => (
            <div key={item.job_id} className="flex flex-wrap items-center gap-3 p-4 text-sm">
              <div className="min-w-0 flex-1 basis-60">
                <p className="truncate font-medium">{item.workspace_name} · {item.user_email}</p>
                <p className="mt-1 truncate text-xs text-muted-foreground">
                  {item.workflow_name} · {item.node_type}
                  {item.scene_index != null ? ` · ${r.fields.scene} ${item.scene_index}` : ""} · {item.provider} / {item.model}
                </p>
                <p className="mt-1 truncate text-xs text-muted-foreground" title={item.error_message || item.error_category || undefined}>
                  {r.fields.errorMessage}: {item.error_message || item.error_category || "—"}
                </p>
                <p className="mt-1 truncate text-xs text-muted-foreground" title={item.remote_request_id ?? undefined}>
                  {r.fields.remoteRequestId}: {item.remote_request_id ?? "—"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{date(item.reconciled_at ?? item.created_at)}</p>
              </div>
              <span className="text-xs font-medium">{t.common.credits(formatNumber(item.credits))}</span>
              {badge(item)}
              <Button variant="outline" size="sm" onClick={() => setSelected(item)}>{r.details}</Button>
            </div>
          ))}
        </div>
      )}

      {query.data && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
          <p>{r.range(formatNumber(items.length ? offset + 1 : 0), formatNumber(items.length ? offset + items.length : 0), formatNumber(total))}</p>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={offset === 0 || query.isFetching} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              {r.previous}
            </Button>
            <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= total || query.isFetching} onClick={() => setOffset(offset + PAGE_SIZE)}>
              {r.next}
            </Button>
          </div>
        </div>
      )}

        </TabsContent>
      </Tabs>

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
    </div>
  );
}
