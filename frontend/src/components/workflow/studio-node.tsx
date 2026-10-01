"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { createContext, memo, useContext } from "react";
import { CheckCircle2, Loader2, MoreHorizontal, Play, Send, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { assetUrl } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { kindOf, type NodeKind } from "@/lib/workflow";
import { PlatformIcon } from "@/components/reelforge/primitives";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { kindIcon } from "./kind-icon";
import {
  clockTime,
  outputJobs,
  outputMedia,
  outputMetadata,
  outputScenes,
  outputSubtitle,
  outputText,
  portLabel,
  portsOf,
  type PortCatalog,
} from "./ports";
import type { NodeStatus, StudioNode } from "./types";

const kindWidth: Record<NodeKind, string> = {
  input: "w-[230px]",
  ai: "w-[240px]",
  script: "w-[260px]",
  image: "w-[270px]",
  video: "w-[270px]",
  voice: "w-[240px]",
  subtitle: "w-[240px]",
  edit: "w-[210px]",
  render: "w-[270px]",
  review: "w-[210px]",
  publish: "w-[210px]",
};

const statusDot: Record<NodeStatus, string> = {
  idle: "bg-muted-foreground/50",
  ready: "bg-info",
  attention: "bg-warning",
  queued: "bg-muted-foreground",
  running: "bg-primary animate-pulse",
  completed: "bg-success",
  review: "bg-warning",
  blocked: "bg-warning",
  skipped: "bg-muted-foreground/50",
  failed: "bg-destructive",
};

const waveform = [4, 9, 14, 7, 18, 11, 5, 15, 20, 9, 13, 6, 17, 10, 4, 12, 19, 8, 14, 6, 10, 16, 7, 12];

export type NodeActions = { onDuplicate: (id: string) => void; onDelete: (id: string) => void };
export const NodeActionsContext = createContext<NodeActions | null>(null);

/** Studio facts the previews need, supplied once by the editor. */
export const NodeContext = createContext<{ assetCount: number; vertical: boolean; catalog: PortCatalog }>({
  assetCount: 0,
  vertical: true,
  catalog: {},
});

/**
 * One row per port: inputs on the left edge, outputs on the right. A node with no
 * inputs keeps an inert target handle so older edges into it still draw.
 */
function Ports({ type }: { type: StudioNode["data"]["type"] }) {
  const { t } = useI18n();
  const { catalog } = useContext(NodeContext);
  const ports = portsOf(catalog, type);
  const rows = Math.max(ports.inputs.length, ports.outputs.length);
  if (!rows) return null;
  return (
    <div className="pb-1">
      {Array.from({ length: rows }, (_, index) => {
        const input = ports.inputs[index];
        const output = ports.outputs[index];
        return (
          <div key={index} className="relative flex h-5 items-center justify-between gap-2 px-3 text-[10px] text-muted-foreground">
            {input ? (
              <>
                <Handle type="target" position={Position.Left} id={input.name} className="studio-handle" />
                <span className="truncate">{portLabel(t, input.name)}</span>
              </>
            ) : (
              <span />
            )}
            {output && (
              <>
                <span className="truncate text-right">{portLabel(t, output.name)}</span>
                <Handle type="source" position={Position.Right} id={output.name} className="studio-handle" />
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Preview({ data }: { data: StudioNode["data"] }) {
  const { t } = useI18n();
  const { assetCount, vertical, catalog } = useContext(NodeContext);
  const kind = kindOf[data.type];
  const output = data.output ?? null;
  const running = data.status === "running" || data.status === "queued";

  switch (kind) {
    case "image": {
      const images = outputMedia(output, "image_assets");
      const progress = outputJobs(output);
      if (images.length) {
        return (
          <div className="space-y-1">
            <div className={cn("grid gap-1 overflow-hidden rounded-lg", images.length > 1 ? "grid-cols-2" : "grid-cols-1")}>
              {images.slice(0, 4).map((image) => (
                <img key={image.id} src={assetUrl(image.id)} alt="" loading="lazy" className="aspect-video w-full rounded bg-black object-cover" />
              ))}
            </div>
            {(images.length > 1 || (progress && progress.done < progress.expected)) && (
              <p className="text-[10px] text-muted-foreground">
                {progress && progress.done < progress.expected
                  ? t.editor.node.progress(progress.done, progress.expected)
                  : t.editor.node.images(images.length)}
              </p>
            )}
          </div>
        );
      }
      return (
        <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-surface-2">
          <span className="px-2 text-center text-[10px] text-muted-foreground">
            {running ? (progress ? t.editor.node.progress(progress.done, progress.expected) : t.editor.node.generating) : t.editor.node.previewHere}
          </span>
        </div>
      );
    }
    case "video":
    case "render": {
      if (data.type === "match_scenes") {
        const matched = Array.isArray(output?.source_clips) ? output.source_clips.length : 0;
        return (
          <p className="rounded-lg bg-surface-2 p-2.5 text-[11px] text-muted-foreground">
            {matched
              ? t.editor.node.clipsMatched(typeof output?.matched === "number" ? output.matched : 0, matched)
              : running
                ? t.editor.node.generating
                : t.editor.node.previewHere}
          </p>
        );
      }
      const clips = outputMedia(output, "video_assets");
      const progress = outputJobs(output);
      const assetId = clips[0]?.id ?? (typeof output?.asset_id === "string" ? output.asset_id : null);
      return (
        <div
          className={cn(
            "relative flex items-center justify-center overflow-hidden rounded-lg",
            vertical ? "mx-auto aspect-[9/16] w-[46%]" : "aspect-video w-full",
            assetId ? "bg-black" : "bg-surface-2",
          )}
        >
          {assetId ? (
            <>
              <video src={`${assetUrl(assetId)}#t=0.5`} muted playsInline preload="metadata" className="size-full object-cover" />
              <span className="absolute flex size-8 items-center justify-center rounded-full bg-background/70 backdrop-blur">
                <Play className="size-3.5" />
              </span>
              {(clips.length > 1 || (progress && progress.done < progress.expected)) && (
                <span className="absolute bottom-1 right-1 rounded bg-background/80 px-1.5 py-0.5 text-[10px] backdrop-blur">
                  {progress && progress.done < progress.expected
                    ? t.editor.node.progress(progress.done, progress.expected)
                    : t.editor.node.clips(clips.length)}
                </span>
              )}
              {data.type === "render" && clips[0]?.duration ? (
                <span className="absolute bottom-1 right-1 rounded bg-background/80 px-1.5 py-0.5 text-[10px] backdrop-blur">
                  {clockTime(clips[0].duration)}
                </span>
              ) : null}
            </>
          ) : (
            <span className="px-2 text-center text-[10px] text-muted-foreground">
              {running ? (progress ? t.editor.node.progress(progress.done, progress.expected) : t.editor.node.generating) : t.editor.node.previewHere}
            </span>
          )}
        </div>
      );
    }
    case "voice": {
      const segments = outputMedia(output, "audio_assets");
      const progress = outputJobs(output);
      // One narration plays on the node; scene segments show their count (each plays in the inspector).
      if (segments.length === 1 && segments[0]!.scene_index == null) {
        return <audio src={assetUrl(segments[0]!.id)} controls preload="none" className="nodrag h-8 w-full" />;
      }
      const note =
        progress && progress.done < progress.expected
          ? t.editor.node.progress(progress.done, progress.expected)
          : segments.length
            ? t.editor.node.segments(segments.length)
            : null;
      return (
        <div className="space-y-1">
          <div className="flex items-center gap-2 rounded-lg bg-surface-2 p-2">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
              <Play className="size-3" />
            </span>
            <div className="flex h-6 flex-1 items-center gap-[2px]">
              {waveform.map((h, i) => (
                <span
                  key={i}
                  style={{ height: data.status === "completed" ? h : 3 }}
                  className={cn(
                    "w-[3px] rounded-full",
                    data.status === "completed" ? "bg-primary/70" : "bg-muted-foreground/30",
                    running && "animate-pulse",
                  )}
                />
              ))}
            </div>
          </div>
          {note && <p className="text-[10px] text-muted-foreground">{note}</p>}
        </div>
      );
    }
    case "subtitle": {
      const file = outputSubtitle(output);
      const lines = file ? file.cues.slice(0, 2).map((cue) => cue.text.replace(/\n/g, " ")) : ["—", "—"];
      return (
        <div className="space-y-1 rounded-lg bg-surface-2 p-2">
          {lines.map((line, i) => (
            <p key={i} className={cn("truncate text-center text-[11px] font-medium", !file && "text-muted-foreground")}>
              {line}
            </p>
          ))}
          {file && (
            <p className="text-center text-[10px] text-muted-foreground">
              {(file.format ?? "srt").toUpperCase()} · {t.editor.node.cues(file.cue_count ?? file.cues.length)}
            </p>
          )}
        </div>
      );
    }
    case "script": {
      const scenes = outputScenes(output);
      if (scenes) {
        return (
          <div className="space-y-1 rounded-lg bg-surface-2 p-2.5 text-[11px] leading-relaxed">
            <p className="text-muted-foreground">{t.editor.node.sceneCount(scenes.length)}</p>
            {scenes.slice(0, 2).map((scene) => (
              <p key={scene.index} className="line-clamp-1">
                {scene.index}. {scene.text}
              </p>
            ))}
          </div>
        );
      }
      return (
        <div className="rounded-lg bg-surface-2 p-2.5 text-[11px] leading-relaxed">
          <p className="text-muted-foreground">{data.type === "scenes" ? t.editor.node.scenesHere : t.editor.node.scriptHere}</p>
        </div>
      );
    }
    case "ai": {
      const prepared = data.type === "metadata" ? outputMetadata(output) : null;
      if (prepared) {
        return (
          <div className="space-y-1 rounded-lg bg-surface-2 p-2.5 text-[11px] leading-relaxed">
            <p className="line-clamp-2 font-medium">{prepared.title}</p>
            {prepared.tags.length > 0 && (
              <p className="line-clamp-1 text-primary">{prepared.tags.map((tag) => `#${tag}`).join(" ")}</p>
            )}
          </div>
        );
      }
      const summary = data.type === "story_analysis" && typeof output?.summary === "string" ? output.summary : null;
      const text = summary || outputText(output, portsOf(catalog, data.type));
      return (
        <div className="rounded-lg bg-surface-2 p-2.5 text-[11px] leading-relaxed">
          {text ? (
            <p className="line-clamp-4 whitespace-pre-line">{text}</p>
          ) : (
            <p className="text-muted-foreground">{running ? t.editor.node.generating : t.editor.node.textHere}</p>
          )}
        </div>
      );
    }
    case "input": {
      if (data.type === "source_text" || data.type === "source_url" || data.type === "source_media") {
        const source = (output?.source ?? null) as { title?: string; text?: string; segments?: unknown[] | null } | null;
        const lines = source
          ? [
              source.title || null,
              source.segments?.length
                ? t.editor.node.segmentsFound(source.segments.length)
                : source.text
                  ? t.editor.node.sourceChars(source.text.length)
                  : null,
            ].filter((line): line is string => Boolean(line))
          : [];
        return lines.length ? (
          <ul className="space-y-1 rounded-lg bg-surface-2 p-2.5 text-[11px]">
            {lines.map((line, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary" />
                <span className={cn("line-clamp-2", i > 0 && "text-muted-foreground")}>{line}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rounded-lg bg-surface-2 p-2.5 text-[11px] text-muted-foreground">
            {running ? t.editor.node.generating : t.editor.node.sourceHere}
          </p>
        );
      }
      if (data.type === "assets") {
        const listed = Array.isArray(output?.assets) ? output.assets.length : null;
        return (
          <p className="rounded-lg bg-surface-2 p-2.5 text-[11px] text-muted-foreground">
            {listed !== null ? t.editor.node.media(listed) : `${t.editor.node.studioMedia} · ${t.editor.node.media(assetCount)}`}
          </p>
        );
      }
      const title = typeof output?.title === "string" ? output.title : null;
      const topic = typeof output?.topic === "string" ? output.topic : null;
      return title || topic ? (
        <ul className="space-y-1 rounded-lg bg-surface-2 p-2.5 text-[11px]">
          {[title, topic].filter(Boolean).map((line, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary" />
              <span className={cn("line-clamp-2", i > 0 && "text-muted-foreground")}>{line}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="rounded-lg bg-surface-2 p-2.5 text-[11px] text-muted-foreground">{t.editor.node.fromProject}</p>
      );
    }
    case "publish": {
      const prepared = outputMetadata(output);
      return (
        <div className="flex items-center gap-2 rounded-lg bg-surface-2 p-2">
          {data.type === "publish" ? <PlatformIcon platform="youtube" /> : <Send className="size-4 text-muted-foreground" />}
          <span className="min-w-0 truncate text-[11px]">
            {prepared
              ? `${prepared.title} · ${t.publishing.privacy[(prepared.privacy_status ?? "private") as "private"]}`
              : t.editor.node.channelsHandoff}
          </span>
        </div>
      );
    }
    default:
      return <p className="rounded-lg bg-surface-2 p-2.5 text-[11px] text-muted-foreground">{t.editor.node.reviewSummary}</p>;
  }
}

function StudioNodeView({ id, data, selected }: NodeProps<StudioNode>) {
  const { t } = useI18n();
  const actions = useContext(NodeActionsContext);
  const kind = kindOf[data.type];
  const Icon = kindIcon[kind];
  const name = t.nodes[data.type].name;
  const statusLabel = t.status.node[data.status];
  const { catalog } = useContext(NodeContext);
  const hasInputs = portsOf(catalog, data.type).inputs.length > 0;

  return (
    <div
      className={cn(
        "group relative rounded-xl border bg-card shadow-[var(--shadow-panel)] transition-[border-color,box-shadow]",
        kindWidth[kind],
        selected ? "border-primary/70" : "border-border-strong",
        data.status === "running" && "node-running",
        data.status === "failed" && "border-destructive/70",
      )}
    >
      {!hasInputs && (
        <Handle type="target" position={Position.Left} isConnectable={false} className="studio-handle !opacity-0" />
      )}

      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-surface-2">
          <Icon className="size-3.5 text-primary" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[10px] uppercase tracking-wider text-muted-foreground">{name}</p>
          <p className="truncate text-[13px] font-medium leading-tight">{data.label || name}</p>
        </div>
        <span className="flex items-center gap-1" title={statusLabel}>
          {data.status === "running" ? (
            <Loader2 className="size-3.5 animate-spin text-primary" />
          ) : data.status === "completed" ? (
            <CheckCircle2 className="size-3.5 text-success" />
          ) : data.status === "failed" ? (
            <TriangleAlert className="size-3.5 text-destructive" />
          ) : (
            <span className={cn("size-2 rounded-full", statusDot[data.status])} />
          )}
        </span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={name}
              className="nodrag rounded p-0.5 text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            >
              <MoreHorizontal className="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => actions?.onDuplicate(id)}>{t.editor.node.duplicate}</DropdownMenuItem>
            <DropdownMenuItem className="text-destructive" onClick={() => actions?.onDelete(id)}>
              {t.editor.node.delete}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="p-2.5">
        <Preview data={data} />
      </div>

      <Ports type={data.type} />

      <div className="flex items-center px-3 pb-2 text-[10px] text-muted-foreground">
        <span
          className={cn(
            (data.status === "review" || data.status === "attention" || data.status === "blocked") && "text-warning",
            data.status === "failed" && "text-destructive",
            data.status === "completed" && "text-success",
            data.status === "running" && "text-primary",
          )}
        >
          {statusLabel}
        </span>
      </div>
    </div>
  );
}

export const StudioNodeComponent = memo(StudioNodeView);
