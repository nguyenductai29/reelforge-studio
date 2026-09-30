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
export const NodeContext = createContext<{ assetCount: number; vertical: boolean }>({ assetCount: 0, vertical: true });

function Preview({ data }: { data: StudioNode["data"] }) {
  const { t } = useI18n();
  const { assetCount, vertical } = useContext(NodeContext);
  const kind = kindOf[data.type];
  const output = data.output ?? null;
  const running = data.status === "running" || data.status === "queued";

  switch (kind) {
    case "image":
    case "video":
    case "render": {
      const assetId = typeof output?.asset_id === "string" ? output.asset_id : null;
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
            </>
          ) : (
            <span className="px-2 text-center text-[10px] text-muted-foreground">
              {running ? t.editor.node.generating : t.editor.node.previewHere}
            </span>
          )}
        </div>
      );
    }
    case "voice":
      return (
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
      );
    case "subtitle":
      return (
        <div className="space-y-1 rounded-lg bg-surface-2 p-2">
          {["—", "—"].map((line, i) => (
            <p key={i} className="truncate text-center text-[11px] font-medium text-muted-foreground">
              {line}
            </p>
          ))}
        </div>
      );
    case "script":
      return (
        <div className="rounded-lg bg-surface-2 p-2.5 text-[11px] leading-relaxed">
          <p className="text-muted-foreground">{t.editor.node.scriptHere}</p>
        </div>
      );
    case "ai": {
      const text = typeof output?.text === "string" ? output.text : null;
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
    case "publish":
      return (
        <div className="flex items-center gap-2 rounded-lg bg-surface-2 p-2">
          {data.type === "publish" ? <PlatformIcon platform="youtube" /> : <Send className="size-4 text-muted-foreground" />}
          <span className="truncate text-[11px]">{t.editor.node.youtubePrivate}</span>
        </div>
      );
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
      <Handle type="target" position={Position.Left} className="studio-handle" />

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

      <div className="flex items-center justify-between px-3 pb-2 text-[10px] text-muted-foreground">
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
        <span>→ {t.editor.outputs[data.type]}</span>
      </div>

      {kind !== "publish" && <Handle type="source" position={Position.Right} className="studio-handle" />}
    </div>
  );
}

export const StudioNodeComponent = memo(StudioNodeView);
