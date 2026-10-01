"use client";

import Link from "next/link";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader, SectionTitle, StatusBadge } from "@/components/reelforge/primitives";
import { MiniDiagram } from "@/components/workflow/mini-diagram";
import { useCreateFromTemplate, useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useDashboard, useRuns } from "@/lib/queries";
import { previewKinds, workflowTemplates } from "@/lib/workflow";

export default function WorkflowsPage() {
  const { t, formatRelative } = useI18n();
  useDocumentTitle(t.workflows.title);
  const { data } = useDashboard();
  const runs = useRuns().data ?? [];
  const { create, pending } = useCreateFromTemplate();
  const workflows = data?.workflows ?? [];
  const lastRun = (id: string) => runs.find((run) => run.workflow_id === id);
  const limit = data?.limits.workflows ?? null;

  return (
    <div className="space-y-10">
      <PageHeader
        title={t.workflows.title}
        subtitle={limit !== null ? `${t.workflows.subtitle} (${workflows.length} / ${limit})` : t.workflows.subtitle}
      />

      <section>
        <SectionTitle>{t.workflows.recent}</SectionTitle>
        {workflows.length === 0 ? (
          <div className="panel p-5 text-sm text-muted-foreground">{t.workflows.empty}</div>
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {workflows.map((workflow) => {
              const run = lastRun(workflow.id);
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
                        {run ? ` · ${formatRelative(run.created_at)}` : ""}
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

      <section>
        <SectionTitle>{t.workflows.fromTemplate}</SectionTitle>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {workflowTemplates.map((template) => (
            <div key={template.id} className="panel flex flex-col gap-3 p-4">
              <MiniDiagram kinds={template.preview} branches={template.branches} />
              <div className="flex-1">
                <p className="text-sm font-medium">{t.templates[template.id].name}</p>
                <p className="mt-1 text-xs text-muted-foreground">{t.templates[template.id].description}</p>
              </div>
              <p className="text-[11px] text-muted-foreground">
                {template.steps ? t.common.steps(template.steps) : t.workflows.emptyCanvas}
                {template.branches ? ` · ${t.workflows.outputs(template.branches)}` : ""}
              </p>
              <Button
                size="sm"
                variant={template.backend ? "default" : "outline"}
                disabled={Boolean(pending)}
                onClick={() => void create(template.id)}
              >
                {pending === template.id && <Loader2 className="size-3.5 animate-spin" />}
                {t.workflows.createWorkflow}
              </Button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
