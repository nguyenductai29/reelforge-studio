"use client";

import Link from "next/link";
import { Film } from "lucide-react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { tintFor, type ProjectStatus } from "@/lib/studio";
import type { Project } from "@/lib/types";
import { StatusBadge } from "./primitives";

export function ProjectCard({
  project,
  status,
  videos,
  layout = "grid",
}: {
  project: Project;
  status: ProjectStatus;
  videos: number;
  layout?: "grid" | "list";
}) {
  const { t, formatRelative } = useI18n();
  const tint = tintFor(project.id);
  const topic = project.topic.trim() || t.projects.noTopic;
  const meta = [t.projects.videos(videos), project.created_at ? formatRelative(project.created_at) : null]
    .filter(Boolean)
    .join(" · ");
  const badge = <StatusBadge status={status} label={t.status.project[status]} />;

  if (layout === "list") {
    return (
      <Link
        href={`/projects/${project.id}`}
        className="panel flex items-center gap-4 p-3 transition-colors hover:border-border-strong"
      >
        <div className={cn("h-14 w-24 shrink-0 rounded-lg bg-gradient-to-br", tint)} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{project.title}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {topic} · {meta}
          </p>
        </div>
        {badge}
      </Link>
    );
  }

  return (
    <Link
      href={`/projects/${project.id}`}
      className="panel group overflow-hidden transition-colors hover:border-border-strong"
    >
      <div className={cn("relative aspect-video bg-gradient-to-br", tint)}>
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between p-3">
          <span className="inline-flex items-center gap-1 rounded-md bg-background/70 px-2 py-1 text-[11px] font-medium backdrop-blur">
            <Film className="size-3" />
            {t.projects.videos(videos)}
          </span>
        </div>
      </div>
      <div className="space-y-2 p-4">
        <div className="flex items-start justify-between gap-3">
          <p className="min-w-0 font-medium leading-snug break-words group-hover:text-primary">{project.title}</p>
          {badge}
        </div>
        <p className="line-clamp-2 text-xs text-muted-foreground">{topic}</p>
        {project.created_at && <p className="text-[11px] text-muted-foreground">{formatRelative(project.created_at)}</p>}
      </div>
    </Link>
  );
}
