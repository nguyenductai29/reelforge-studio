"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Film, FolderKanban, Loader2, PenLine, Recycle, Share2, Sparkles, Video } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import {
  EmptyState,
  OptionChips,
  SectionLink,
  SectionTitle,
  StatusBadge,
} from "@/components/reelforge/primitives";
import { OnboardingChecklist } from "@/components/reelforge/onboarding";
import { ProjectCard } from "@/components/reelforge/project-card";
import { MiniDiagram } from "@/components/workflow/mini-diagram";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useCreateFromTemplate, useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, useBilling, useDashboard, usePublications, useRuns, useUsage } from "@/lib/queries";
import { assetKind, projectStatus } from "@/lib/studio";
import type { Project } from "@/lib/types";
import { previewKinds, type TemplateId } from "@/lib/workflow";

// Each one creates a workflow from a template the backend runs.
const quickCreate: { key: "youtube" | "tiktok" | "recap" | "review" | "repurpose"; icon: typeof Video; template: TemplateId }[] = [
  { key: "youtube", icon: Video, template: "youtube-video" },
  { key: "tiktok", icon: Share2, template: "tiktok-video" },
  { key: "recap", icon: Film, template: "movie-recap" },
  { key: "review", icon: PenLine, template: "movie-review" },
  { key: "repurpose", icon: Recycle, template: "repurpose" },
];

function greeting(t: ReturnType<typeof useI18n>["t"]) {
  const hour = new Date().getHours();
  return hour < 12 ? t.home.greeting.morning : hour < 18 ? t.home.greeting.afternoon : t.home.greeting.evening;
}

export default function HomePage() {
  const { t, formatNumber, formatRelative } = useI18n();
  useDocumentTitle(t.nav.home);
  const router = useRouter();
  const client = useQueryClient();
  const showError = useErrorToast();
  const { data } = useDashboard();
  const runs = useRuns().data ?? [];
  const publications = usePublications().data ?? [];
  const usage = useUsage().data;
  const billing = useBilling().data;
  const { create, pending } = useCreateFromTemplate();
  const [type, setType] = useState(["2"]);
  const [idea, setIdea] = useState("");
  const [creating, setCreating] = useState(false);
  const [hello, setHello] = useState("");

  useEffect(() => {
    setHello(greeting(t));
  }, [t]);

  // Payment providers and older links return to "/"; forward them to the page that handles them.
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.has("payment")) router.replace(`/billing?payment=${encodeURIComponent(query.get("payment") ?? "")}`);
    else if (query.get("channel") === "youtube") router.replace("/channels");
  }, [router]);

  const projects = data?.projects ?? [];
  const workflows = data?.workflows ?? [];
  const assets = data?.assets ?? [];
  const lastRun = useMemo(() => {
    const map = new Map<string, (typeof runs)[number]>();
    for (const run of runs) if (!map.has(run.workflow_id)) map.set(run.workflow_id, run);
    return map;
  }, [runs]);
  const recentWorkflows = [...workflows]
    .sort((a, b) => (lastRun.get(b.id)?.created_at ?? "").localeCompare(lastRun.get(a.id)?.created_at ?? ""))
    .slice(0, 3);
  const videosOf = (project: Project) =>
    assets.filter((a) => a.project_id === project.id && assetKind(a.content_type) === "video").length;
  const plan = billing?.plans.find((p) => p.code === billing.subscription.plan_code);
  const balance = usage?.balance ?? 0;
  const monthly = plan?.monthly_credits ?? 0;
  const projectTitle = (id: string) => projects.find((p) => p.id === id)?.title ?? t.editor.runs.unknownProject;

  async function createFromIdea() {
    const text = idea.trim();
    if (!text) {
      toast.error(t.home.ideaRequired);
      return;
    }
    const format = t.home.contentTypes[Number(type[0])] ?? "";
    const firstLine = text.split("\n")[0]!.trim();
    const short = firstLine.length > 100 ? `${firstLine.slice(0, 99)}…` : firstLine;
    setCreating(true);
    try {
      const project = await api<Project>(
        "projects",
        jsonRequest("POST", { title: `${format}: ${short}`.slice(0, 150), topic: text.slice(0, 3000) }),
      );
      await client.invalidateQueries({ queryKey: keys.dashboard });
      toast.success(t.home.projectCreated);
      router.push(`/projects/${project.id}`);
    } catch (error) {
      showError(error);
      setCreating(false);
    }
  }

  return (
    <div className="space-y-10">
      <OnboardingChecklist />
      <section className="stage-glow panel relative overflow-hidden p-6 sm:p-8">
        <p className="min-h-5 text-sm text-muted-foreground">{hello}</p>
        <h1 className="mt-1 text-2xl font-semibold sm:text-4xl">{t.home.title}</h1>

        <div className="mt-6 rounded-xl border border-border bg-surface-2 p-3">
          <Textarea
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void createFromIdea();
            }}
            rows={3}
            maxLength={3000}
            placeholder={t.home.ideaPlaceholder}
            aria-label={t.home.title}
            className="resize-none border-0 bg-transparent text-base shadow-none focus-visible:ring-0"
          />
          <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3 lg:flex-row lg:items-center lg:justify-between">
            <OptionChips
              options={t.home.contentTypes.map((label, index) => ({ value: String(index), label }))}
              value={type}
              onChange={setType}
            />
            <Button className="shrink-0" onClick={() => void createFromIdea()} disabled={creating}>
              {creating ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
              {t.home.createWithAi}
            </Button>
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>{t.home.quickCreate}</SectionTitle>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {quickCreate.map((item) => {
            const body = (
              <>
                <span className="flex size-9 items-center justify-center rounded-lg border border-border bg-surface-2 group-hover:border-primary/40">
                  {pending === item.template ? (
                    <Loader2 className="size-4 animate-spin text-primary" />
                  ) : (
                    <item.icon className="size-4 text-primary" />
                  )}
                </span>
                <span className="text-sm font-medium leading-snug">{t.home.quick[item.key]}</span>
              </>
            );
            const className =
              "panel group flex flex-col items-start gap-3 p-4 text-left transition-colors hover:border-border-strong";
            return (
              <button
                key={item.key}
                type="button"
                className={className}
                disabled={Boolean(pending)}
                onClick={() => void create(item.template)}
              >
                {body}
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <SectionTitle
          action={
            <SectionLink href="/workflows">
              {t.home.allWorkflows} <ArrowRight className="size-3.5" />
            </SectionLink>
          }
        >
          {t.home.recentWorkflows}
        </SectionTitle>
        {recentWorkflows.length === 0 ? (
          <div className="panel p-5 text-sm text-muted-foreground">{t.home.noWorkflows}</div>
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {recentWorkflows.map((workflow) => {
              const run = lastRun.get(workflow.id);
              return (
                <Link
                  key={workflow.id}
                  href={`/workflows/${workflow.id}`}
                  className="panel space-y-3 p-4 transition-colors hover:border-border-strong"
                >
                  <MiniDiagram kinds={previewKinds(workflow.graph)} />
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{workflow.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {t.common.steps(workflow.graph.nodes.length)}
                        {run ? ` · ${projectTitle(run.project_id)}` : ""}
                      </p>
                    </div>
                    {run ? (
                      <StatusBadge status={run.status} label={t.status.run[run.status]} />
                    ) : (
                      <StatusBadge status="draft" label={t.workflows.neverRun} />
                    )}
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </section>

      <div className="grid gap-8 xl:grid-cols-[1fr_300px]">
        <div className="min-w-0 space-y-8">
          <section>
            <SectionTitle
              action={
                <SectionLink href="/projects">
                  {t.home.allProjects} <ArrowRight className="size-3.5" />
                </SectionLink>
              }
            >
              {t.home.recentProjects}
            </SectionTitle>
            {projects.length === 0 ? (
              <EmptyState icon={FolderKanban} title={t.home.noProjects} description={t.home.noProjectsHint} />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {projects.slice(0, 6).map((project) => (
                  <ProjectCard
                    key={project.id}
                    project={project}
                    status={projectStatus(project, runs, publications)}
                    videos={videosOf(project)}
                  />
                ))}
              </div>
            )}
          </section>

          <section>
            <SectionTitle>{t.home.activeGenerations}</SectionTitle>
            <div className="panel divide-y divide-border">
              {runs.length === 0 && <p className="p-4 text-sm text-muted-foreground">{t.home.noGenerations}</p>}
              {runs.slice(0, 5).map((run) => (
                <Link
                  key={run.id}
                  href={`/workflows/${run.workflow_id}?run=${run.id}`}
                  className="flex flex-wrap items-center gap-3 p-4 transition-colors hover:bg-surface-2/40"
                >
                  <span className="flex size-9 items-center justify-center rounded-lg bg-surface-2">
                    <Video className="size-4 text-muted-foreground" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{projectTitle(run.project_id)}</p>
                    <p className="text-xs text-muted-foreground">
                      {workflows.find((w) => w.id === run.workflow_id)?.name ?? ""} · {formatRelative(run.created_at)}
                    </p>
                  </div>
                  <StatusBadge status={run.status} label={t.status.run[run.status]} />
                </Link>
              ))}
            </div>
          </section>
        </div>

        <aside className="space-y-4">
          <div className="panel p-5">
            <p className="text-sm text-muted-foreground">{t.shell.credits}</p>
            <p className="mt-1 text-3xl font-semibold">{formatNumber(balance)}</p>
            <p className="text-xs text-muted-foreground">
              {monthly > 0 ? t.home.creditsOf(formatNumber(monthly)) : t.home.creditsBalance}
            </p>
            <Progress
              value={monthly > 0 ? Math.min(100, (balance / monthly) * 100) : balance > 0 ? 100 : 0}
              className="mt-4 h-2"
              aria-label={t.shell.credits}
            />
            <Button asChild variant="outline" size="sm" className="mt-4 w-full">
              <Link href="/billing">{t.home.managePlan}</Link>
            </Button>
          </div>
          <div className="panel p-5">
            <p className="text-sm font-medium">{t.home.publishingNext}</p>
            <div className="mt-3 space-y-3 text-sm">
              {publications.length === 0 && <p className="text-xs text-muted-foreground">{t.home.noPublishing}</p>}
              {publications.slice(0, 3).map((publication) => (
                <div key={publication.id} className="flex items-center justify-between gap-3">
                  <span className="truncate text-muted-foreground">{publication.title}</span>
                  <span className="shrink-0 text-xs">{formatRelative(publication.finished_at ?? publication.created_at)}</span>
                </div>
              ))}
            </div>
            <Button asChild variant="outline" size="sm" className="mt-4 w-full">
              <Link href="/calendar">{t.home.openCalendar}</Link>
            </Button>
          </div>
        </aside>
      </div>
    </div>
  );
}
