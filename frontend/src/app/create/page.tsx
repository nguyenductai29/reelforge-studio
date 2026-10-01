"use client";

import { Loader2 } from "lucide-react";
import { PageHeader } from "@/components/reelforge/primitives";
import { MiniDiagram } from "@/components/workflow/mini-diagram";
import { useCreateFromTemplate, useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { templateById, type TemplateId } from "@/lib/workflow";

// Every template here creates a workflow the backend runs (lib/workflow.ts: a local graph or a backend template).
const mainTemplates: TemplateId[] = [
  "youtube-short",
  "youtube-video",
  "tiktok-video",
  "facebook-reel",
  "movie-recap",
  "movie-review",
  "repurpose",
  "article-to-video",
  "product-video",
  "social-video",
  "blank",
];

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
          return (
            <button
              key={id}
              type="button"
              disabled={Boolean(pending)}
              onClick={() => void create(id)}
              className="panel group flex flex-col gap-3 p-4 text-left transition-colors hover:border-border-strong"
            >
              <MiniDiagram kinds={template.preview} branches={template.branches} />
              <div>
                <p className="flex items-center gap-2 font-medium group-hover:text-primary">
                  {t.templates[id].name}
                  {pending === id && <Loader2 className="size-3.5 animate-spin" />}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{t.templates[id].description}</p>
              </div>
              <p className="mt-auto text-[11px] text-muted-foreground">
                {template.steps ? t.common.steps(template.steps) : t.create.emptyCanvas}
              </p>
            </button>
          );
        })}
      </div>
    </div>
  );
}
