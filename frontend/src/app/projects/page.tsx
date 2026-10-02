"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { FolderKanban, Grid2x2, List, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState, FilterPills, PageHeader } from "@/components/reelforge/primitives";
import { NewProjectDialog } from "@/components/reelforge/new-project-dialog";
import { ProjectCard } from "@/components/reelforge/project-card";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { can } from "@/lib/permissions";
import { useDashboard, usePublications, useRuns } from "@/lib/queries";
import { assetKind, projectStatus, type ProjectStatus } from "@/lib/studio";
import { cn } from "@/lib/utils";

const filters: ("all" | ProjectStatus)[] = ["all", "draft", "generating", "review", "ready", "published"];

export default function ProjectsPage() {
  const { t } = useI18n();
  useDocumentTitle(t.projects.title);
  const router = useRouter();
  const { data } = useDashboard();
  const runs = useRuns().data ?? [];
  const publications = usePublications().data ?? [];
  const [filter, setFilter] = useState<"all" | ProjectStatus>("all");
  const [query, setQuery] = useState("");
  const [layout, setLayout] = useState<"grid" | "list">("grid");

  const projects = (data?.projects ?? []).map((project) => ({
    project,
    status: projectStatus(project, runs, publications),
    videos: (data?.assets ?? []).filter((a) => a.project_id === project.id && assetKind(a.content_type) === "video").length,
  }));
  const q = query.trim().toLocaleLowerCase();
  const visible = projects.filter(
    ({ project, status }) =>
      (filter === "all" || status === filter) &&
      (project.title.toLocaleLowerCase().includes(q) || project.topic.toLocaleLowerCase().includes(q)),
  );
  const limit = data?.limits.projects ?? null;

  const newProject = !can(data, "content.edit") ? null : (
    <NewProjectDialog
      onCreated={(project) => router.push(`/projects/${project.id}`)}
      trigger={
        <Button>
          <Plus className="size-4" />
          {t.projects.newProject}
        </Button>
      }
    />
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.projects.title}
        subtitle={
          limit !== null ? `${t.projects.subtitle} ${t.projects.limit(projects.length, limit)}.` : t.projects.subtitle
        }
        actions={newProject}
      />

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t.projects.searchPlaceholder}
            aria-label={t.projects.searchPlaceholder}
            className="h-9 bg-surface pl-9"
          />
        </div>
        <FilterPills
          value={filter}
          onChange={setFilter}
          options={filters.map((f) => ({ value: f, label: f === "all" ? t.common.all : t.status.project[f] }))}
        />
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

      {projects.length === 0 ? (
        <EmptyState
          icon={FolderKanban}
          title={t.projects.emptyTitle}
          description={t.projects.emptyDescription}
          action={newProject}
        />
      ) : visible.length === 0 ? (
        <EmptyState icon={Search} title={t.projects.noMatchTitle} description={t.projects.noMatchDescription} />
      ) : layout === "grid" ? (
        // Three cards a row on a laptop (a title and its status side by side), four from 1536 px, five from 1800.
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 3xl:grid-cols-5">
          {visible.map((item) => (
            <ProjectCard key={item.project.id} {...item} />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {visible.map((item) => (
            <ProjectCard key={item.project.id} {...item} layout="list" />
          ))}
        </div>
      )}
    </div>
  );
}
