"use client";

import { Fragment, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Radio, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useSystemReadiness, useVerification } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { SystemCheck, SystemCheckStatus, VerificationItem, VerificationStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const DOT: Record<SystemCheckStatus, string> = {
  ok: "bg-success",
  warning: "bg-warning",
  error: "bg-destructive",
  missing: "bg-destructive",
  off: "bg-muted-foreground/40",
};

const TONE: Record<VerificationStatus, string> = {
  passed: "text-success",
  failed: "text-destructive",
  not_applicable: "text-muted-foreground",
  not_checked: "text-warning",
};

/** Values a check may carry, shown in plain words; paths and versions are fine, secrets never reach here. */
function checkValue(check: SystemCheck, formatDateTime: (value: string) => string): string {
  const parts: string[] = [];
  if (typeof check.dialect === "string") parts.push(check.dialect);
  if (typeof check.current === "string") parts.push(`${check.current}${check.head !== check.current ? ` → ${check.head}` : ""}`);
  if (typeof check.path === "string") parts.push(check.path);
  if (typeof check.free_bytes === "number" && typeof check.total_bytes === "number") {
    parts.push(`${formatBytes(check.free_bytes)} / ${formatBytes(check.total_bytes)}`);
  }
  if (typeof check.last_run === "string") parts.push(formatDateTime(check.last_run));
  if (typeof check.last_seen_at === "string") parts.push(formatDateTime(check.last_seen_at));
  if (typeof check.source === "string") parts.push(check.source);
  if (typeof check.key_source === "string") parts.push(check.key_source);
  if (typeof check.vietqr_mode === "string") parts.push(check.vietqr_mode);
  if (typeof check.provider === "string") parts.push(check.provider);
  if (typeof check.redirect === "string") parts.push(check.redirect);
  if (typeof check.count === "number" && check.count) parts.push(`${check.count}: ${((check.names as string[]) ?? []).join(", ")}`);
  if (typeof check.cache_seconds === "number") parts.push(`${check.cache_seconds}s`);
  if (typeof check.mode === "string") parts.push(check.mode);
  if (Array.isArray(check.variables)) parts.push((check.variables as string[]).join(", "));
  if (typeof check.enabled_models === "number" && check.enabled_models) parts.push(`${check.enabled_models} model`);
  if (typeof check.open_streams === "number") parts.push(`${check.open_streams} stream`);
  if (typeof check.awaiting_support === "number") parts.push(`${check.awaiting_support}`);
  return parts.join(" · ");
}

function ChecklistRow({ item }: { item: VerificationItem }) {
  const { t, formatDateTime } = useI18n();
  const v = t.admin.verification;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [note, setNote] = useState(item.note ?? "");
  // Shown at once; put back if the server refuses.
  const [status, setStatus] = useState<VerificationStatus>(item.status);
  const [busy, setBusy] = useState(false);
  const label = v.items[item.key as keyof typeof v.items] ?? item.key;
  // "Not applicable" only for an optional provider; the server refuses it for every other gate.
  const choices: VerificationStatus[] = ["not_checked", "passed", "failed", ...(item.optional ? ["not_applicable" as const] : [])];
  if (!choices.includes(item.status)) choices.push(item.status);

  async function save(next: VerificationStatus) {
    setBusy(true);
    setStatus(next);
    try {
      await api(`admin/verification/${item.key}`, jsonRequest("PUT", { status: next, note: note.trim() || null }));
      await client.invalidateQueries({ queryKey: keys.verification });
      toast.success(t.admin.saved);
    } catch (error) {
      setStatus(item.status);
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="space-y-1.5 border-b border-border px-3 py-2.5 last:border-0">
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1">
          <p className="text-sm">
            {label}
            {item.paid && <span className="ml-2 rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning">{v.paid}</span>}
            {item.optional && <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{v.optional}</span>}
          </p>
          <p className="break-all font-mono text-[11px] text-muted-foreground">{item.how}</p>
          {item.status !== "not_checked" && item.recorded_at && (
            <p className={cn("text-[11px]", TONE[item.status])}>
              {v.statusNames[item.status]} · {v.recordedBy(item.recorded_by ?? "—", formatDateTime(item.recorded_at))}
            </p>
          )}
        </div>
        <Select value={status} onValueChange={(next) => void save(next as VerificationStatus)} disabled={busy}>
          <SelectTrigger className={cn("h-7 w-36 shrink-0 bg-surface text-xs", TONE[status])} aria-label={`${v.statusLabel}: ${label}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {choices.map((choice) => (
              <SelectItem key={choice} value={choice} className="text-xs">{v.statusNames[choice]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex gap-1.5">
        <Input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder={v.notePlaceholder}
               aria-label={`${v.notePlaceholder}: ${label}`} className="h-7 bg-surface text-xs" />
        <Button variant="ghost" size="sm" className="h-7 text-xs" disabled={busy || note === (item.note ?? "")}
                onClick={() => void save(status)}>
          {v.saveNote}
        </Button>
      </div>
    </li>
  );
}

/**
 * Readiness (safe local checks, run when the tab opens or on request) beside the manual checklist.
 * Nothing here calls a paid API: live tests are commands an operator runs on purpose.
 */
export function AdminVerification() {
  const { t, formatDateTime } = useI18n();
  const v = t.admin.verification;
  const readiness = useSystemReadiness(true);
  const checklist = useVerification(true);
  const [stream, setStream] = useState<"idle" | "checking" | "ok" | "failed">("idle");
  const summary = checklist.data?.summary;

  function checkStream() {
    setStream("checking");
    const source = new EventSource("/api/notifications/stream");
    const timer = setTimeout(() => {
      source.close();
      setStream("failed");
    }, 10000);
    source.addEventListener("unread", () => {
      clearTimeout(timer);
      source.close();
      setStream("ok");
    });
  }

  return (
    <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] gap-3 lg:grid-cols-2 lg:grid-rows-1">
      <section className="panel flex min-h-0 flex-col">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
          <div>
            <h2 className="text-sm font-semibold">{v.readiness}</h2>
            {readiness.data && <p className="text-[11px] text-muted-foreground">{v.checkedAt(formatDateTime(readiness.data.checked_at))}</p>}
          </div>
          <div className="flex gap-1.5">
            <Button variant="outline" size="sm" className="h-7" onClick={checkStream} disabled={stream === "checking"}>
              {stream === "checking" ? <Loader2 className="size-3.5 animate-spin" /> : <Radio className="size-3.5" />}
              {v.checkStream}
            </Button>
            <Button variant="outline" size="sm" className="h-7" onClick={() => void readiness.refetch()} disabled={readiness.isFetching}>
              <RefreshCw className={cn("size-3.5", readiness.isFetching && "animate-spin")} /> {v.recheck}
            </Button>
          </div>
        </div>
        {stream !== "idle" && stream !== "checking" && (
          <p className={cn("border-b border-border px-3 py-1.5 text-xs", stream === "ok" ? "text-success" : "text-destructive")}>
            {stream === "ok" ? v.streamOk : v.streamFailed}
          </p>
        )}
        <div className="scrollbar-thin relative min-h-0 flex-1 overflow-y-auto p-3 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring" tabIndex={0}>
          {readiness.isPending && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
          <div className="space-y-4">
            {readiness.data?.sections.map((section) => (
              <div key={section.key}>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  {v.sections[section.key as keyof typeof v.sections] ?? section.key}
                </p>
                <ul className="space-y-1">
                  {section.checks.map((check) => (
                    <li key={check.key} className="flex items-start gap-2 text-xs">
                      <span className={cn("mt-1 size-2 shrink-0 rounded-full", DOT[check.status])} />
                      <span className="min-w-0 flex-1">
                        <span className="font-medium">{v.checks[check.key as keyof typeof v.checks] ?? check.key}</span>
                        <span className="text-muted-foreground"> · {v.statuses[check.status]}</span>
                        {check.detail && <span className="text-muted-foreground"> · {check.key === "alert"
                          ? (check.detail.startsWith("worker:") ? t.adminV1.workerAlert(check.detail.slice(7))
                            : t.adminV1.alerts[check.detail] ?? check.detail)
                          : v.details[check.detail as keyof typeof v.details] ?? check.detail}</span>}
                        {checkValue(check, formatDateTime) && (
                          <span className="block break-all text-[11px] text-muted-foreground">{checkValue(check, formatDateTime)}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>
      <section className="panel flex min-h-0 flex-col">
        <div className="border-b border-border px-3 py-2">
          <h2 className="text-sm font-semibold">{v.checklist}</h2>
          <p className="text-[11px] text-muted-foreground">{v.checklistHint}</p>
          {summary && (
            <p className={cn("text-[11px]", summary.complete ? "text-success" : "text-warning")}>
              {v.summary(summary.passed, summary.failed, summary.not_applicable, summary.not_checked, summary.total)}
              {" · "}
              {summary.complete ? v.releaseReady : v.releaseOpen(summary.open.length)}
            </p>
          )}
        </div>
        <ul className="scrollbar-thin relative min-h-0 flex-1 overflow-y-auto">
          {checklist.isPending && <Loader2 className="m-3 size-4 animate-spin text-muted-foreground" />}
          {checklist.data?.items.map((item, index, all) => (
            <Fragment key={`${item.key}-${item.status}-${item.recorded_at}-${item.note}`}>
              {item.group && item.group !== all[index - 1]?.group && (
                <li className="sticky top-0 z-10 border-b border-border bg-surface px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {v.groups[item.group]}
                </li>
              )}
              <ChecklistRow item={item} />
            </Fragment>
          ))}
        </ul>
      </section>
    </div>
  );
}
