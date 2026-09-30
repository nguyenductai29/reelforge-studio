"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ArrowLeft, Workflow as WorkflowIcon } from "lucide-react";
import { EmptyState } from "@/components/reelforge/primitives";
import { WorkflowEditor } from "@/components/workflow/workflow-editor";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useDashboard } from "@/lib/queries";

function WorkflowPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const search = useSearchParams();
  const { t } = useI18n();
  const { data } = useDashboard();
  const workflow = data?.workflows.find((w) => w.id === workflowId);
  useDocumentTitle(workflow?.name ?? t.workflows.title);

  if (!workflow) {
    return (
      <div className="mx-auto max-w-[1400px] space-y-6 px-4 py-6 sm:px-6 lg:px-8">
        <Link href="/workflows" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-3.5" /> {t.editor.back}
        </Link>
        <EmptyState icon={WorkflowIcon} title={t.editor.notFound} description={t.editor.notFoundHint} />
      </div>
    );
  }
  return (
    <WorkflowEditor
      key={workflow.id}
      workflow={workflow}
      initialRunId={search.get("run")}
      initialProjectId={search.get("project")}
    />
  );
}

export default function Page() {
  return (
    <Suspense>
      <WorkflowPage />
    </Suspense>
  );
}
