"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState, type DragEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Download, Grid2x2, Layers, List, Loader2, Trash2, Upload } from "lucide-react";
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
import { EmptyState, FieldLabel, FilterPills, PageHeader } from "@/components/reelforge/primitives";
import { MediaThumb, assetKindIcon } from "@/components/reelforge/media-preview";
import { api, assetUrl, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, useDashboard } from "@/lib/queries";
import type { MediaDeleteResult } from "@/lib/types";
import { MAX_UPLOAD_BYTES, UPLOAD_ACCEPT, acceptedUpload, assetKind, formatBytes, type AssetKind } from "@/lib/studio";
import { cn } from "@/lib/utils";

type Filter = "all" | Exclude<AssetKind, "other">;
// Radix Select items cannot use an empty string, so "no project" and "every project" get their own tokens.
const NO_PROJECT = "__none__";
const ANY_PROJECT = "__any__";

function MediaPage() {
  const { t, formatDateTime } = useI18n();
  useDocumentTitle(t.media.title);
  const client = useQueryClient();
  const showError = useErrorToast();
  const search = useSearchParams();
  const { data } = useDashboard();
  const assets = data?.assets ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(search.get("asset"));
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const [filter, setFilter] = useState<Filter>("all");
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [projectFilter, setProjectFilter] = useState(ANY_PROJECT);
  const [picked, setPicked] = useState<string[]>([]);
  const [confirming, setConfirming] = useState<string[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const linked = search.get("asset");

  // Search results link here with ?asset=; follow them even when this page is already open.
  useEffect(() => {
    if (linked) setSelectedId(linked);
  }, [linked]);

  const visible = assets.filter(
    (a) =>
      (filter === "all" || assetKind(a.content_type) === filter) &&
      (projectFilter === ANY_PROJECT || (a.project_id ?? NO_PROJECT) === projectFilter),
  );
  const pickedSet = new Set(picked);
  const toggle = (id: string) => setPicked((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]));
  const confirmBytes = (confirming ?? []).reduce((sum, id) => sum + (assets.find((a) => a.id === id)?.bytes ?? 0), 0);
  const selected = assets.find((a) => a.id === selectedId) ?? visible[0] ?? null;

  async function upload(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setUploading(true);
    let last: string | null = null;
    for (const file of list) {
      if (!acceptedUpload(file)) {
        toast.error(t.media.unsupported(file.name));
        continue;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        toast.error(t.media.tooLarge(file.name));
        continue;
      }
      const body = new FormData();
      body.append("file", file);
      try {
        const result = await api<{ id: string; filename: string }>("assets", { method: "POST", body });
        last = result.id;
        toast.success(t.media.uploaded(result.filename));
      } catch (error) {
        showError(error);
      }
    }
    await client.invalidateQueries({ queryKey: keys.dashboard });
    if (last) setSelectedId(last);
    setUploading(false);
    if (input.current) input.current.value = "";
  }

  /** Attach an uploaded file to a project (or detach it); generated media stays with its run's project. */
  async function attach(assetId: string, projectId: string | null) {
    try {
      await api(`assets/${encodeURIComponent(assetId)}`, jsonRequest("PATCH", { project_id: projectId }));
      await client.invalidateQueries({ queryKey: keys.dashboard });
      toast.success(t.media.attached);
    } catch (error) {
      showError(error);
    }
  }

  /** Delete the chosen files after confirmation; media an unfinished publication still needs is kept. */
  async function remove(ids: string[]) {
    setDeleting(true);
    try {
      const result = await api<MediaDeleteResult>("assets/delete", jsonRequest("POST", { asset_ids: ids }));
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.dashboard }),
        client.invalidateQueries({ queryKey: keys.storage }),
      ]);
      if (result.deleted.length) toast.success(t.media.deleted(result.deleted.length, formatBytes(result.freed_bytes)));
      if (result.skipped.some((item) => item.reason === "asset_in_use")) toast.error(t.media.inUse);
      setPicked((current) => current.filter((id) => !result.deleted.includes(id)));
      setConfirming(null);
    } catch (error) {
      showError(error);
    } finally {
      setDeleting(false);
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (!uploading) void upload(e.dataTransfer.files);
  };

  const project = selected ? data?.projects.find((p) => p.id === selected.project_id) : undefined;
  const source = selected?.run_id ? t.library.origin.workflowOnly : t.library.origin.upload;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.media.title}
        subtitle={t.media.subtitle}
        actions={
          <Button onClick={() => input.current?.click()} disabled={uploading}>
            {uploading ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
            {uploading ? t.media.uploading : t.media.upload}
          </Button>
        }
      />
      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept={UPLOAD_ACCEPT}
        onChange={(e) => e.target.files && void upload(e.target.files)}
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <FilterPills
              value={filter}
              onChange={setFilter}
              options={(["all", "video", "image", "audio"] as const).map((f) => ({
                value: f,
                label: f === "all" ? t.common.all : t.media.kinds[f],
              }))}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={projectFilter}
                onValueChange={(value) => {
                  setProjectFilter(value);
                  setPicked([]);
                }}
              >
                <SelectTrigger className="h-8 w-48 bg-surface" aria-label={t.media.projectFilter}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY_PROJECT}>{t.media.allProjects}</SelectItem>
                  <SelectItem value={NO_PROJECT}>{t.media.noProject}</SelectItem>
                  {(data?.projects ?? []).map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="flex rounded-lg border border-border bg-surface-2 p-0.5">
                {(["grid", "list"] as const).map((l) => (
                  <button
                    key={l}
                    type="button"
                    onClick={() => setLayout(l)}
                    aria-pressed={layout === l}
                    aria-label={l === "grid" ? t.common.grid : t.common.list}
                    className={cn("rounded-md p-1.5", layout === l ? "bg-surface text-foreground" : "text-muted-foreground")}
                  >
                    {l === "grid" ? <Grid2x2 className="size-4" /> : <List className="size-4" />}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {visible.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <label className="inline-flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  className="size-3.5 accent-[var(--primary)]"
                  checked={visible.every((a) => pickedSet.has(a.id))}
                  onChange={(e) => setPicked(e.target.checked ? visible.map((a) => a.id) : [])}
                />
                {t.media.selectAll}
              </label>
              {picked.length > 0 && (
                <>
                  <span>{t.media.selectedCount(picked.length)}</span>
                  <Button variant="outline" size="sm" className="h-7 text-destructive" onClick={() => setConfirming(picked)}>
                    <Trash2 className="size-3.5" /> {t.media.deleteSelected}
                  </Button>
                </>
              )}
            </div>
          )}
          <button
            type="button"
            onClick={() => input.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            disabled={uploading}
            className={cn(
              "w-full rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground transition-colors",
              dragging ? "border-primary bg-[color-mix(in_oklab,var(--primary)_8%,transparent)] text-foreground" : "border-border-strong hover:border-primary/50",
            )}
          >
            <span className="block">{dragging ? t.media.dropActive : t.media.dropHere}</span>
            <span className="mt-1 block text-xs">{t.media.accepted}</span>
          </button>

          {assets.length === 0 ? (
            <EmptyState icon={Layers} title={t.media.empty} description={t.media.emptyHint} />
          ) : (
            <div className={cn(layout === "grid" ? "grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 3xl:grid-cols-5" : "space-y-2")}>
              {visible.map((asset) => {
                const kind = assetKind(asset.content_type);
                const Icon = assetKindIcon[kind];
                return (
                  <div key={asset.id} className="relative">
                    <input
                      type="checkbox"
                      aria-label={t.media.pick(asset.filename)}
                      checked={pickedSet.has(asset.id)}
                      onChange={() => toggle(asset.id)}
                      className={cn(
                        "absolute z-10 size-4 accent-[var(--primary)]",
                        layout === "grid" ? "left-5 top-5" : "left-3 top-1/2 -translate-y-1/2",
                      )}
                    />
                    <button
                      type="button"
                      onClick={() => setSelectedId(asset.id)}
                      aria-pressed={selected?.id === asset.id}
                      className={cn(
                        "panel w-full p-3 text-left transition-colors hover:border-border-strong",
                        selected?.id === asset.id && "border-primary/50",
                        layout === "list" && "flex items-center gap-3 pl-9",
                      )}
                    >
                      <span
                        className={cn(
                          "flex items-center justify-center overflow-hidden rounded-lg bg-surface-2",
                          layout === "grid" ? "mb-3 h-24 w-full" : "size-10 shrink-0",
                        )}
                      >
                        {layout === "grid" && kind !== "audio" ? (
                          <MediaThumb asset={asset} />
                        ) : (
                          <Icon className="size-5 text-muted-foreground" />
                        )}
                      </span>
                      <span className="block min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{asset.filename}</span>
                        <span className="block text-xs text-muted-foreground">
                          {t.media.kinds[kind]} · {formatBytes(asset.bytes)}
                        </span>
                      </span>
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <aside className="panel h-fit space-y-4 p-5 lg:sticky lg:top-20">
          {selected ? (
            <>
              <div className="aspect-video overflow-hidden rounded-lg bg-black">
                <MediaThumb key={selected.id} asset={selected} controls />
              </div>
              <p className="break-words text-sm font-medium">{selected.filename}</p>
              <dl className="space-y-2 text-sm">
                {(
                  [
                    [t.media.details.type, t.media.kinds[assetKind(selected.content_type)]],
                    [t.media.details.size, formatBytes(selected.bytes)],
                    [t.media.details.format, selected.content_type],
                    [t.media.details.usedIn, project?.title ?? t.media.notUsed],
                    [t.media.details.source, source],
                    ...(selected.created_at ? [[t.media.details.added, formatDateTime(selected.created_at)]] : []),
                  ] as [string, string][]
                ).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="min-w-0 break-words text-right">{v}</dd>
                  </div>
                ))}
              </dl>
              <div className="flex flex-col gap-2">
                <Button asChild variant="outline" size="sm" className="w-full">
                  <a href={assetUrl(selected.id)}>
                    <Download className="size-4" /> {t.common.download}
                  </a>
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full text-destructive hover:text-destructive"
                  onClick={() => setConfirming([selected.id])}
                >
                  <Trash2 className="size-4" /> {t.media.delete}
                </Button>
                {!selected.run_id && (
                  <div>
                    <FieldLabel>{t.media.addToProject}</FieldLabel>
                    <Select
                      value={selected.project_id ?? NO_PROJECT}
                      onValueChange={(value) => void attach(selected.id, value === NO_PROJECT ? null : value)}
                    >
                      <SelectTrigger className="h-8 bg-surface" aria-label={t.media.addToProject}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NO_PROJECT}>{t.media.noProject}</SelectItem>
                        {(data?.projects ?? []).map((project) => (
                          <SelectItem key={project.id} value={project.id}>
                            {project.title}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">{t.media.selectHint}</p>
          )}
        </aside>
      </div>
      <AlertDialog open={Boolean(confirming)} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.media.deleteTitle(confirming?.length ?? 0)}</AlertDialogTitle>
            <AlertDialogDescription>{t.media.deleteDescription(formatBytes(confirmBytes))}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting}
              onClick={(event) => {
                event.preventDefault();
                if (confirming) void remove(confirming);
              }}
            >
              {deleting && <Loader2 className="size-4 animate-spin" />}
              {t.media.delete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <MediaPage />
    </Suspense>
  );
}
