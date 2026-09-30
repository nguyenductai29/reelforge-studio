"use client";

import Link from "next/link";
import { Film, Image as ImageIcon, Loader2, PenLine, Recycle, Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader, SectionTitle, SoonBadge } from "@/components/reelforge/primitives";
import { MiniDiagram } from "@/components/workflow/mini-diagram";
import { useCreateFromTemplate, useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { templateById, type TemplateId } from "@/lib/workflow";

const mainTemplates: TemplateId[] = [
  "social-video",
  "movie-recap",
  "movie-review",
  "youtube-video",
  "youtube-short",
  "repurpose",
  "product-video",
  "blank",
];

const tools = [
  { key: "writer", href: "/ai/writer", icon: PenLine },
  { key: "recap", href: "/ai/movie-recap", icon: Film },
  { key: "video", href: "/ai/video", icon: Video },
  { key: "image", href: "/ai/image", icon: ImageIcon },
  { key: "repurpose", href: "/ai/repurpose", icon: Recycle },
] as const;

export default function CreatePage() {
  const { t } = useI18n();
  useDocumentTitle(t.nav.create);
  const { create, pending } = useCreateFromTemplate();

  return (
    <div className="space-y-10">
      <PageHeader title={t.create.title} subtitle={t.create.subtitle} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {mainTemplates.map((id) => {
          const template = templateById(id);
          const available = Boolean(template.graph);
          return (
            <button
              key={id}
              type="button"
              disabled={!available || Boolean(pending)}
              onClick={() => void create(id)}
              title={available ? undefined : t.create.soonHint}
              className={cn(
                "panel group flex flex-col gap-3 p-4 text-left transition-colors",
                available ? "hover:border-border-strong" : "cursor-not-allowed opacity-75",
              )}
            >
              <MiniDiagram kinds={template.preview} branches={template.branches} />
              <div>
                <p className={cn("flex items-center gap-2 font-medium", available && "group-hover:text-primary")}>
                  {t.templates[id].name}
                  {pending === id && <Loader2 className="size-3.5 animate-spin" />}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{t.templates[id].description}</p>
              </div>
              <div className="mt-auto flex items-center justify-between gap-2">
                <p className="text-[11px] text-muted-foreground">
                  {template.steps ? t.common.steps(template.steps) : t.create.emptyCanvas}
                </p>
                {!available && <SoonBadge />}
              </div>
            </button>
          );
        })}
      </div>

      <section>
        <SectionTitle>{t.create.singleTool}</SectionTitle>
        <div className="flex flex-wrap gap-2">
          {tools.map((tool) => (
            <Button key={tool.href} asChild variant="outline" size="sm">
              <Link href={tool.href}>
                <tool.icon className="size-4 text-primary" />
                {t.create.tools[tool.key]}
              </Link>
            </Button>
          ))}
        </div>
      </section>
    </div>
  );
}
