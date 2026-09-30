"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ArrowLeft, Loader2, RefreshCw, Workflow as WorkflowIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/reelforge/primitives";
import { WorkflowEditor } from "@/components/workflow/workflow-editor";
import { errorText } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useDashboard, useNodeTypes } from "@/lib/queries";

function WorkflowPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const search = useSearchParams();
  const { t } = useI18n();
  const { data } = useDashboard();
  const nodeTypes = useNodeTypes();
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
  // Connection handles are the node types' ports, so the canvas waits for them.
  if (!nodeTypes.data) {
    return (
      <div className="flex h-[calc(100dvh-3.5rem)] flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
        {nodeTypes.error ? (
          <>
            <p>{errorText(nodeTypes.error, t)}</p>
            <Button variant="outline" size="sm" onClick={() => void nodeTypes.refetch()}>
              <RefreshCw className="size-3.5" /> {t.common.retry}
            </Button>
          </>
        ) : (
          <Loader2 className="size-5 animate-spin" />
        )}
      </div>
    );
  }
  return (
    <WorkflowEditor
      key={workflow.id}
      workflow={workflow}
      catalog={nodeTypes.data.node_types}
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
