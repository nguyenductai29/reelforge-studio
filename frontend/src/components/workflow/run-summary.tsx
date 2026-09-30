"use client";

import { AlertTriangle, CheckCircle2, Film } from "lucide-react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import type { RunStepItem, RunSummary } from "@/lib/types";
import { clockTime } from "./ports";
import { detailText } from "./types";

const minutes = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

/** A run at a glance: progress, what it produced, the final video, credits and what needs a person. */
export function RunSummaryPanel({ summary, nodeName }: { summary: RunSummary; nodeName: (id: string) => string }) {
  const { t } = useI18n();
  const s = t.editor.summary;
  const results = (
    [
      [s.scriptWords, summary.counts.script_words],
      [s.scenes, summary.counts.scenes],
      [s.images, summary.counts.images],
      [s.clips, summary.counts.clips],
      [s.narrations, summary.counts.narrations],
      [s.subtitleCues, summary.counts.subtitle_cues],
    ] as [string, number][]
  ).filter(([, value]) => value > 0);
  const problems: [string, RunStepItem[], string][] = [
    [s.failed, summary.failed, "text-destructive"],
    [s.needsAttention, summary.needs_attention, "text-warning"],
    [s.blocked, summary.blocked, "text-warning"],
  ];

  return (
    <div className="w-full space-y-3 border-t border-border pt-2 text-xs">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
        <span>{s.progress(summary.steps.completed, summary.steps.total)}</span>
        <span>{s.elapsed(minutes(summary.elapsed_seconds))}</span>
        {summary.active_jobs > 0 && <span>{s.activeJobs(summary.active_jobs)}</span>}
        {summary.current.length > 0 && (
          <span className="text-primary">{s.running(summary.current.map((step) => nodeName(step.node_id)).join(", "))}</span>
        )}
      </div>

      {results.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {results.map(([label, value]) => (
            <span key={label} className="rounded-full bg-surface-2 px-2 py-0.5">
              {label}: <span className="font-medium text-foreground">{value}</span>
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2">
        <Film className="size-3.5 shrink-0 text-muted-foreground" />
        {summary.final_video ? (
          <span>
            {summary.final_video.final ? s.finalVideo : s.clipVideo} · {summary.final_video.filename}
            {summary.final_video.duration ? ` · ${clockTime(summary.final_video.duration)}` : ""}
          </span>
        ) : (
          <span className="text-muted-foreground">{s.noFinalVideo}</span>
        )}
      </div>

      <div>
        <p className="mb-1 text-muted-foreground">{s.credits}</p>
        <div className="grid grid-cols-4 gap-1.5 text-center">
          {(
            [
              [s.reserved, summary.credits.reserved],
              [s.consumed, summary.credits.consumed],
              [s.refunded, summary.credits.refunded],
              [s.held, summary.credits.held],
            ] as [string, number][]
          ).map(([label, value]) => (
            <div key={label} className="rounded-md bg-surface-2 px-1 py-1">
              <p className="text-sm font-semibold">{value}</p>
              <p className="text-[10px] text-muted-foreground">{label}</p>
            </div>
          ))}
        </div>
        <p className="mt-1 text-[10px] text-muted-foreground">{s.creditsHint}</p>
      </div>

      {problems.map(([label, items, color]) =>
        items.length ? (
          <div key={label}>
            <p className={cn("mb-1 flex items-center gap-1 font-medium", color)}>
              <AlertTriangle className="size-3.5" /> {label}
            </p>
            <ul className="space-y-0.5">
              {items.map((step) => (
                <li key={step.node_id} className="break-words">
                  <span className="font-medium">{nodeName(step.node_id)}</span>
                  {step.detail ? ` · ${detailText(step.detail, t)}` : ""}
                </li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
      {summary.render_failed?.message && (
        <p className="break-words text-destructive">
          {s.renderError}: {summary.render_failed.message}
        </p>
      )}
      {summary.review.approved && (
        <p className="flex items-center gap-1 text-success">
          <CheckCircle2 className="size-3.5" /> {s.approved}
        </p>
      )}
    </div>
  );
}
