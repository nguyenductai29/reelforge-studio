"use client";

import Link from "next/link";
import {
  addEdge,
  Background,
  BackgroundVariant,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  useViewport,
  type Connection,
  type Edge,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  Download,
  History,
  Loader2,
  Maximize,
  Minus,
  Play,
  Plus,
  Redo2,
  RotateCcw,
  Save,
  Search,
  Undo2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { FieldLabel, SoonBadge, StatusBadge } from "@/components/reelforge/primitives";
import { NewProjectDialog } from "@/components/reelforge/new-project-dialog";
import { api, assetUrl, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import {
  keys,
  useAiTools,
  useDashboard,
  useReadiness,
  useRefreshStudio,
  useRun,
  useSettings,
  useWorkflowRuns,
} from "@/lib/queries";
import { approvedAsset, isActiveRun } from "@/lib/studio";
import type { Graph, NodeType, Project, Readiness, ReadinessStep, Run, RunStep, Workflow } from "@/lib/types";
import { EXECUTABLE, MAX_NODES, TEXT_NODES, kindOf, newNodeId, nodeLibrary } from "@/lib/workflow";
import { kindIcon } from "./kind-icon";
import { NodeActionsContext, NodeContext, StudioNodeComponent, type NodeActions } from "./studio-node";
import { detailText, readinessText, runStatusToNode, type NodeStatus, type StudioNode } from "./types";

const nodeTypes = { studio: StudioNodeComponent };
const DRAG_TYPE = "application/reelforge";
const ASPECT = { vertical: "9:16", horizontal: "16:9", square: "1:1" } as const;

type Snapshot = { nodes: StudioNode[]; edges: Edge[] };

const toNodes = (graph: Graph): StudioNode[] =>
  graph.nodes.map((node) => ({
    id: node.id,
    type: "studio",
    position: { x: node.x, y: node.y },
    // Settings are kept as-is so saving the canvas never drops them.
    data: { type: node.type, label: node.label ?? undefined, config: node.config ?? null, status: "idle" },
  }));

const toEdges = (graph: Graph): Edge[] =>
  graph.edges.map((edge) => ({ id: `${edge.source}-${edge.target}`, source: edge.source, target: edge.target }));

function NodeLibrary({ onAdd, className }: { onAdd: (type: NodeType) => void; className?: string }) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string[]>(["input", "ai", "script", "visual", "output"]);
  const q = query.trim().toLocaleLowerCase();
  const lib = t.editor.library;

  return (
    <aside className={cn("w-64 shrink-0 flex-col border-r border-border bg-sidebar", className)}>
      <div className="border-b border-border p-3">
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">{lib.title}</p>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={lib.search}
            aria-label={lib.search}
            className="h-8 bg-surface pl-8 text-sm"
          />
        </div>
      </div>
      <div className="scrollbar-thin flex-1 space-y-1 overflow-y-auto p-2">
        {nodeLibrary.map((group) => {
          const items = group.items.filter((item) => lib.items[item.id].toLocaleLowerCase().includes(q));
          if (!items.length) return null;
          const expanded = q.length > 0 || open.includes(group.category);
          return (
            <div key={group.category}>
              <button
                type="button"
                aria-expanded={expanded}
                onClick={() =>
                  setOpen((o) =>
                    o.includes(group.category) ? o.filter((c) => c !== group.category) : [...o, group.category],
                  )
                }
                className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
              >
                {lib.categories[group.category]}
                <ChevronDown className={cn("size-3.5 transition-transform", !expanded && "-rotate-90")} />
              </button>
              {expanded && (
                <div className="space-y-0.5 pb-2">
                  {items.map((item) => {
                    const Icon = kindIcon[item.kind];
                    const type = item.type;
                    return type ? (
                      <button
                        key={item.id}
                        type="button"
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData(DRAG_TYPE, type);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        onClick={() => onAdd(type)}
                        className="flex w-full cursor-grab items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-left text-sm hover:border-border hover:bg-surface active:cursor-grabbing"
                      >
                        <Icon className="size-3.5 shrink-0 text-primary" />
                        <span className="truncate">{lib.items[item.id]}</span>
                      </button>
                    ) : (
                      <div
                        key={item.id}
                        title={lib.soonHint}
                        className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground/70"
                      >
                        <Icon className="size-3.5 shrink-0" />
                        <span className="min-w-0 flex-1 truncate">{lib.items[item.id]}</span>
                        <SoonBadge className="px-1.5" />
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <p className="border-t border-border p-3 text-[11px] text-muted-foreground">{lib.hint}</p>
    </aside>
  );
}

type VideoSettings = {
  tools: { id: string; provider: string; model: string }[];
  toolId: string;
  setToolId: (id: string) => void;
  prompt: string;
  setPrompt: (prompt: string) => void;
};

function VideoFields({ video, idPrefix }: { video: VideoSettings; idPrefix: string }) {
  const { t } = useI18n();
  const i = t.editor.inspector;
  return (
    <>
      <div>
        <FieldLabel>{i.model}</FieldLabel>
        {video.tools.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            {i.noModel}{" "}
            <Link href="/models" className="text-primary hover:underline">
              {i.configureModels}
            </Link>
          </p>
        ) : (
          <Select value={video.toolId || "auto"} onValueChange={(v) => video.setToolId(v === "auto" ? "" : v)}>
            <SelectTrigger className="bg-surface" aria-label={i.model}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">{i.modelAuto}</SelectItem>
              {video.tools.map((tool) => (
                <SelectItem key={tool.id} value={tool.id}>
                  {tool.provider} · {tool.model}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      <div>
        <FieldLabel htmlFor={`${idPrefix}-prompt`}>{i.prompt}</FieldLabel>
        <Textarea
          id={`${idPrefix}-prompt`}
          rows={4}
          maxLength={3000}
          value={video.prompt}
          onChange={(e) => video.setPrompt(e.target.value)}
          placeholder={i.promptPlaceholder}
          className="resize-none bg-surface"
        />
      </div>
    </>
  );
}

function Inspector({
  node,
  step,
  readinessStep,
  readiness,
  run,
  parents,
  advanced,
  aspect,
  video,
  busy,
  onClose,
  onRename,
  onDuplicate,
  onDelete,
  onRun,
  onApprove,
}: {
  node: StudioNode;
  step?: RunStep;
  readinessStep?: ReadinessStep;
  readiness?: Readiness;
  run?: Run;
  parents: string[];
  advanced: boolean;
  aspect: string;
  video: VideoSettings;
  busy: boolean;
  onClose: () => void;
  onRename: (label: string) => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onRun: () => void;
  onApprove: () => void;
}) {
  const { t } = useI18n();
  const i = t.editor.inspector;
  const d = node.data;
  const name = t.nodes[d.type].name;
  const detail = step
    ? detailText(step.detail, t)
    : readinessStep && readiness
      ? readinessText(readinessStep, readiness, t)
      : null;
  const hint =
    d.type === "idea"
      ? i.ideaHint
      : d.type === "assets"
        ? i.assetsHint
        : d.type === "review"
          ? i.reviewHint
          : d.type === "publish"
            ? i.publishHint
            : TEXT_NODES.has(d.type)
              ? i.textHint
              : null;
  const assetId = typeof step?.output?.asset_id === "string" ? step.output.asset_id : null;
  const generatedText = typeof step?.output?.text === "string" ? step.output.text : null;
  const tool = video.tools.find((x) => x.id === video.toolId) ?? video.tools[0];

  return (
    <aside className="absolute inset-y-0 right-0 z-10 flex w-full max-w-sm flex-col border-l border-border bg-sidebar sm:w-80 xl:static">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{name}</p>
          <p className="truncate text-sm font-medium">{d.label || name}</p>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label={t.common.close}>
          <X className="size-4" />
        </Button>
      </div>

      <div className="scrollbar-thin flex-1 space-y-5 overflow-y-auto p-4">
        <div className="flex items-center justify-between rounded-lg bg-surface p-2.5 text-xs">
          <span className="text-muted-foreground">{i.status}</span>
          <span className="font-medium">{t.status.node[d.status]}</span>
        </div>
        {detail && (
          <div
            className={cn(
              "rounded-lg border p-3 text-xs",
              d.status === "failed"
                ? "border-destructive/40 bg-[color-mix(in_oklab,var(--destructive)_10%,transparent)]"
                : d.status === "blocked" || d.status === "attention" || d.status === "review"
                  ? "border-warning/40 bg-[color-mix(in_oklab,var(--warning)_10%,transparent)]"
                  : "border-border bg-surface",
            )}
          >
            {detail}
          </div>
        )}
        <div>
          <FieldLabel htmlFor="step-name">{i.stepName}</FieldLabel>
          <Input
            id="step-name"
            value={d.label ?? ""}
            maxLength={80}
            placeholder={name}
            onChange={(e) => onRename(e.target.value)}
            className="bg-surface"
          />
        </div>
        <div>
          <FieldLabel>{i.about}</FieldLabel>
          <p className="text-sm text-muted-foreground">{t.nodes[d.type].description}</p>
          {hint && <p className="mt-2 text-xs text-muted-foreground">{hint}</p>}
        </div>

        {!EXECUTABLE.has(d.type) && d.type !== "publish" && (
          <div className="flex items-start gap-2 rounded-lg border border-border bg-surface p-3 text-xs text-muted-foreground">
            <SoonBadge />
            <span>{i.executorSoon}</span>
          </div>
        )}

        {d.type === "video" && (
          <>
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{i.runSettings}</p>
            <VideoFields video={video} idPrefix="inspector" />
            <div>
              <FieldLabel>{i.aspect}</FieldLabel>
              <div className="flex items-center justify-between gap-2 text-sm">
                <span className="rounded-lg border border-primary/60 bg-[color-mix(in_oklab,var(--primary)_16%,transparent)] px-3 py-1.5 text-primary">
                  {aspect}
                </span>
                <Link href="/settings" className="text-xs text-muted-foreground hover:text-foreground">
                  {i.changeInSettings}
                </Link>
              </div>
              <p className="mt-1.5 text-[11px] text-muted-foreground">{i.aspectHint}</p>
            </div>
            {assetId && (
              <div className="overflow-hidden rounded-lg border border-border bg-black">
                <video src={assetUrl(assetId)} controls playsInline preload="metadata" className="max-h-72 w-full" />
              </div>
            )}
          </>
        )}

        {TEXT_NODES.has(d.type) && generatedText && (
          <div>
            <FieldLabel>{i.generatedText}</FieldLabel>
            <div className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-surface p-3 text-xs leading-relaxed">
              {generatedText}
            </div>
          </div>
        )}

        {d.type === "review" && run?.status === "awaiting_review" && (
          <Button className="w-full" onClick={onApprove} disabled={busy}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
            {i.approve}
          </Button>
        )}

        {advanced && (
          <div className="space-y-2 rounded-lg border border-border bg-surface p-3 text-xs">
            <p className="font-medium">{t.editor.advanced}</p>
            {(
              [
                [i.nodeId, node.id],
                [i.inputs, parents.length ? parents.join(", ") : i.noInputs],
                ...(d.type === "video"
                  ? [
                      [i.provider, tool?.provider ?? "—"],
                      [i.modelId, tool?.model ?? "—"],
                      [i.creditCost, readiness ? t.common.credits(String(readiness.credits_required)) : "—"],
                    ]
                  : []),
              ] as [string, string][]
            ).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <span className="text-muted-foreground">{k}</span>
                <span className="min-w-0 break-all text-right">{v}</span>
              </div>
            ))}
            {step?.output && (
              <>
                <p className="pt-1 text-muted-foreground">{i.output}</p>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-background p-2 text-[10px] text-muted-foreground">
                  {JSON.stringify(step.output, null, 2)}
                </pre>
              </>
            )}
          </div>
        )}
      </div>

      <div className="flex gap-2 border-t border-border p-3">
        <Button size="sm" className="flex-1" onClick={onRun}>
          <Play className="size-3.5" /> {i.runWorkflow}
        </Button>
        <Button size="sm" variant="outline" onClick={onDuplicate}>
          {t.editor.node.duplicate}
        </Button>
        <Button size="sm" variant="ghost" className="text-destructive" onClick={onDelete}>
          {t.editor.node.delete}
        </Button>
      </div>
    </aside>
  );
}

function ZoomControls() {
  const { t } = useI18n();
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { zoom } = useViewport();
  return (
    <div className="absolute bottom-4 left-4 z-10 flex items-center gap-0.5 rounded-lg border border-border bg-surface p-0.5 shadow-[var(--shadow-panel)]">
      <Button variant="ghost" size="icon" className="size-7" onClick={() => zoomOut()} aria-label={t.editor.zoomOut}>
        <Minus className="size-3.5" />
      </Button>
      <span className="w-11 text-center text-xs tabular-nums">{Math.round(zoom * 100)}%</span>
      <Button variant="ghost" size="icon" className="size-7" onClick={() => zoomIn()} aria-label={t.editor.zoomIn}>
        <Plus className="size-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="size-7"
        title={t.editor.fit}
        aria-label={t.editor.fit}
        onClick={() => fitView({ padding: 0.2, duration: 300, maxZoom: 1 })}
      >
        <Maximize className="size-3.5" />
      </Button>
    </div>
  );
}

function RunDialog({
  open,
  onOpenChange,
  projects,
  projectId,
  onProject,
  hasVideo,
  video,
  readiness,
  busy,
  onStart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projects: Project[];
  projectId: string;
  onProject: (id: string) => void;
  hasVideo: boolean;
  video: VideoSettings;
  readiness?: Readiness;
  busy: boolean;
  onStart: () => void;
}) {
  const { t } = useI18n();
  const r = t.editor.runDialog;
  const videoStep = readiness?.steps.find((s) => s.task === "video");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{r.title}</DialogTitle>
          <DialogDescription>{r.description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <FieldLabel>{r.project}</FieldLabel>
            {projects.length === 0 ? (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface p-3 text-sm text-muted-foreground">
                {r.noProjects}
                <NewProjectDialog
                  onCreated={(project) => onProject(project.id)}
                  trigger={
                    <Button size="sm" variant="outline">
                      <Plus className="size-3.5" /> {r.createProject}
                    </Button>
                  }
                />
              </div>
            ) : (
              <Select value={projectId} onValueChange={onProject}>
                <SelectTrigger className="bg-surface" aria-label={r.project}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((project) => (
                    <SelectItem key={project.id} value={project.id}>
                      {project.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          {hasVideo && <VideoFields video={video} idPrefix="run" />}
          {readiness && (hasVideo || readiness.credits_required > 0) && (
            <div className="rounded-lg border border-border bg-surface p-3 text-xs text-muted-foreground">
              {hasVideo && videoStep && <p>{readinessText(videoStep, readiness, t)}</p>}
              <p className={cn(hasVideo && videoStep && "mt-1")}>
                {r.credits(readiness.credits_required, readiness.credits_available)}
              </p>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button onClick={onStart} disabled={busy || !projectId}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
            {r.start}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RunsSheet({
  open,
  onOpenChange,
  workflowId,
  selectedId,
  onSelect,
  projects,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  projects: Project[];
}) {
  const { t, formatDateTime } = useI18n();
  const runs = useWorkflowRuns(workflowId);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-md">
        <SheetHeader className="border-b border-border p-4 pr-12 text-left">
          <SheetTitle>{t.editor.runs.title}</SheetTitle>
          <SheetDescription>{t.editor.runs.subtitle}</SheetDescription>
        </SheetHeader>
        <div className="scrollbar-thin flex-1 space-y-2 overflow-y-auto p-3">
          {runs.isPending && <Loader2 className="mx-auto mt-6 size-5 animate-spin text-muted-foreground" />}
          {runs.data?.length === 0 && <p className="p-2 text-sm text-muted-foreground">{t.editor.runs.empty}</p>}
          {runs.data?.map((run) => (
            <button
              key={run.id}
              type="button"
              onClick={() => onSelect(run.id)}
              className={cn(
                "w-full rounded-lg border p-3 text-left transition-colors",
                selectedId === run.id ? "border-primary/50 bg-surface" : "border-border hover:border-border-strong",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 break-words text-sm font-medium">
                  {projects.find((p) => p.id === run.project_id)?.title ?? t.editor.runs.unknownProject}
                </p>
                <StatusBadge status={run.status} label={t.status.run[run.status]} />
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {formatDateTime(run.created_at)}
                {run.retry_of_id ? ` · ${t.editor.runs.retryOf}` : ""}
                {selectedId === run.id ? ` · ${t.editor.runs.viewing}` : ""}
              </p>
            </button>
          ))}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Editor({
  workflow,
  initialRunId,
  initialProjectId,
}: {
  workflow: Workflow;
  initialRunId: string | null;
  initialProjectId: string | null;
}) {
  const { t, formatDateTime } = useI18n();
  const client = useQueryClient();
  const showError = useErrorToast();
  const refreshStudio = useRefreshStudio();
  const { data } = useDashboard();
  const projects = data?.projects ?? [];
  const settings = useSettings().data;
  const videoTools = (useAiTools().data ?? []).filter((tool) => tool.task === "video" && tool.is_enabled);

  const [nodes, setNodes, onNodesChange] = useNodesState<StudioNode>(toNodes(workflow.graph));
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(toEdges(workflow.graph));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [runsOpen, setRunsOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(initialRunId);
  const [projectId, setProjectId] = useState(initialProjectId ?? projects[0]?.id ?? "");
  const [toolId, setToolId] = useState("");
  const [prompt, setPrompt] = useState(() => projects.find((p) => p.id === (initialProjectId ?? projects[0]?.id))?.topic ?? "");
  const { screenToFlowPosition } = useReactFlow();

  const readiness = useReadiness(workflow.id, toolId).data;
  const runQuery = useRun(selectedRunId);
  const run = runQuery.data;

  const past = useRef<Snapshot[]>([]);
  const future = useRef<Snapshot[]>([]);
  const latest = useRef({ nodes, edges });
  latest.current = { nodes, edges };

  // A run that just finished may have produced media and changed credits.
  const previousStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const status = run?.status;
    if (previousStatus.current && ["running", "queued", "submitting"].includes(previousStatus.current) && status && !isActiveRun(run!)) {
      void refreshStudio();
    }
    previousStatus.current = status;
  }, [run, refreshStudio]);

  const snapshot = useCallback(() => {
    past.current.push(structuredClone(latest.current));
    future.current = [];
    setDirty(true);
  }, []);

  const undo = () => {
    const prev = past.current.pop();
    if (!prev) return;
    future.current.push(structuredClone(latest.current));
    setNodes(prev.nodes);
    setEdges(prev.edges);
    setDirty(true);
  };
  const redo = () => {
    const next = future.current.pop();
    if (!next) return;
    past.current.push(structuredClone(latest.current));
    setNodes(next.nodes);
    setEdges(next.edges);
    setDirty(true);
  };

  const addNode = useCallback(
    (type: NodeType, position?: { x: number; y: number }) => {
      if (latest.current.nodes.length >= MAX_NODES) {
        toast.error(t.editor.maxNodes);
        return;
      }
      snapshot();
      const at =
        position ??
        screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
      setNodes((ns) => [
        ...ns.map((n) => ({ ...n, selected: false })),
        { id: newNodeId(), type: "studio", position: at, selected: true, data: { type, status: "idle" } },
      ]);
      setLibraryOpen(false);
    },
    [screenToFlowPosition, setNodes, snapshot, t],
  );

  const actions = useMemo<NodeActions>(
    () => ({
      onDelete: (id) => {
        snapshot();
        setNodes((ns) => ns.filter((n) => n.id !== id));
        setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
      },
      onDuplicate: (id) => {
        if (latest.current.nodes.length >= MAX_NODES) {
          toast.error(t.editor.maxNodes);
          return;
        }
        snapshot();
        setNodes((ns) => {
          const src = ns.find((n) => n.id === id);
          if (!src) return ns;
          const label = `${src.data.label || t.nodes[src.data.type].name}${t.editor.node.copySuffix}`.slice(0, 80);
          return [
            ...ns.map((n) => ({ ...n, selected: false })),
            {
              ...src,
              id: newNodeId(),
              position: { x: src.position.x + 40, y: src.position.y + 60 },
              selected: true,
              data: { type: src.data.type, label, config: src.data.config ?? null, status: "idle" },
            },
          ];
        });
      },
    }),
    [setEdges, setNodes, snapshot, t],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      const { source, target } = connection;
      if (!source || !target || source === target) return;
      const current = latest.current.edges;
      if (current.some((e) => e.source === source && e.target === target)) return;
      // Reject links that would let the target reach back to the source.
      const seen = new Set<string>();
      const reaches = (id: string): boolean => {
        if (id === source) return true;
        if (seen.has(id)) return false;
        seen.add(id);
        return current.filter((e) => e.source === id).some((e) => reaches(e.target));
      };
      if (reaches(target)) {
        toast.error(t.editor.cycle);
        return;
      }
      snapshot();
      setEdges((es) => addEdge({ ...connection, id: `${source}-${target}` }, es));
    },
    [setEdges, snapshot, t],
  );

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData(DRAG_TYPE) as NodeType;
      if (!type || !(type in kindOf)) return;
      addNode(type, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
    },
    [addNode, screenToFlowPosition],
  );

  async function save(): Promise<boolean> {
    setSaving(true);
    try {
      const graph: Graph = {
        nodes: latest.current.nodes.map((n) => ({
          id: n.id,
          type: n.data.type,
          x: Math.round(n.position.x),
          y: Math.round(n.position.y),
          label: n.data.label?.trim() || null,
          config: n.data.config ?? null,
        })),
        edges: latest.current.edges.map((e) => ({ source: e.source, target: e.target })),
      };
      await api(`workflows/${encodeURIComponent(workflow.id)}`, jsonRequest("PUT", graph));
      setDirty(false);
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.dashboard }),
        client.invalidateQueries({ queryKey: ["readiness", workflow.id] }),
      ]);
      toast.success(t.editor.saveSuccess);
      return true;
    } catch (error) {
      showError(error);
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function openRun() {
    if (dirty && !(await save())) return;
    setRunOpen(true);
  }

  async function startRun() {
    setBusy(true);
    try {
      const created = await api<Run>(
        `workflows/${encodeURIComponent(workflow.id)}/runs`,
        jsonRequest("POST", { project_id: projectId, prompt: prompt.trim() || null, tool_id: toolId || null }),
      );
      client.setQueryData(keys.run(created.id), created);
      setSelectedRunId(created.id);
      setRunOpen(false);
      toast.success(t.editor.runDialog.started);
      await refreshStudio();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function runAction(path: string, message: string) {
    if (!run) return;
    setBusy(true);
    try {
      const result = await api<Run>(`workflow-runs/${encodeURIComponent(run.id)}/${path}`, { method: "POST" });
      client.setQueryData(keys.run(result.id), result);
      setSelectedRunId(result.id);
      toast.success(message);
      await refreshStudio();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  const stepById = useMemo(() => new Map((run?.steps ?? []).map((s) => [s.node_id, s])), [run]);
  const readinessById = useMemo(() => new Map((readiness?.steps ?? []).map((s) => [s.node_id, s])), [readiness]);

  const statusOf = useCallback(
    (id: string): NodeStatus => {
      const step = stepById.get(id);
      if (step) return runStatusToNode[step.status];
      if (run) return "idle";
      const ready = readinessById.get(id);
      if (!ready) return "idle";
      return ready.status === "ready" || ready.status === "configured" ? "ready" : "attention";
    },
    [readinessById, run, stepById],
  );

  const displayNodes = useMemo(
    () =>
      nodes.map((n) => ({
        ...n,
        data: { ...n.data, status: statusOf(n.id), output: stepById.get(n.id)?.output ?? null },
      })),
    [nodes, statusOf, stepById],
  );

  const styledEdges = useMemo(
    () =>
      edges.map((e) => {
        const src = statusOf(e.source);
        const live = statusOf(e.target) === "running";
        const done = src === "completed" || src === "review";
        const color = done || live ? "var(--primary)" : "var(--border-strong)";
        return {
          ...e,
          animated: live,
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color },
          style: { stroke: color, strokeWidth: 1.75, opacity: done || live ? 0.85 : 1 },
        };
      }),
    [edges, statusOf],
  );

  const selected = nodes.filter((n) => n.selected);
  const inspected = selected.length === 1 ? displayNodes.find((n) => n.id === selected[0]!.id)! : null;
  const completed = (run?.steps ?? []).filter((s) => s.status === "completed").length;
  const hasVideo = nodes.some((n) => n.data.type === "video");
  const approvedId = approvedAsset(run);
  const orientation = settings?.workspace.video_orientation ?? "vertical";
  const video: VideoSettings = { tools: videoTools, toolId, setToolId, prompt, setPrompt };
  const nodeContext = useMemo(
    () => ({ assetCount: data?.assets.length ?? 0, vertical: orientation !== "horizontal" }),
    [data?.assets.length, orientation],
  );
  const selectProject = (id: string) => {
    setProjectId(id);
    setPrompt(projects.find((p) => p.id === id)?.topic ?? "");
  };

  return (
    <NodeActionsContext.Provider value={actions}>
      <NodeContext.Provider value={nodeContext}>
        <div className="flex h-[calc(100dvh-3.5rem)] flex-col">
          <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-2.5">
            <Link
              href="/workflows"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-3.5" /> {t.editor.back}
            </Link>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{workflow.name}</p>
              <p className="text-[11px] text-muted-foreground">
                {t.editor.subtitle} ·{" "}
                {dirty ? (
                  t.editor.unsaved
                ) : (
                  <span className="inline-flex items-center gap-0.5">
                    <Check className="size-3" /> {t.editor.saved}
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-0.5">
              <Button variant="ghost" size="icon" className="size-8" onClick={undo} title={t.editor.undo} aria-label={t.editor.undo}>
                <Undo2 className="size-4" />
              </Button>
              <Button variant="ghost" size="icon" className="size-8" onClick={redo} title={t.editor.redo} aria-label={t.editor.redo}>
                <Redo2 className="size-4" />
              </Button>
              <Button variant="outline" size="sm" className="ml-1 lg:hidden" onClick={() => setLibraryOpen(true)}>
                <Plus className="size-3.5" /> {t.editor.library.add}
              </Button>
            </div>

            <div className="ml-auto flex flex-wrap items-center gap-2">
              {run ? (
                <span className="inline-flex items-center gap-2 rounded-full bg-[color-mix(in_oklab,var(--primary)_15%,transparent)] px-3 py-1 text-xs text-primary">
                  <span className={cn("size-1.5 rounded-full bg-primary", isActiveRun(run) && "animate-pulse")} />
                  {t.editor.runState(t.status.run[run.status], completed, run.steps?.length ?? nodes.length)}
                </span>
              ) : (
                <span className="text-xs text-muted-foreground">{t.common.steps(nodes.length)}</span>
              )}
              <label className="hidden items-center gap-2 text-xs text-muted-foreground md:flex">
                <Switch checked={advanced} onCheckedChange={setAdvanced} />
                {t.editor.advanced}
              </label>
              <Button variant="outline" size="sm" onClick={() => setRunsOpen(true)}>
                <History className="size-3.5" /> {t.editor.history}
              </Button>
              <Button variant="outline" size="sm" onClick={() => void save()} disabled={!dirty || saving}>
                {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />} {t.editor.save}
              </Button>
              <Button size="sm" onClick={() => void openRun()} disabled={saving}>
                <Play className="size-3.5" /> {t.editor.run}
              </Button>
            </div>
          </header>

          <div className="relative flex min-h-0 flex-1">
            <NodeLibrary onAdd={(type) => addNode(type)} className="hidden lg:flex" />
            <div
              className="relative min-w-0 flex-1 bg-background"
              onDragOver={(e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
              }}
              onDrop={onDrop}
            >
              {run && (
                <div className="absolute inset-x-4 top-3 z-10 mx-auto flex max-w-2xl flex-wrap items-center gap-2 rounded-xl border border-border bg-surface/95 px-3 py-2 text-xs shadow-[var(--shadow-panel)] backdrop-blur">
                  <StatusBadge status={run.status} label={t.status.run[run.status]} />
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {projects.find((p) => p.id === run.project_id)?.title ?? t.editor.runs.unknownProject} ·{" "}
                    {formatDateTime(run.created_at)}
                  </span>
                  {run.status === "awaiting_review" && (
                    <Button size="sm" className="h-7" disabled={busy} onClick={() => void runAction("approve", t.editor.runs.approved)}>
                      <Check className="size-3.5" /> {t.editor.runs.approve}
                    </Button>
                  )}
                  {["blocked", "failed"].includes(run.status) && !approvedId && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7"
                      disabled={busy || dirty}
                      onClick={() => void runAction("retry", t.editor.runs.retried)}
                    >
                      <RotateCcw className="size-3.5" /> {t.editor.runs.retry}
                    </Button>
                  )}
                  {approvedId && (
                    <>
                      <Button asChild size="sm" variant="outline" className="h-7">
                        <a href={assetUrl(approvedId)}>
                          <Download className="size-3.5" /> {t.editor.runs.downloadMp4}
                        </a>
                      </Button>
                      <Button asChild size="sm" className="h-7">
                        <Link href="/publishing">{t.editor.runs.publishOnPublishing}</Link>
                      </Button>
                    </>
                  )}
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-7"
                    onClick={() => setSelectedRunId(null)}
                    aria-label={t.editor.runs.clear}
                    title={t.editor.runs.clear}
                  >
                    <X className="size-3.5" />
                  </Button>
                  {run.status === "needs_attention" && (
                    <p className="w-full text-warning">{t.editor.runs.needsAttention}</p>
                  )}
                </div>
              )}
              <ReactFlow
                colorMode="dark"
                nodes={displayNodes}
                edges={styledEdges}
                nodeTypes={nodeTypes}
                onNodesChange={(changes) => {
                  if (changes.some((c) => c.type === "remove")) snapshot();
                  onNodesChange(changes);
                }}
                onEdgesChange={(changes) => {
                  if (changes.some((c) => c.type === "remove")) snapshot();
                  onEdgesChange(changes);
                }}
                onConnect={onConnect}
                onNodeDragStart={() => snapshot()}
                deleteKeyCode={["Backspace", "Delete"]}
                multiSelectionKeyCode={["Shift", "Meta", "Control"]}
                fitView
                fitViewOptions={{ padding: 0.15, minZoom: 0.6, maxZoom: 1 }}
                minZoom={0.2}
                maxZoom={1.6}
                proOptions={{ hideAttribution: true }}
              >
                <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="var(--border-strong)" />
                <MiniMap
                  pannable
                  zoomable
                  className="!hidden md:!block"
                  nodeColor={(n) => {
                    const s = (n as StudioNode).data.status;
                    return s === "completed"
                      ? "var(--success)"
                      : s === "running"
                        ? "var(--primary)"
                        : s === "review" || s === "blocked" || s === "attention"
                          ? "var(--warning)"
                          : s === "failed"
                            ? "var(--destructive)"
                            : "var(--surface-2)";
                  }}
                />
              </ReactFlow>
              <ZoomControls />
            </div>
            {inspected && (
              <Inspector
                key={inspected.id}
                node={inspected}
                step={stepById.get(inspected.id)}
                readinessStep={run ? undefined : readinessById.get(inspected.id)}
                readiness={readiness}
                run={run}
                parents={edges
                  .filter((e) => e.target === inspected.id)
                  .map((e) => {
                    const parent = nodes.find((n) => n.id === e.source);
                    return parent ? parent.data.label || t.nodes[parent.data.type].name : e.source;
                  })}
                advanced={advanced}
                aspect={ASPECT[orientation]}
                video={video}
                busy={busy}
                onClose={() => setNodes((ns) => ns.map((n) => ({ ...n, selected: false })))}
                onRename={(label) => {
                  setDirty(true);
                  setNodes((ns) => ns.map((n) => (n.id === inspected.id ? { ...n, data: { ...n.data, label } } : n)));
                }}
                onDuplicate={() => actions.onDuplicate(inspected.id)}
                onDelete={() => actions.onDelete(inspected.id)}
                onRun={() => void openRun()}
                onApprove={() => void runAction("approve", t.editor.runs.approved)}
              />
            )}
          </div>
        </div>

        <Sheet open={libraryOpen} onOpenChange={setLibraryOpen}>
          <SheetContent side="left" className="w-72 p-0 [&>button]:hidden">
            <SheetTitle className="sr-only">{t.editor.library.title}</SheetTitle>
            <NodeLibrary onAdd={(type) => addNode(type)} className="flex h-full w-full border-r-0" />
          </SheetContent>
        </Sheet>
        <RunDialog
          open={runOpen}
          onOpenChange={setRunOpen}
          projects={projects}
          projectId={projectId}
          onProject={selectProject}
          hasVideo={hasVideo}
          video={video}
          readiness={readiness}
          busy={busy}
          onStart={() => void startRun()}
        />
        <RunsSheet
          open={runsOpen}
          onOpenChange={setRunsOpen}
          workflowId={workflow.id}
          selectedId={selectedRunId}
          projects={projects}
          onSelect={(id) => {
            setSelectedRunId(id);
            setRunsOpen(false);
          }}
        />
      </NodeContext.Provider>
    </NodeActionsContext.Provider>
  );
}

export function WorkflowEditor(props: {
  workflow: Workflow;
  initialRunId: string | null;
  initialProjectId: string | null;
}) {
  return (
    <ReactFlowProvider>
      <Editor {...props} />
    </ReactFlowProvider>
  );
}
