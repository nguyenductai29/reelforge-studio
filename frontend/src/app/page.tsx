"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { OptionChips } from "@/components/reelforge/primitives";
import { OnboardingChecklist } from "@/components/reelforge/onboarding";
import { QueryError } from "@/components/reelforge/query-state";
import {
  AttentionPanel,
  HomeSection,
  OverviewStats,
  PublishingPanel,
  QuickCreate,
  RecentProjects,
  RecentRuns,
  RecentWorkflows,
  UsagePanel,
  ViewAll,
} from "@/components/reelforge/home-dashboard";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { can } from "@/lib/permissions";
import { keys, useDashboard, useHome } from "@/lib/queries";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/utils";

function greeting(t: ReturnType<typeof useI18n>["t"]) {
  const hour = new Date().getHours();
  return hour < 12 ? t.home.greeting.morning : hour < 18 ? t.home.greeting.afternoon : t.home.greeting.evening;
}

/** The first thing on Home: describe an idea, pick a format, create a project from it. Viewers read instead. */
function CreateHero({ canCreate, studio }: { canCreate: boolean; studio?: string }) {
  const { t } = useI18n();
  const router = useRouter();
  const client = useQueryClient();
  const showError = useErrorToast();
  const [type, setType] = useState(["2"]);
  const [idea, setIdea] = useState("");
  const [creating, setCreating] = useState(false);
  const [hello, setHello] = useState("");

  useEffect(() => {
    setHello(greeting(t));
  }, [t]);

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
      await Promise.all([client.invalidateQueries({ queryKey: keys.dashboard }),
                         client.invalidateQueries({ queryKey: keys.home })]);
      toast.success(t.home.projectCreated);
      router.push(`/projects/${project.id}`);
    } catch (error) {
      showError(error);
      setCreating(false);
    }
  }

  return (
    <section aria-labelledby="home-title" className="stage-glow panel relative overflow-hidden p-5 sm:p-6">
      <p className="min-h-5 truncate text-sm text-muted-foreground">
        {hello}
        {studio && hello ? ` · ${studio}` : ""}
      </p>
      <h1 id="home-title" className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">
        {canCreate ? t.home.title : (studio ?? t.nav.home)}
      </h1>
      {canCreate ? (
        <div className="mt-4 rounded-xl border border-border bg-surface-2 p-2.5 transition-colors focus-within:border-primary/50">
          <Textarea
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void createFromIdea();
            }}
            rows={2}
            maxLength={3000}
            placeholder={t.home.ideaPlaceholder}
            aria-label={t.home.title}
            // Three lines on a phone, where the example idea wraps; two from a tablet up.
            className="min-h-[5.5rem] resize-none border-0 bg-transparent px-2 text-base shadow-none focus-visible:ring-0 sm:min-h-16"
          />
          <div className="mt-2 flex flex-col gap-2.5 border-t border-border px-1 pt-2.5 lg:flex-row lg:items-center lg:justify-between">
            <div role="group" aria-label={t.home.contentType} className="min-w-0">
              <OptionChips
                options={t.home.contentTypes.map((label, index) => ({ value: String(index), label }))}
                value={type}
                onChange={setType}
              />
            </div>
            <Button className="shrink-0 self-end lg:self-auto" onClick={() => void createFromIdea()} disabled={creating}>
              {creating ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
              {t.home.createWithAi}
            </Button>
          </div>
        </div>
      ) : (
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{t.home.readOnly}</p>
      )}
    </section>
  );
}

/**
 * Home, the user dashboard. In order of importance: create with AI; the studio's overview and what waits for the
 * member; quick templates and recent workflows; recent projects, AI usage and publishing; recent runs. Everything
 * but the hero comes from one request (GET /api/home), shown as the member's role allows.
 */
export default function HomePage() {
  const { t } = useI18n();
  useDocumentTitle(t.nav.home);
  const router = useRouter();
  const { data: dashboard } = useDashboard();
  const home = useHome();
  const data = home.data;
  const canCreate = can(dashboard, "content.edit");
  const canConnect = can(dashboard, "channels.manage");
  const period = data ? (
    <span className="ml-1.5 text-xs font-normal text-muted-foreground">· {t.home.period(data.period_days)}</span>
  ) : null;

  // Payment providers and older links return to "/"; forward them to the page that handles them.
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.has("payment")) router.replace(`/billing?payment=${encodeURIComponent(query.get("payment") ?? "")}`);
    else if (query.get("channel") === "youtube") router.replace("/channels");
  }, [router]);

  return (
    <div className="space-y-6">
      <CreateHero canCreate={canCreate} studio={dashboard?.workspace.name} />

      {home.isError && !data ? (
        <QueryError error={home.error} onRetry={() => void home.refetch()} />
      ) : (
        <section aria-labelledby="home-overview">
          <h2 id="home-overview" className="sr-only">{t.home.overview}</h2>
          <OverviewStats data={data} />
        </section>
      )}

      <div className={cn("grid gap-6", canCreate && "lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]")}>
        {!(home.isError && !data) && (
          <HomeSection id="home-attention" title={t.home.attention.title}>
            <AttentionPanel data={data} />
          </HomeSection>
        )}
        {canCreate && (
          <HomeSection id="home-quick" title={t.home.quickCreate}
                       action={<ViewAll href="/create">{t.home.allTemplates}</ViewAll>}>
            <QuickCreate />
          </HomeSection>
        )}
      </div>

      {/* A new studio's first steps, after what waits; every step is one a viewer cannot take. */}
      {canCreate && <OnboardingChecklist />}

      {!(home.isError && !data) && (
        <>
          <HomeSection id="home-workflows" title={t.home.recentWorkflows}
                       action={<ViewAll href="/workflows">{t.home.allWorkflows}</ViewAll>}>
            <RecentWorkflows data={data} workflows={dashboard?.workflows ?? []} canCreate={canCreate} />
          </HomeSection>

          {/* Recent projects beside AI usage and publishing. From 1536 px the projects take three columns and the runs
              move up under them, beside the same side column, instead of making the page longer. */}
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem] 2xl:grid-cols-[minmax(0,1fr)_24rem]">
            <HomeSection id="home-projects" title={t.home.recentProjects}
                         action={<ViewAll href="/projects">{t.home.allProjects}</ViewAll>}>
              <RecentProjects data={data} canCreate={canCreate} />
            </HomeSection>
            <div className="grid content-start gap-6 md:grid-cols-2 xl:grid-cols-1 2xl:row-span-2">
              <HomeSection id="home-usage" title={<>{t.home.usage.title}{period}</>}
                           action={<ViewAll href="/billing">{t.home.usage.billing}</ViewAll>}>
                <UsagePanel data={data} />
              </HomeSection>
              <HomeSection id="home-publishing" title={t.home.publishing.title}
                           action={<ViewAll href="/publishing">{t.home.publishing.all}</ViewAll>}>
                <PublishingPanel data={data} canConnect={canConnect} />
              </HomeSection>
            </div>
            <HomeSection id="home-runs" title={t.home.recentRuns} className="xl:col-span-2 2xl:col-span-1"
                         action={<ViewAll href="/workflows">{t.home.history}</ViewAll>}>
              <RecentRuns data={data} />
            </HomeSection>
          </div>
        </>
      )}
    </div>
  );
}
