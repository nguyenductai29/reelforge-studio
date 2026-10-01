"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Download, FolderKanban, Play, Workflow as WorkflowIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, PlatformBadge, StatusBadge } from "@/components/reelforge/primitives";
import { MediaThumb, assetKindIcon } from "@/components/reelforge/media-preview";
import { MiniDiagram } from "@/components/workflow/mini-diagram";
import { assetUrl } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useDashboard, usePublications, useRun, useRuns, useScripts } from "@/lib/queries";
import { assetKind, formatBytes, projectStatus, tintFor } from "@/lib/studio";
import { cn } from "@/lib/utils";
import { previewKinds } from "@/lib/workflow";

const tabs = ["overview", "script", "media", "generations", "publishing", "history"] as const;

export default function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { t, formatDate, formatDateTime, formatRelative } = useI18n();
  const { data } = useDashboard();
  const allRuns = useRuns().data ?? [];
  const allPublications = usePublications().data ?? [];
  const project = data?.projects.find((p) => p.id === projectId);
  useDocumentTitle(project?.title ?? t.projects.title);

  const runs = allRuns.filter((run) => run.project_id === projectId);
  const lastRun = runs[0] ?? null;
  const lastRunDetail = useRun(lastRun?.id ?? null).data;
  const latestScript = useScripts(0, projectId, 1).data?.items[0] ?? null;

  if (!data || !project) {
    return (
      <div className="space-y-6">
        <Link href="/projects" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-3.5" /> {t.project.back}
        </Link>
        <EmptyState icon={FolderKanban} title={t.project.notFound} description={t.project.notFoundHint} />
      </div>
    );
  }

  const status = projectStatus(project, allRuns, allPublications);
  const media = data.assets.filter((asset) => asset.project_id === project.id);
  const videos = media.filter((asset) => assetKind(asset.content_type) === "video");
  const latestVideo = videos[0] ?? null;
  const runIds = new Set(runs.map((run) => run.id));
  const publications = allPublications.filter((p) => runIds.has(p.run_id));
  const workflowName = (id: string) => data.workflows.find((w) => w.id === id)?.name ?? t.editor.subtitle;
  const lastWorkflow = lastRun ? data.workflows.find((w) => w.id === lastRun.workflow_id) : undefined;
  const openWorkflowId = lastWorkflow?.id ?? data.workflows[0]?.id;
  const openWorkflowHref = openWorkflowId ? `/workflows/${openWorkflowId}?project=${project.id}` : "/workflows";
  const steps = lastRunDetail?.steps ?? [];
  const history = [
    ...(project.created_at ? [{ at: project.created_at, text: t.project.history.created }] : []),
    ...runs.map((run) => ({ at: run.created_at, text: t.project.history.run(workflowName(run.workflow_id), t.status.run[run.status]) })),
    ...publications.map((p) => ({
      at: p.finished_at ?? p.created_at,
      text: t.project.history.published(p.title, t.status.publication[p.state]),
    })),
  ].sort((a, b) => b.at.localeCompare(a.at));

  return (
    <div className="space-y-6">
      <Link href="/projects" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> {t.project.back}
      </Link>

      <div className="panel flex flex-wrap items-center gap-4 p-5">
        <div className={cn("h-16 w-28 shrink-0 rounded-lg bg-gradient-to-br", tintFor(project.id))} />
        <div className="min-w-[220px] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold break-words sm:text-2xl">{project.title}</h1>
            <StatusBadge status={status} label={t.status.project[status]} />
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {t.projects.videos(videos.length)}
            {project.created_at ? ` · ${t.project.created(formatRelative(project.created_at))}` : ""}
          </p>
          {publications.length > 0 && (
            <div className="mt-2 flex gap-1.5">
              {[...new Set(publications.map((p) => p.channel))].map((channel) => (
                <PlatformBadge key={channel} platform={channel} withLabel />
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline">
            <Link href={`/workspace/${project.id}`}>{t.project.editScript}</Link>
          </Button>
          <Button asChild size="lg">
            <Link href={openWorkflowHref}>
              <WorkflowIcon className="size-4" />
              {t.project.openWorkflow}
            </Link>
          </Button>
        </div>
      </div>

      {lastRun && lastWorkflow ? (
        <Link
          href={`/workflows/${lastWorkflow.id}?run=${lastRun.id}`}
          className="panel grid gap-4 p-4 transition-colors hover:border-border-strong sm:grid-cols-[1fr_auto] sm:items-center"
        >
          <MiniDiagram kinds={previewKinds(lastWorkflow.graph)} />
          <div className="space-y-1 text-sm sm:w-56">
            <p className="text-xs uppercase tracking-wider text-muted-foreground">{t.project.workflowLabel}</p>
            <p className="font-medium">{lastWorkflow.name}</p>
            <p className="text-muted-foreground">
              {t.project.workflowStatus(
                t.status.run[lastRun.status],
                steps.filter((s) => s.status === "completed").length,
                steps.length || lastWorkflow.graph.nodes.length,
              )}
            </p>
          </div>
        </Link>
      ) : (
        <div className="panel flex flex-wrap items-center justify-between gap-3 p-4 text-sm text-muted-foreground">
          {t.project.noWorkflowRun}
          <Button asChild variant="outline" size="sm">
            <Link href={openWorkflowHref}>{t.project.openWorkflow}</Link>
          </Button>
        </div>
      )}

      <Tabs defaultValue="overview">
        <TabsList className="h-auto flex-wrap">
          {tabs.map((tab) => (
            <TabsTrigger key={tab} value={tab}>
              {t.project.tabs[tab]}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="overview" className="mt-5">
          <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
            <div className="space-y-4">
              <div className="panel p-5">
                <p className="text-sm font-medium">{t.project.topic}</p>
                <p className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">
                  {project.topic.trim() || t.project.noTopic}
                </p>
              </div>
              <div className="panel p-5">
                <p className="text-sm font-medium">{t.project.latestScript}</p>
                <p className="mt-2 line-clamp-6 whitespace-pre-wrap text-sm text-muted-foreground">
                  {latestScript?.text ?? t.project.noScript}
                </p>
              </div>
            </div>
            <div className="space-y-4">
              <div className="panel overflow-hidden">
                {latestVideo ? (
                  <div className="aspect-video bg-black">
                    <MediaThumb asset={latestVideo} controls />
                  </div>
                ) : (
                  <div className={cn("flex aspect-video items-center justify-center bg-gradient-to-br", tintFor(project.id))}>
                    <span className="flex size-12 items-center justify-center rounded-full bg-background/70 backdrop-blur">
                      <Play className="size-5" />
                    </span>
                  </div>
                )}
                <div className="p-4">
                  <p className="text-sm font-medium">{t.project.latestVideo}</p>
                  <p className="text-xs text-muted-foreground">
                    {latestVideo
                      ? t.project.videoMeta(latestVideo.created_at ? formatRelative(latestVideo.created_at) : "")
                      : t.project.noVideo}
                  </p>
                </div>
              </div>
              <div className="panel p-5">
                <p className="text-sm font-medium">{t.project.attachedMedia}</p>
                {media.length === 0 ? (
                  <p className="mt-3 text-sm text-muted-foreground">{t.project.noMedia}</p>
                ) : (
                  <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
                    {media.slice(0, 6).map((asset) => {
                      const Icon = assetKindIcon[assetKind(asset.content_type)];
                      return (
                        <li key={asset.id} className="flex items-center gap-2">
                          <Icon className="size-3.5 shrink-0" />
                          <a href={assetUrl(asset.id)} className="min-w-0 truncate hover:text-foreground">
                            {asset.filename}
                          </a>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              <div className="panel p-5">
                <p className="text-sm font-medium">{t.project.publishingStatus}</p>
                <div className="mt-3 space-y-2">
                  {publications.length === 0 && <p className="text-sm text-muted-foreground">{t.project.noPublications}</p>}
                  {publications.map((publication) => (
                    <div key={publication.id} className="flex items-center justify-between gap-3">
                      <PlatformBadge platform={publication.channel} withLabel />
                      <StatusBadge status={publication.state} label={t.status.publication[publication.state]} />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="script" className="mt-5 space-y-4">
          <div className="panel p-5">
            <p className="text-xs uppercase tracking-wider text-muted-foreground">{t.project.latestScript}</p>
            {latestScript ? (
              <>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {t.nodes[latestScript.node_type]?.name ?? latestScript.node_type} · {formatDateTime(latestScript.created_at)}
                  {" · "}
                  {t.workspace.words(latestScript.words)}
                </p>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed">{latestScript.text}</p>
              </>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">{t.project.noScript}</p>
            )}
          </div>
          <div className="panel p-5">
            <p className="text-xs uppercase tracking-wider text-muted-foreground">{t.project.topic}</p>
            <p className="mt-2 whitespace-pre-wrap text-sm">{project.topic.trim() || t.project.noTopic}</p>
          </div>
        </TabsContent>

        <TabsContent value="media" className="mt-5">
          {media.length === 0 ? (
            <div className="panel p-5 text-sm text-muted-foreground">{t.project.noMedia}</div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-3">
              {media.map((asset) => (
                <div key={asset.id} className="panel overflow-hidden">
                  <div className="aspect-video bg-black">
                    <MediaThumb asset={asset} controls={assetKind(asset.content_type) !== "image"} />
                  </div>
                  <div className="flex items-center justify-between gap-2 p-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm">{asset.filename}</p>
                      <p className="text-xs text-muted-foreground">{formatBytes(asset.bytes)}</p>
                    </div>
                    <Button asChild variant="ghost" size="icon" className="size-8 shrink-0">
                      <a href={assetUrl(asset.id)} aria-label={t.common.download}>
                        <Download className="size-4" />
                      </a>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="generations" className="mt-5">
          <div className="panel divide-y divide-border">
            {runs.length === 0 && <p className="p-4 text-sm text-muted-foreground">{t.project.generationsEmpty}</p>}
            {runs.map((run) => (
              <Link
                key={run.id}
                href={`/workflows/${run.workflow_id}?run=${run.id}`}
                className="flex items-center gap-3 p-4 transition-colors hover:bg-surface-2/40"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{workflowName(run.workflow_id)}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatDateTime(run.created_at)}
                    {run.retry_of_id ? ` · ${t.editor.runs.retryOf}` : ""}
                  </p>
                </div>
                <StatusBadge status={run.status} label={t.status.run[run.status]} />
              </Link>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="publishing" className="mt-5 space-y-4">
          <div className="panel divide-y divide-border">
            {publications.length === 0 && <p className="p-4 text-sm text-muted-foreground">{t.project.noPublications}</p>}
            {publications.map((publication) => (
              <div key={publication.id} className="flex flex-wrap items-center gap-3 p-4 text-sm">
                <PlatformBadge platform={publication.channel} />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{publication.title}</p>
                  <p className="text-xs text-muted-foreground">{formatDate(publication.created_at)}</p>
                </div>
                <StatusBadge status={publication.state} label={t.status.publication[publication.state]} />
              </div>
            ))}
          </div>
          <div className="panel flex flex-wrap items-center justify-between gap-3 p-4 text-sm text-muted-foreground">
            {t.project.publishHint}
            <Button asChild variant="outline" size="sm">
              <Link href="/publishing">{t.project.goPublish}</Link>
            </Button>
          </div>
        </TabsContent>

        <TabsContent value="history" className="mt-5">
          <div className="panel divide-y divide-border text-sm">
            {history.length === 0 && <p className="p-4 text-muted-foreground">{t.project.historyEmpty}</p>}
            {history.map((event, index) => (
              <div key={`${event.at}-${index}`} className="flex items-center justify-between gap-4 p-4">
                <span className="min-w-0 break-words">{event.text}</span>
                <span className="shrink-0 text-xs text-muted-foreground">{formatRelative(event.at)}</span>
              </div>
            ))}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
