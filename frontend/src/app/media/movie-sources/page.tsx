"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronUp,
  Clapperboard,
  File as FileIcon,
  Folder,
  Loader2,
  MoreHorizontal,
  Plus,
  Search,
} from "lucide-react";
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
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DataTable, useDebounced, type Column } from "@/components/reelforge/data-table";
import { FilterSelect } from "@/components/reelforge/admin/shared";
import { MediaTabs } from "@/components/reelforge/media-tabs";
import { FieldLabel, PageHeader, StatusBadge } from "@/components/reelforge/primitives";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { can } from "@/lib/permissions";
import { keys, useDashboard, useMovieSource, useMovieSources, useRun } from "@/lib/queries";
import { formatBytes, formatClock } from "@/lib/studio";
import type {
  DriveInboxListing,
  LocalImportListing,
  MovieReviewMode,
  MovieSource,
  MovieSourceConfig,
  MovieSourceStatus,
  MovieSourceType,
  MovieWorkflowStart,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const LIMIT = 20;
const STATUSES: MovieSourceStatus[] = ["importing", "ready", "processing", "completed", "failed", "delete_scheduled", "deleted"];
const TYPES: MovieSourceType[] = ["local", "url", "drive"];
const NONE = "__none__";
// The movie steps of a review, in pipeline order (their run outputs carry a progress count).
const MOVIE_STEPS = ["movie", "prepare", "transcript", "visual", "timeline", "analysis", "script", "voice", "select", "clips", "render", "review"];

function useRefresh() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: keys.movieSources });
}

/** Bytes moved so far while importing or uploading, as a share of the movie (when its size is known). */
function TransferProgress({ source }: { source: MovieSource }) {
  if (!source.progress_bytes || !source.bytes) return null;
  const percent = Math.min(100, Math.round((source.progress_bytes / source.bytes) * 100));
  return (
    <span className="mt-1 flex items-center gap-2">
      <Progress value={percent} className="h-1 w-20" />
      <span className="text-[11px] tabular-nums text-muted-foreground">{percent}%</span>
    </span>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">{children}</dd>
    </div>
  );
}

/** The steps of the latest active run of this source, with their progress counts (frames, batches, clips). */
function RunProgress({ runId }: { runId: string }) {
  const { t } = useI18n();
  const m = t.movieSources;
  const run = useRun(runId);
  const steps = (run.data?.steps ?? []).filter((step) => MOVIE_STEPS.includes(step.node_id))
    .sort((a, b) => MOVIE_STEPS.indexOf(a.node_id) - MOVIE_STEPS.indexOf(b.node_id));
  if (!run.data) return <Loader2 className="size-4 animate-spin text-muted-foreground" />;
  return (
    <ol className="space-y-1.5" aria-label={m.detail.progress}>
      {steps.map((step) => {
        const progress = step.output?.progress as { stage?: string; done?: number; total?: number; frames_done?: number; frames_total?: number } | undefined;
        const counts = progress && typeof progress.done === "number" && typeof progress.total === "number" && progress.total > 0
          ? m.progress.counts(progress.stage ?? "", progress.done, progress.total) : null;
        return (
          <li key={step.node_id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate">{t.nodes[step.node_type]?.name ?? step.node_type}</span>
            <span className="flex items-center gap-2">
              {counts && <span className="tabular-nums text-muted-foreground">{counts}</span>}
              {typeof progress?.frames_total === "number" && progress.frames_total > 0 && (
                <span className="tabular-nums text-muted-foreground">{m.progress.frames(progress.frames_done ?? 0, progress.frames_total)}</span>
              )}
              <StatusBadge status={step.status} label={t.status.run[step.status] ?? step.status} className="px-2 py-0.5 text-[10px]" />
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function DetailDialog({ sourceId, onClose, onAction }: {
  sourceId: string | null;
  onClose: () => void;
  onAction: (action: Action, source: MovieSource) => void;
}) {
  const { t, formatDateTime } = useI18n();
  const m = t.movieSources;
  const detail = useMovieSource(sourceId);
  const source = detail.data;
  const active = source?.runs.find((run) => run.status === "running" || run.status === "awaiting_review");
  return (
    <Dialog open={Boolean(sourceId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="break-words pr-6">{source?.name ?? m.detail.title}</DialogTitle>
          <DialogDescription>{source ? `${m.types[source.source_type]} · ${m.statuses[source.status]}` : m.detail.title}</DialogDescription>
        </DialogHeader>
        {detail.isError ? (
          <p className="text-sm text-destructive">{errorText(detail.error, t)}</p>
        ) : !source ? (
          <Loader2 className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
        ) : (
          <div className="space-y-5">
            {source.failure && (
              <div role="alert" className="flex gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <span>
                  {m.detail.failure(m.detail.stages[source.failure.stage as keyof typeof m.detail.stages] ?? source.failure.stage ?? "",
                                    t.errors.codes[source.failure.code ?? ""] ?? source.failure.message ?? source.failure.code ?? "")}
                </span>
              </div>
            )}
            <dl className="grid gap-3 sm:grid-cols-3">
              <Fact label={m.detail.size}>{source.bytes ? formatBytes(source.bytes) : "—"}</Fact>
              <Fact label={m.detail.duration}>{source.duration_seconds ? formatClock(source.duration_seconds) : "—"}</Fact>
              <Fact label={m.detail.resolution}>{source.width && source.height ? `${source.width} × ${source.height}` : "—"}</Fact>
              <Fact label={m.detail.format}>{source.container?.toUpperCase() ?? "—"}</Fact>
              <Fact label={m.detail.codecs}>{[source.video_codec, source.audio_codec].filter(Boolean).join(" / ") || "—"}</Fact>
              <Fact label={m.detail.project}>{source.project?.title ?? "—"}</Fact>
              <Fact label={m.detail.source}>
                <span className="font-mono text-xs">{source.original_url ?? source.local_path ?? m.types[source.source_type]}</span>
              </Fact>
              <Fact label={m.detail.added}>{source.created_at ? formatDateTime(source.created_at) : "—"}</Fact>
              <Fact label={m.detail.ready}>{source.ready_at ? formatDateTime(source.ready_at) : "—"}</Fact>
              <Fact label={m.detail.expires}>{source.expires_at ? formatDateTime(source.expires_at) : "—"}</Fact>
              <Fact label={m.detail.deletionDue}>{source.deletion_due_at ? formatDateTime(source.deletion_due_at) : "—"}</Fact>
              {source.success_at && <Fact label={m.detail.success}>{formatDateTime(source.success_at)}</Fact>}
              {source.deleted_at && <Fact label={m.detail.deleted}>{formatDateTime(source.deleted_at)}</Fact>}
            </dl>
            <p className="text-xs text-muted-foreground">
              {source.stored_in_drive ? m.detail.storedInDrive : m.detail.notInDrive} ·{" "}
              {source.delete_after_success ? m.detail.afterSuccess(source.delete_grace_hours) : m.detail.keepAfterSuccess}
            </p>
            {active && (
              <section className="space-y-2">
                <h3 className="text-sm font-medium">{m.detail.progress}</h3>
                <RunProgress runId={active.id} />
              </section>
            )}
            <section className="space-y-2">
              <h3 className="text-sm font-medium">{m.detail.runs}</h3>
              {source.runs.length === 0 ? (
                <p className="text-xs text-muted-foreground">{m.detail.noRuns}</p>
              ) : (
                <ul className="divide-y divide-border rounded-lg border border-border">
                  {source.runs.map((run) => (
                    <li key={run.id} className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
                      <span className="text-muted-foreground">{run.created_at ? formatDateTime(run.created_at) : run.id.slice(0, 8)}</span>
                      <span className="flex items-center gap-2">
                        <StatusBadge status={run.status} label={t.status.run[run.status] ?? run.status} className="px-2 py-0.5 text-[10px]" />
                        <Link href={`/workflows/${run.workflow_id}?run=${run.id}`} className="text-primary hover:underline">
                          {m.detail.openRun}
                        </Link>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
        {source && (
          <DialogFooter className="flex-wrap gap-2 sm:justify-start">
            <SourceActions source={source} onAction={onAction} buttons />
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

type Action = "view" | "review" | "recap" | "extend" | "delete" | "retryImport" | "retryUpload" | "retryDelete";

function actionsFor(source: MovieSource, allowed: { edit: boolean; run: boolean; remove: boolean }): Action[] {
  const list: Action[] = [];
  if (source.can_use && allowed.run) list.push("review", "recap");
  if (source.can_extend && allowed.edit) list.push("extend");
  if (source.can_retry_upload && allowed.edit) list.push("retryUpload");
  else if (source.can_retry_import && allowed.edit) list.push("retryImport");
  if (source.status === "delete_scheduled" && source.failure && allowed.edit) list.push("retryDelete");
  if (!["delete_scheduled", "deleting", "deleted"].includes(source.status) && allowed.remove) list.push("delete");
  return list;
}

function SourceActions({ source, onAction, buttons = false }: {
  source: MovieSource;
  onAction: (action: Action, source: MovieSource) => void;
  buttons?: boolean;
}) {
  const { t } = useI18n();
  const m = t.movieSources;
  const { data: dashboard } = useDashboard();
  const allowed = { edit: can(dashboard, "content.edit"), run: can(dashboard, "runs.execute"),
                    remove: can(dashboard, "movie_sources.delete") };
  const list = actionsFor(source, allowed);
  if (buttons) {
    return (
      <>
        {list.map((action) => (
          <Button key={action} size="sm" variant={action === "review" ? "default" : action === "delete" ? "ghost" : "outline"}
                  className={cn(action === "delete" && "text-destructive hover:text-destructive")}
                  onClick={() => onAction(action, source)}>
            {m.actions[action]}
          </Button>
        ))}
      </>
    );
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-7" aria-label={`${m.columns.actions} · ${source.name}`}>
          <MoreHorizontal className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => onAction("view", source)}>{m.actions.view}</DropdownMenuItem>
        {list.length > 0 && <DropdownMenuSeparator />}
        {list.map((action) => (
          <DropdownMenuItem key={action} onClick={() => onAction(action, source)}
                            className={cn(action === "delete" && "text-destructive focus:text-destructive")}>
            {m.actions[action]}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The import folder on the server: folders to open, movie files to pick (names and sizes only). */
function LocalBrowser({ value, onPick }: { value: string; onPick: (path: string, name: string) => void }) {
  const { t, formatDateTime } = useI18n();
  const m = t.movieSources.addDialog;
  const [folder, setFolder] = useState("");
  const listing = useQuery({
    queryKey: [...keys.movieSources, "local", folder],
    queryFn: () => api<LocalImportListing>(`movie-sources/local-files?folder=${encodeURIComponent(folder)}`),
  });
  const data = listing.data;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="min-w-0 truncate font-mono">/{data?.folder ?? folder}</span>
        {data?.parent !== null && data?.parent !== undefined && (
          <Button type="button" size="sm" variant="ghost" className="h-7" onClick={() => setFolder(data.parent ?? "")}>
            <ChevronUp className="size-3.5" /> {m.up}
          </Button>
        )}
      </div>
      <div className="scrollbar-thin max-h-64 overflow-y-auto rounded-lg border border-border">
        {listing.isError ? (
          <p className="p-3 text-sm text-destructive">{errorText(listing.error, t)}</p>
        ) : !data ? (
          <Loader2 className="m-3 size-4 animate-spin text-muted-foreground" />
        ) : data.entries.length === 0 ? (
          <p className="p-3 text-sm text-muted-foreground">{m.emptyFolder}</p>
        ) : (
          <ul className="divide-y divide-border">
            {data.entries.map((entry) => (
              <li key={entry.path}>
                <button
                  type="button"
                  onClick={() => (entry.type === "folder" ? setFolder(entry.path) : onPick(entry.path, entry.name))}
                  aria-pressed={entry.type === "file" ? value === entry.path : undefined}
                  className={cn("flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2",
                    value === entry.path && "bg-primary/10")}
                >
                  {entry.type === "folder" ? <Folder className="size-4 text-muted-foreground" /> : <FileIcon className="size-4 text-muted-foreground" />}
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  {entry.type === "file" && (
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {entry.bytes !== undefined ? formatBytes(entry.bytes) : ""}
                      {entry.modified_at ? ` · ${formatDateTime(entry.modified_at)}` : ""}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {data?.truncated && <p className="text-[11px] text-muted-foreground">{m.truncated}</p>}
    </div>
  );
}

/** The studio's inbox on the operator's Google Drive. */
function DriveBrowser({ value, onPick }: { value: string; onPick: (id: string, name: string) => void }) {
  const { t, formatDateTime } = useI18n();
  const m = t.movieSources.addDialog;
  const [pages, setPages] = useState<string[]>([""]);
  const listing = useQuery({
    queryKey: [...keys.movieSources, "drive", pages],
    queryFn: async () => {
      const files = [];
      let next: string | null = null;
      for (const token of pages) {
        const page: DriveInboxListing = await api<DriveInboxListing>(
          `movie-sources/drive-files${token ? `?page_token=${encodeURIComponent(token)}` : ""}`);
        files.push(...page.files);
        next = page.next_page_token;
      }
      return { files, next };
    },
  });
  const data = listing.data;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">{m.driveHint}</p>
      <div className="scrollbar-thin max-h-64 overflow-y-auto rounded-lg border border-border">
        {listing.isError ? (
          <p className="p-3 text-sm text-destructive">{errorText(listing.error, t)}</p>
        ) : !data ? (
          <Loader2 className="m-3 size-4 animate-spin text-muted-foreground" />
        ) : data.files.length === 0 ? (
          <p className="p-3 text-sm text-muted-foreground">{m.driveEmpty}</p>
        ) : (
          <ul className="divide-y divide-border">
            {data.files.map((file) => (
              <li key={file.id}>
                <button type="button" onClick={() => onPick(file.id, file.name)} aria-pressed={value === file.id}
                        className={cn("flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2",
                          value === file.id && "bg-primary/10")}>
                  <Clapperboard className="size-4 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {file.bytes !== null ? formatBytes(file.bytes) : ""}
                    {file.created_at ? ` · ${formatDateTime(file.created_at)}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {data?.next && (
        <Button type="button" size="sm" variant="outline" onClick={() => setPages((current) => [...current, data.next!])}>
          {m.more}
        </Button>
      )}
    </div>
  );
}

function AddDialog({ open, config, onClose }: { open: boolean; config: MovieSourceConfig | undefined; onClose: () => void }) {
  const { t } = useI18n();
  const m = t.movieSources.addDialog;
  const refresh = useRefresh();
  const showError = useErrorToast();
  const { data: dashboard } = useDashboard();
  const [tab, setTab] = useState<MovieSourceType>("local");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [driveId, setDriveId] = useState("");
  const [name, setName] = useState("");
  const [project, setProject] = useState(NONE);
  const [rights, setRights] = useState(false);
  const [busy, setBusy] = useState(false);
  const picked = tab === "local" ? path : tab === "url" ? url.trim() : driveId;

  function reset() {
    setPath("");
    setUrl("");
    setDriveId("");
    setName("");
    setProject(NONE);
    setRights(false);
  }

  async function submit() {
    setBusy(true);
    try {
      await api("movie-sources", jsonRequest("POST", {
        source_type: tab, path: tab === "local" ? path : undefined, url: tab === "url" ? url.trim() : undefined,
        drive_file_id: tab === "drive" ? driveId : undefined, name: name.trim() || undefined,
        project_id: project === NONE ? undefined : project,
      }));
      await refresh();
      toast.success(m.added);
      reset();
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !busy && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{m.title}</DialogTitle>
          <DialogDescription>{m.description}</DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={(value) => setTab(value as MovieSourceType)}>
          <TabsList className="w-full">
            {TYPES.map((type) => (
              <TabsTrigger key={type} value={type} className="flex-1"
                           disabled={(type === "local" && config && !config.local_import) || false}>
                {m.tabs[type]}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="local" className="mt-3 space-y-2">
            <p className="text-xs text-muted-foreground">{m.localHint}</p>
            {config && !config.local_import ? (
              <p className="text-sm text-warning">{m.localUnavailable(dashboard?.workspace.id ?? "")}</p>
            ) : (
              open && tab === "local" && <LocalBrowser value={path} onPick={(next, file) => { setPath(next); if (!name) setName(file); }} />
            )}
          </TabsContent>
          <TabsContent value="url" className="mt-3 space-y-2">
            <FieldLabel htmlFor="movie-url">{m.urlLabel}</FieldLabel>
            <Input id="movie-url" type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)}
                   placeholder={m.urlPlaceholder} className="bg-surface font-mono text-xs" maxLength={2000} />
            <p className="text-xs text-muted-foreground">{m.urlHint}</p>
          </TabsContent>
          <TabsContent value="drive" className="mt-3">
            {open && tab === "drive" && <DriveBrowser value={driveId} onPick={(id, file) => { setDriveId(id); setName(file); }} />}
          </TabsContent>
        </Tabs>
        {picked && tab !== "url" && <p className="text-xs text-muted-foreground">{m.selected(name || picked)}</p>}
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <FieldLabel htmlFor="movie-name">{m.name}</FieldLabel>
            <Input id="movie-name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} className="bg-surface" />
          </div>
          <div>
            <FieldLabel>{m.project}</FieldLabel>
            <Select value={project} onValueChange={setProject}>
              <SelectTrigger className="bg-surface" aria-label={m.project}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>{m.noProject}</SelectItem>
                {(dashboard?.projects ?? []).map((item) => (
                  <SelectItem key={item.id} value={item.id}>{item.title}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5 size-4" checked={rights} onChange={(e) => setRights(e.target.checked)} />
          <span>{m.rights}</span>
        </label>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>{t.common.cancel}</Button>
          <Button onClick={() => void submit()} disabled={busy || !picked || !rights}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {m.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ExtendDialog({ source, config, onClose }: { source: MovieSource | null; config: MovieSourceConfig | undefined; onClose: () => void }) {
  const { t, formatDateTime } = useI18n();
  const m = t.movieSources.extendDialog;
  const refresh = useRefresh();
  const showError = useErrorToast();
  const [busy, setBusy] = useState<number | null>(null);

  async function extend(days: number) {
    if (!source) return;
    setBusy(days);
    try {
      await api(`movie-sources/${source.id}/extend`, jsonRequest("POST", { days }));
      await refresh();
      toast.success(m.done);
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={Boolean(source)} onOpenChange={(open) => !open && busy === null && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{m.title}</DialogTitle>
          <DialogDescription>{m.description(config?.max_retention_days ?? 30)}</DialogDescription>
        </DialogHeader>
        {source?.expires_at && <p className="text-sm">{m.current(formatDateTime(source.expires_at))}</p>}
        <div className="flex gap-2">
          {(config?.extend_days ?? [1, 3, 7]).map((days) => (
            <Button key={days} variant="outline" className="flex-1" disabled={busy !== null} onClick={() => void extend(days)}>
              {busy === days && <Loader2 className="size-4 animate-spin" />}
              {m.days(days)}
            </Button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function UseDialog({ request, onClose }: { request: { source: MovieSource; kind: "review" | "recap" } | null; onClose: () => void }) {
  const { t } = useI18n();
  const m = t.movieSources.useDialog;
  const router = useRouter();
  const refresh = useRefresh();
  const showError = useErrorToast();
  const [mode, setMode] = useState<MovieReviewMode>("review");
  const [spoiler, setSpoiler] = useState("light");
  const [tone, setTone] = useState("neutral");
  const [duration, setDuration] = useState("90");
  const [language, setLanguage] = useState("auto");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!request) return;
    setMode(request.kind === "recap" ? "recap" : "review");
    setSpoiler(request.kind === "recap" ? "full" : "light");
    setDuration(request.kind === "recap" ? "120" : "90");
  }, [request]);

  async function start() {
    if (!request) return;
    setBusy(true);
    try {
      const endpoint = mode === "recap" ? "movie-recap" : "movie-review";
      const started = await api<MovieWorkflowStart>(`movie-sources/${request.source.id}/${endpoint}`, jsonRequest("POST", {
        mode, spoiler_level: spoiler, tone, duration: Number(duration), language,
      }));
      await refresh();
      toast.success(m.started);
      onClose();
      router.push(`/workflows/${started.workflow_id}?run=${started.run.id}`);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  const select = (label: string, value: string, onChange: (value: string) => void, options: [string, string][]) => (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="bg-surface" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map(([key, text]) => <SelectItem key={key} value={key}>{text}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );

  return (
    <Dialog open={Boolean(request)} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{request?.kind === "recap" ? m.recapTitle : m.reviewTitle}</DialogTitle>
          <DialogDescription>{request?.source.name} — {m.description}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          {select(m.mode, mode, (value) => setMode(value as MovieReviewMode),
                  (["review", "recap", "ending_explained"] as const).map((key) => [key, m.modes[key]]))}
          {select(m.spoiler, spoiler, setSpoiler, (["none", "light", "full"] as const).map((key) => [key, t.config.options.spoiler_level[key]]))}
          {select(m.tone, tone, setTone, (["neutral", "cinematic", "storytelling", "documentary", "funny", "critical"] as const)
            .map((key) => [key, t.config.options.tone[key] ?? key]))}
          {select(m.duration, duration, setDuration, ["60", "90", "120", "180", "300", "480"].map((value) => [value, t.config.seconds(Number(value))]))}
          {select(m.language, language, setLanguage, (["auto", "vi", "en", "ja"] as const).map((key) => [key, t.config.options.language[key]]))}
        </div>
        <p className="text-xs text-muted-foreground">{t.movieSources.notice}</p>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>{t.common.cancel}</Button>
          <Button onClick={() => void start()} disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {m.start}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MovieSourcesPage() {
  const { t, formatDate, formatRelative } = useI18n();
  const m = t.movieSources;
  useDocumentTitle(m.title);
  const search = useSearchParams();
  const refresh = useRefresh();
  const showError = useErrorToast();
  const { data: dashboard } = useDashboard();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);
  const [viewing, setViewing] = useState<string | null>(search.get("source"));
  const [extending, setExtending] = useState<MovieSource | null>(null);
  const [deleting, setDeleting] = useState<MovieSource | null>(null);
  const [using, setUsing] = useState<{ source: MovieSource; kind: "review" | "recap" } | null>(null);
  const [busy, setBusy] = useState(false);
  const term = useDebounced(q.trim());
  const sources = useMovieSources({ q: term, status, source_type: type, limit: LIMIT, offset });
  const config = sources.data?.config;
  const linked = search.get("source");
  const canAdd = can(dashboard, "content.edit") && Boolean(config?.enabled) && !config?.drive_problem;

  // Notifications link here with ?source=; follow them even when this page is already open.
  useEffect(() => {
    if (linked) setViewing(linked);
  }, [linked]);

  const filtered = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };

  async function post(path: string, body: unknown, done: string) {
    try {
      await api(path, jsonRequest("POST", body));
      await refresh();
      toast.success(done);
    } catch (error) {
      showError(error);
    }
  }

  function onAction(action: Action, source: MovieSource) {
    if (action === "view") setViewing(source.id);
    else if (action === "review" || action === "recap") setUsing({ source, kind: action });
    else if (action === "extend") setExtending(source);
    else if (action === "delete") setDeleting(source);
    else if (action === "retryUpload") void post(`movie-sources/${source.id}/retry`, { stage: "upload" }, m.retried);
    else void post(`movie-sources/${source.id}/retry`, { stage: "import" }, m.retried);
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api(`movie-sources/${encodeURIComponent(deleting.id)}`, { method: "DELETE" });
      await refresh();
      toast.success(m.deleteDialog.done);
      setDeleting(null);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<MovieSource>[] = [
    { key: "name", header: m.columns.name, className: "max-w-[280px] 2xl:max-w-[420px]",
      cell: (row) => (
        <button type="button" onClick={() => setViewing(row.id)} className="block min-w-0 max-w-full text-left">
          <span className="block truncate font-medium hover:underline" title={row.name}>{row.name}</span>
        </button>
      ) },
    { key: "type", header: m.columns.type, className: "whitespace-nowrap text-muted-foreground", cell: (row) => m.types[row.source_type] },
    { key: "status", header: m.columns.status, className: "whitespace-nowrap",
      cell: (row) => (
        <span className="block">
          <StatusBadge status={row.status} label={m.statuses[row.status]} />
          {row.in_use && <span className="mt-1 block text-[11px] text-muted-foreground">{m.inUse}</span>}
          {row.failure && <span className="mt-1 block max-w-[220px] truncate text-[11px] text-destructive" title={row.failure.message ?? ""}>
            {t.errors.codes[row.failure.code ?? ""] ?? row.failure.code}</span>}
          <TransferProgress source={row} />
        </span>
      ) },
    { key: "size", header: m.columns.size, className: "whitespace-nowrap text-right tabular-nums",
      cell: (row) => (row.bytes ? formatBytes(row.bytes) : "—") },
    { key: "duration", header: m.columns.duration, className: "whitespace-nowrap text-right tabular-nums",
      cell: (row) => (row.duration_seconds ? formatClock(row.duration_seconds) : "—") },
    { key: "project", header: m.columns.project, className: "max-w-[180px] text-muted-foreground 2xl:max-w-[260px]",
      cell: (row) => <span className="block truncate" title={row.project?.title}>{row.project?.title ?? "—"}</span> },
    { key: "expires", header: m.columns.expires, className: "whitespace-nowrap text-muted-foreground",
      cell: (row) => (row.deleted_at ? formatDate(row.deleted_at) : row.deletion_due_at ? formatRelative(row.deletion_due_at) : "—") },
    { key: "added", header: m.columns.added, className: "whitespace-nowrap text-muted-foreground",
      cell: (row) => (row.created_at ? formatDate(row.created_at) : "—") },
    { key: "actions", header: <span className="sr-only">{m.columns.actions}</span>, className: "w-10 text-right",
      cell: (row) => <SourceActions source={row} onAction={onAction} /> },
  ];

  return (
    <div className="flex min-h-[calc(100dvh-7rem)] flex-col gap-4">
      <PageHeader
        title={m.title}
        subtitle={m.subtitle}
        actions={
          can(dashboard, "content.edit") && (
            <Button onClick={() => setAdding(true)} disabled={!canAdd}>
              <Plus className="size-4" /> {m.add}
            </Button>
          )
        }
      />
      <MediaTabs active="movies" />
      {config && !config.enabled && <p role="status" className="rounded-lg border border-border bg-surface-2 p-3 text-sm">{m.disabled}</p>}
      {config?.enabled && config.drive_problem && (
        <p role="status" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-warning">{m.driveNotReady}</p>
      )}
      <p className="text-xs text-muted-foreground">{m.notice}</p>
      <DataTable
        columns={columns}
        rows={sources.data?.items ?? []}
        rowKey={(row) => row.id}
        loading={sources.isFetching}
        error={sources.isError ? errorText(sources.error, t) : null}
        empty={term || status || type ? m.noMatches : m.empty}
        minWidth={900}
        total={sources.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffset={setOffset}
        className="min-h-[24rem]"
        toolbar={
          <>
            <div className="relative min-w-[200px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} type="search" onChange={(e) => { setQ(e.target.value); setOffset(0); }}
                     placeholder={m.searchPlaceholder} aria-label={m.searchPlaceholder} className="h-8 bg-surface pl-8" />
            </div>
            <FilterSelect value={status} onChange={filtered(setStatus)} all={m.allStatuses}
                          options={STATUSES.map((value) => [value, m.statuses[value]] as [string, string])} />
            <FilterSelect value={type} onChange={filtered(setType)} all={m.allTypes}
                          options={TYPES.map((value) => [value, m.types[value]] as [string, string])} />
          </>
        }
      />
      <AddDialog open={adding} config={config} onClose={() => setAdding(false)} />
      <DetailDialog sourceId={viewing} onClose={() => setViewing(null)}
                    onAction={(action, source) => { if (action !== "view") setViewing(null); onAction(action, source); }} />
      <ExtendDialog source={extending} config={config} onClose={() => setExtending(null)} />
      <UseDialog request={using} onClose={() => setUsing(null)} />
      <AlertDialog open={Boolean(deleting)} onOpenChange={(open) => !open && !busy && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.deleteDialog.title}</AlertDialogTitle>
            <AlertDialogDescription>{deleting ? m.deleteDialog.description(deleting.name) : ""}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction disabled={busy} onClick={(event) => { event.preventDefault(); void remove(); }}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {m.deleteDialog.confirm}
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
      <MovieSourcesPage />
    </Suspense>
  );
}
