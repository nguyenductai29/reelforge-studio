"use client";

import Link from "next/link";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Trash2 } from "lucide-react";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useDashboard, useStorage } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { StorageCleanup, StorageLevel, StorageLevelInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { FieldLabel } from "./primitives";

const BAR: Record<StorageLevel, string> = {
  ok: "bg-primary",
  notice: "bg-info",
  warning: "bg-warning",
  critical: "bg-destructive",
  full: "bg-destructive",
};
export const STORAGE_TEXT: Record<StorageLevel, string> = {
  ok: "text-muted-foreground",
  notice: "text-info",
  warning: "text-warning",
  critical: "text-destructive",
  full: "text-destructive",
};
const ALL = "__all__";

export function StorageBar({ info, className, label }: { info: StorageLevelInfo; className?: string; label?: string }) {
  const { t } = useI18n();
  const width = Math.min(100, info.percent);
  return (
    <div
      role="meter"
      aria-label={label ?? t.storage.usedOf(formatBytes(info.used_bytes), formatBytes(info.quota_bytes))}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={width}
      aria-valuetext={`${info.percent}%`}
      className={cn("h-2 w-full overflow-hidden rounded-full bg-surface-2", className)}
    >
      <div className={cn("h-full rounded-full transition-all", BAR[info.level])} style={{ width: `${width}%` }} />
    </div>
  );
}

/** Used of quota, the bar, and what the warning level means (70, 80, 90 and 100 %). */
export function StorageMeter({ info }: { info: StorageLevelInfo }) {
  const { t } = useI18n();
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span>{t.storage.usedOf(formatBytes(info.used_bytes), formatBytes(info.quota_bytes))}</span>
        <span className={cn("tabular-nums", STORAGE_TEXT[info.level])}>{info.percent}%</span>
      </div>
      <StorageBar info={info} />
      {info.level !== "ok" && <p className={cn("text-xs", STORAGE_TEXT[info.level])}>{t.storage.levels[info.level]}</p>}
    </div>
  );
}

/** From 80 %: a notice above every page with a way to free space. */
export function StorageAlert() {
  const { t } = useI18n();
  const storage = useDashboard().data?.storage;
  if (!storage || (storage.level !== "warning" && storage.level !== "critical" && storage.level !== "full")) return null;
  return (
    <div
      role="status"
      className={cn(
        "mb-5 flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm",
        storage.level === "warning"
          ? "border-warning/40 bg-[color-mix(in_oklab,var(--warning)_10%,transparent)]"
          : "border-destructive/40 bg-[color-mix(in_oklab,var(--destructive)_10%,transparent)]",
      )}
    >
      <AlertTriangle className={cn("size-4 shrink-0", STORAGE_TEXT[storage.level])} />
      <span className="min-w-0 flex-1">{t.storage.banner[storage.level](String(storage.percent))}</span>
      <Button asChild size="sm" variant="outline">
        <Link href="/settings?tab=storage">{t.storage.manage}</Link>
      </Button>
    </div>
  );
}

/** Remove intermediate media (scene clips, narration, generated images, source cuts) of runs that have their final video. */
export function IntermediateCleanup() {
  const { t } = useI18n();
  const s = t.storage;
  const client = useQueryClient();
  const showError = useErrorToast();
  const projects = useDashboard().data?.projects ?? [];
  const usage = useStorage().data;
  const [project, setProject] = useState(ALL);
  const [preview, setPreview] = useState<StorageCleanup | null>(null);
  const [busy, setBusy] = useState(false);
  const scope = project === ALL ? {} : { project_id: project };

  async function check() {
    setBusy(true);
    try {
      setPreview(await api<StorageCleanup>("storage/cleanup", jsonRequest("POST", scope)));
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true);
    try {
      const done = await api<StorageCleanup>("storage/cleanup", jsonRequest("POST", { ...scope, apply: true }));
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.storage }),
        client.invalidateQueries({ queryKey: keys.dashboard }),
      ]);
      toast.success(s.cleaned(done.assets, formatBytes(done.bytes)));
      setPreview(null);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-3">
      <div>
        <p className="text-sm font-medium">{s.intermediateTitle}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {s.intermediateHint(usage?.retention?.intermediate_days ?? 30)}
        </p>
        {usage?.intermediate && (
          <p className="mt-1 text-xs">{s.intermediateNow(usage.intermediate.assets, formatBytes(usage.intermediate.bytes))}</p>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[200px] flex-1">
          <FieldLabel>{s.scope}</FieldLabel>
          <Select value={project} onValueChange={setProject}>
            <SelectTrigger className="h-8 bg-surface" aria-label={s.scope}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{s.allProjects}</SelectItem>
              {projects.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void check()}>
          {busy && !preview ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
          {s.removeIntermediate}
        </Button>
      </div>
      <AlertDialog open={Boolean(preview)} onOpenChange={(open) => !open && setPreview(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{s.confirmTitle}</AlertDialogTitle>
            <AlertDialogDescription>
              {preview && preview.assets > 0
                ? s.confirmDescription(preview.assets, formatBytes(preview.bytes))
                : s.nothingToRemove}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>{t.common.cancel}</AlertDialogCancel>
            {preview && preview.assets > 0 && (
              <AlertDialogAction
                disabled={busy}
                onClick={(event) => {
                  event.preventDefault();
                  void apply();
                }}
              >
                {busy && <Loader2 className="size-4 animate-spin" />}
                {s.removeConfirm}
              </AlertDialogAction>
            )}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
