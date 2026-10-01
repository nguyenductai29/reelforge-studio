"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Radio, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useSystemReadiness, useVerification } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { SystemCheck, SystemCheckStatus, VerificationItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const DOT: Record<SystemCheckStatus, string> = {
  ok: "bg-success",
  warning: "bg-warning",
  error: "bg-destructive",
  missing: "bg-destructive",
  off: "bg-muted-foreground/40",
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
  const [verified, setVerified] = useState(item.verified);
  const [busy, setBusy] = useState(false);

  async function save(next: boolean) {
    setBusy(true);
    setVerified(next);
    try {
      await api(`admin/verification/${item.key}`, jsonRequest("PUT", { verified: next, note: note.trim() || null }));
      await client.invalidateQueries({ queryKey: keys.verification });
      toast.success(t.admin.saved);
    } catch (error) {
      setVerified(!next);
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="space-y-1.5 border-b border-border px-3 py-2.5 last:border-0">
      <div className="flex items-start gap-2.5">
        <input
          type="checkbox"
          checked={verified}
          disabled={busy}
          onChange={(e) => void save(e.target.checked)}
          aria-label={v.items[item.key as keyof typeof v.items] ?? item.key}
          className="mt-0.5 size-4 shrink-0 accent-[var(--primary)]"
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm">
            {v.items[item.key as keyof typeof v.items] ?? item.key}
            {item.paid && <span className="ml-2 rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning">{v.paid}</span>}
          </p>
          <p className="break-all font-mono text-[11px] text-muted-foreground">{item.how}</p>
          {item.verified && item.verified_at && (
            <p className="text-[11px] text-success">{v.verifiedBy(item.verified_by ?? "—", formatDateTime(item.verified_at))}</p>
          )}
        </div>
      </div>
      <div className="flex gap-1.5 pl-6.5">
        <Input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder={v.notePlaceholder}
               aria-label={v.notePlaceholder} className="h-7 bg-surface text-xs" />
        <Button variant="ghost" size="sm" className="h-7 text-xs" disabled={busy || note === (item.note ?? "")}
                onClick={() => void save(verified)}>
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
  const done = (checklist.data ?? []).filter((item) => item.verified).length;

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
        <div className="scrollbar-thin relative min-h-0 flex-1 overflow-y-auto p-3">
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
                        {check.detail && <span className="text-muted-foreground"> · {v.details[check.detail as keyof typeof v.details] ?? check.detail}</span>}
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
          <p className="text-[11px] text-muted-foreground">{v.checklistHint(done, checklist.data?.length ?? 0)}</p>
        </div>
        <ul className="scrollbar-thin relative min-h-0 flex-1 overflow-y-auto">
          {checklist.isPending && <Loader2 className="m-3 size-4 animate-spin text-muted-foreground" />}
          {checklist.data?.map((item) => (
            <ChecklistRow key={`${item.key}-${item.verified_at}-${item.note}`} item={item} />
          ))}
        </ul>
      </section>
    </div>
  );
}
