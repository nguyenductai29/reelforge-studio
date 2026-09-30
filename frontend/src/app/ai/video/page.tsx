"use client";

import Link from "next/link";
import { useState } from "react";
import { Download, Play, Plus, RefreshCw, Send, Sparkles, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { FieldLabel, OptionChips, PageHeader, SoonBadge, StatusBadge } from "@/components/reelforge/primitives";
import { AiSoonBanner, ChipField } from "@/components/reelforge/ai-tool";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useAiTools, useDashboard, useRuns } from "@/lib/queries";
import { isActiveRun } from "@/lib/studio";

const ratios = ["9:16", "16:9", "1:1"];

export default function VideoGeneratorPage() {
  const { t, formatRelative } = useI18n();
  const v = t.ai.video;
  useDocumentTitle(v.title);
  const [ratio, setRatio] = useState(["9:16"]);
  const { data } = useDashboard();
  const runs = useRuns().data ?? [];
  const videoTools = (useAiTools().data ?? []).filter((tool) => tool.task === "video" && tool.is_enabled);
  // Runs whose workflow contains a video step are the only renders the studio makes today.
  const videoWorkflows = new Set(
    (data?.workflows ?? []).filter((w) => w.graph.nodes.some((n) => n.type === "video")).map((w) => w.id),
  );
  const renders = runs.filter((run) => videoWorkflows.has(run.workflow_id)).slice(0, 6);
  const frame = ratio[0] === "9:16" ? "aspect-[9/16] w-full max-w-[280px]" : ratio[0] === "1:1" ? "aspect-square w-full max-w-[460px]" : "aspect-video w-full";

  return (
    <div className="space-y-6">
      <PageHeader title={v.title} subtitle={v.subtitle} />
      <AiSoonBanner />
      <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)_280px]">
        <aside className="panel space-y-5 p-4">
          <div>
            <FieldLabel htmlFor="video-prompt">{v.prompt}</FieldLabel>
            <Textarea id="video-prompt" rows={5} placeholder={v.promptPlaceholder} className="resize-none bg-surface-2" />
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" className="flex-1" disabled>
              <Upload className="size-3.5" /> {v.referenceImage}
            </Button>
            <Button variant="outline" size="sm" className="flex-1" disabled>
              {v.attachScript}
            </Button>
          </div>
          <div>
            <FieldLabel>{v.aspect}</FieldLabel>
            <OptionChips options={ratios} value={ratio} onChange={setRatio} />
          </div>
          <ChipField label={v.duration} options={v.durations} initial={[2]} />
          {videoTools.length ? (
            <ChipField label={v.model} options={videoTools.map((tool) => `${tool.provider} · ${tool.model}`)} />
          ) : (
            <div>
              <FieldLabel>{v.model}</FieldLabel>
              <p className="text-xs text-muted-foreground">
                {v.noModels} ·{" "}
                <Link href="/models" className="text-primary hover:underline">
                  {t.editor.inspector.configureModels}
                </Link>
              </p>
            </div>
          )}
          <ChipField label={v.quality} options={v.qualities} initial={[2]} />
          <ChipField label={v.audio} options={v.audios} />
          <Button className="w-full" disabled>
            <Sparkles className="size-4" /> {v.submit} <SoonBadge className="ml-1" />
          </Button>
        </aside>
        <section className="space-y-3">
          <div className="panel stage-glow flex items-center justify-center overflow-hidden p-6">
            <div className={frame}>
              <div className="flex size-full items-center justify-center rounded-xl bg-gradient-to-br from-[oklch(0.32_0.06_250)] to-[oklch(0.19_0.02_270)]">
                <span className="flex size-14 items-center justify-center rounded-full bg-background/70 backdrop-blur">
                  <Play className="size-5" />
                </span>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {(
              [
                [RefreshCw, v.regenerate],
                [null, v.variation],
                [Download, v.download],
                [Plus, v.addToProject],
              ] as const
            ).map(([Icon, label]) => (
              <Button key={label} variant="outline" size="sm" disabled>
                {Icon && <Icon className="size-3.5" />} {label}
              </Button>
            ))}
            <Button size="sm" disabled>
              <Send className="size-3.5" /> {v.publish}
            </Button>
          </div>
        </section>
        <aside className="space-y-4">
          <div className="panel p-4">
            <p className="text-sm font-medium">{v.renderStatus}</p>
            <div className="mt-3 space-y-3">
              {renders.length === 0 && <p className="text-xs text-muted-foreground">{v.noRenders}</p>}
              {renders.map((run) => (
                <Link
                  key={run.id}
                  href={`/workflows/${run.workflow_id}?run=${run.id}`}
                  className="block rounded-lg border border-border p-3 transition-colors hover:border-border-strong"
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="min-w-0 break-words text-xs leading-snug">
                      {data?.projects.find((p) => p.id === run.project_id)?.title ?? t.editor.runs.unknownProject}
                    </p>
                    <StatusBadge status={run.status} label={t.status.run[run.status]} />
                  </div>
                  <p className="mt-1 text-[11px] text-muted-foreground">{formatRelative(run.created_at)}</p>
                  {isActiveRun(run) && (
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-primary/20">
                      <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
                    </div>
                  )}
                </Link>
              ))}
            </div>
          </div>
          <div className="panel p-4 text-xs text-muted-foreground">
            <p className="text-sm font-medium text-foreground">{v.thisRender}</p>
            <p className="mt-2">{v.thisRenderHint}</p>
          </div>
        </aside>
      </div>
    </div>
  );
}
