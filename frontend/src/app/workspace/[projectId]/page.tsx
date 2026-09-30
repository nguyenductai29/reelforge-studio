"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Eye, FolderKanban, Loader2, Play, Save, Send, Sparkles, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  EmptyState,
  FieldLabel,
  OptionChips,
  SoonBadge,
  StatusBadge,
} from "@/components/reelforge/primitives";
import { MediaThumb } from "@/components/reelforge/media-preview";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, useDashboard, usePublications, useRuns } from "@/lib/queries";
import { assetKind, isActiveRun, projectStatus, tintFor } from "@/lib/studio";
import { cn } from "@/lib/utils";

function DisabledChips({ label, options, initial }: { label: string; options: string[]; initial: number[] }) {
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <OptionChips options={options} value={initial.map((i) => options[i] ?? "")} onChange={() => undefined} disabled />
    </div>
  );
}

export default function WorkspacePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { t, formatRelative } = useI18n();
  const client = useQueryClient();
  const showError = useErrorToast();
  const { data } = useDashboard();
  const runs = useRuns().data ?? [];
  const publications = usePublications().data ?? [];
  const project = data?.projects.find((p) => p.id === projectId);
  useDocumentTitle(project?.title ?? t.projects.title);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [tab, setTab] = useState("script");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (project) {
      setTitle(project.title);
      setTopic(project.topic);
    }
    // Only reset the form when a different project opens, not on every refetch.
  }, [project?.id]);

  if (!data || !project) {
    return <EmptyState icon={FolderKanban} title={t.project.notFound} description={t.project.notFoundHint} />;
  }

  const status = projectStatus(project, runs, publications);
  const dirty = title !== project.title || topic !== project.topic;
  const words = topic.trim() ? topic.trim().split(/\s+/).length : 0;
  const media = data.assets.filter((asset) => asset.project_id === project.id);
  const latestVideo = media.find((asset) => assetKind(asset.content_type) === "video");
  const lastRun = runs.find((run) => run.project_id === project.id);
  const workflowId = lastRun?.workflow_id ?? data.workflows[0]?.id;
  const workflowHref = workflowId ? `/workflows/${workflowId}?project=${project.id}` : "/workflows";
  const c = t.workspace.config;

  async function save() {
    setSaving(true);
    try {
      await api(`projects/${encodeURIComponent(projectId)}`, jsonRequest("PATCH", { title: title.trim(), topic }));
      await client.invalidateQueries({ queryKey: keys.dashboard });
      toast.success(t.workspace.saved);
    } catch (error) {
      showError(error);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <Link
        href={`/projects/${project.id}`}
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> {project.title}
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-[200px] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold break-words">{project.title}</h1>
            <StatusBadge status={status} label={t.status.project[status]} />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => void save()} disabled={!dirty || saving || !title.trim()}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />} {t.workspace.save}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setTab("preview")}>
            <Eye className="size-4" /> {t.workspace.preview}
          </Button>
          <Button asChild size="sm">
            <Link href={workflowHref}>
              <Sparkles className="size-4" /> {t.workspace.generate}
            </Link>
          </Button>
          <Button asChild variant="secondary" size="sm">
            <Link href="/publishing">
              <Send className="size-4" /> {t.workspace.publish}
            </Link>
          </Button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)_300px]">
        <aside className="panel scrollbar-thin space-y-5 p-4 xl:sticky xl:top-20 xl:max-h-[calc(100vh-11rem)] xl:overflow-y-auto">
          <div className="flex items-start justify-between gap-2 rounded-lg bg-surface-2 p-2.5 text-xs text-muted-foreground">
            <span>{c.soon}</span>
            <SoonBadge />
          </div>
          <DisabledChips label={c.goal} options={c.goals} initial={[0]} />
          <DisabledChips label={c.platform} options={c.platforms} initial={[0, 1]} />
          <DisabledChips label={c.format} options={c.formats} initial={[1]} />
          <DisabledChips label={c.language} options={c.languages} initial={[1]} />
          <DisabledChips label={c.tone} options={c.tones} initial={[3]} />
          <DisabledChips label={c.duration} options={c.durations} initial={[2]} />
          <div>
            <FieldLabel>{c.audience}</FieldLabel>
            <Input disabled placeholder={c.audiencePlaceholder} className="bg-surface-2" />
          </div>
        </aside>

        <section className="min-w-0">
          <Tabs value={tab} onValueChange={setTab}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <TabsList>
                {(["script", "storyboard", "media", "preview"] as const).map((key) => (
                  <TabsTrigger key={key} value={key}>
                    {t.workspace.tabs[key]}
                  </TabsTrigger>
                ))}
              </TabsList>
              <div className="flex gap-4 text-xs text-muted-foreground">
                <span>{t.workspace.words(words)}</span>
                <span>{t.workspace.characters(topic.length)}</span>
              </div>
            </div>

            <TabsContent value="script" className="mt-4 space-y-3">
              <div className="panel p-4">
                <FieldLabel htmlFor="ws-title">{t.workspace.titleLabel}</FieldLabel>
                <Input
                  id="ws-title"
                  value={title}
                  maxLength={150}
                  onChange={(e) => setTitle(e.target.value)}
                  className="bg-surface-2 text-base"
                />
              </div>
              <div className="panel border-primary/40 p-4">
                <div className="flex items-center justify-between">
                  <FieldLabel htmlFor="ws-topic">{t.workspace.topicLabel}</FieldLabel>
                </div>
                <Textarea
                  id="ws-topic"
                  value={topic}
                  maxLength={3000}
                  rows={8}
                  onChange={(e) => setTopic(e.target.value)}
                  className="resize-y border-0 bg-transparent px-0 text-[15px] leading-relaxed shadow-none focus-visible:ring-0"
                />
                <p className="text-xs text-muted-foreground">{t.workspace.topicHelp}</p>
                <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-border pt-3">
                  {t.workspace.aiActions.map((action) => (
                    <button
                      key={action}
                      type="button"
                      disabled
                      className="inline-flex items-center gap-1 rounded-md border border-border bg-surface-2 px-2 py-1 text-[11px] text-muted-foreground disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <Wand2 className="size-3" />
                      {action}
                    </button>
                  ))}
                  <SoonBadge />
                </div>
              </div>
            </TabsContent>

            <TabsContent value="storyboard" className="mt-4 space-y-3">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                {t.workspace.storyboardSoon} <SoonBadge />
              </div>
              <div className="grid gap-3 opacity-60 sm:grid-cols-3">
                {[1, 2, 3].map((n) => (
                  <div key={n} className="panel overflow-hidden">
                    <div
                      className={cn(
                        "flex aspect-video items-center justify-center bg-gradient-to-br text-xs text-muted-foreground",
                        n % 2
                          ? "from-[oklch(0.3_0.05_250)] to-[oklch(0.19_0.02_270)]"
                          : "from-[oklch(0.33_0.09_52)] to-[oklch(0.2_0.03_40)]",
                      )}
                    >
                      {t.workspace.scene(n)}
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="media" className="mt-4">
              {media.length === 0 ? (
                <div className="panel p-10 text-center text-sm text-muted-foreground">{t.workspace.mediaEmpty}</div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-3">
                  {media.map((asset) => (
                    <div key={asset.id} className="panel overflow-hidden">
                      <div className="aspect-video bg-black">
                        <MediaThumb asset={asset} />
                      </div>
                      <p className="truncate p-3 text-xs">{asset.filename}</p>
                    </div>
                  ))}
                </div>
              )}
            </TabsContent>

            <TabsContent value="preview" className="mt-4">
              {latestVideo ? (
                <div className="panel mx-auto aspect-[9/16] max-w-xs overflow-hidden bg-black">
                  <MediaThumb asset={latestVideo} controls />
                </div>
              ) : (
                <div
                  className={cn(
                    "panel mx-auto flex aspect-[9/16] max-w-xs flex-col items-center justify-center gap-3 bg-gradient-to-br p-4 text-center",
                    tintFor(project.id),
                  )}
                >
                  <span className="flex size-12 items-center justify-center rounded-full bg-background/70 backdrop-blur">
                    <Play className="size-5" />
                  </span>
                  <p className="text-xs text-foreground/80">{t.workspace.previewEmpty}</p>
                </div>
              )}
            </TabsContent>
          </Tabs>
        </section>

        <aside className="space-y-4 xl:sticky xl:top-20 xl:self-start">
          <div className="panel p-4">
            <p className="flex items-center justify-between gap-2 text-sm font-medium">
              {t.workspace.assistantTitle} <SoonBadge />
            </p>
            <p className="mt-1 text-xs text-muted-foreground">{t.workspace.assistantHint}</p>
            <div className="mt-3 space-y-1.5">
              {t.workspace.assistantSuggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled
                  className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-left text-xs disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
          <div className="panel p-4">
            <p className="text-sm font-medium">{t.workspace.generationTitle}</p>
            {lastRun ? (
              <>
                <div className="mt-3 flex items-center justify-between gap-2 text-xs">
                  <span className="truncate text-muted-foreground">
                    {data.workflows.find((w) => w.id === lastRun.workflow_id)?.name}
                  </span>
                  <StatusBadge status={lastRun.status} label={t.status.run[lastRun.status]} />
                </div>
                {isActiveRun(lastRun) && (
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-primary/20">
                    <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
                  </div>
                )}
                <p className="mt-2 text-[11px] text-muted-foreground">{formatRelative(lastRun.created_at)}</p>
              </>
            ) : (
              <p className="mt-2 text-xs text-muted-foreground">{t.workspace.noGeneration}</p>
            )}
          </div>
          <div className="panel p-4 text-xs text-muted-foreground">
            <p className="text-sm font-medium text-foreground">{t.workspace.costTitle}</p>
            <p className="mt-2">{t.workspace.costVideo}</p>
          </div>
        </aside>
      </div>
    </div>
  );
}
