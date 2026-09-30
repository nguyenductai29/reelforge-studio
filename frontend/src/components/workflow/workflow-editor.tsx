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
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
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
import { api, ApiError, assetUrl, jsonRequest } from "@/lib/api";
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
import type {
  AiTool,
  Graph,
  NodeConfig,
  NodePorts,
  NodeType,
  Project,
  Readiness,
  ReadinessStep,
  Run,
  RunStep,
  Workflow,
} from "@/lib/types";
import { EXECUTABLE, MAX_NODES, TEXT_NODES, kindOf, newNodeId, nodeLibrary } from "@/lib/workflow";
import { kindIcon } from "./kind-icon";
import { ConfigFields, type ConfigChange, type WorkspaceDefaults } from "./config-fields";
import { blocksSaving, configErrors, toolChoices, withValue } from "./node-config";
import { canConnect, edgeId, outputScenes, outputText, portLabel, portsOf, type PortCatalog } from "./ports";
import { NodeActionsContext, NodeContext, StudioNodeComponent, type NodeActions } from "./studio-node";
import { detailText, readinessText, runStatusToNode, type NodeStatus, type StudioNode } from "./types";

const nodeTypes = { studio: StudioNodeComponent };
const DRAG_TYPE = "application/reelforge";
// Aspect ratio a video node set to "auto" gets from the workspace; square has no supported model yet.
const WORKSPACE_ASPECT = { vertical: "9:16", horizontal: "16:9", square: null } as const;

type Snapshot = { nodes: StudioNode[]; edges: Edge[] };

const toNodes = (graph: Graph): StudioNode[] =>
  graph.nodes.map((node) => ({
    id: node.id,
    type: "studio",
    position: { x: node.x, y: node.y },
    // Settings are kept as-is so saving the canvas never drops them.
    data: { type: node.type, label: node.label ?? undefined, config: node.config ?? null, status: "idle" },
  }));

// The API names the ports of older edges too, so almost every edge arrives with its handles.
const toEdges = (graph: Graph): Edge[] =>
  graph.edges.map((edge) => ({
    id: edgeId(edge),
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle ?? null,
    targetHandle: edge.targetHandle ?? null,
  }));

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

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-t border-border pt-4">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground/80">{title}</p>
      {children}
    </section>
  );
}

/** An edge into the inspected node: the input it feeds, and where it comes from ("AI Writer · Script"). */
type Incoming = { handle: string | null; from: string };

function Inspector({
  node,
  step,
  readinessStep,
  readiness,
  run,
  incoming,
  ports,
  advanced,
  tools,
  errors,
  defaults,
  busy,
  onClose,
  onRename,
  onConfig,
  onRemoveSetting,
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
  incoming: Incoming[];
  ports: NodePorts;
  advanced: boolean;
  tools: AiTool[] | undefined;
  errors: Record<string, string>;
  defaults: WorkspaceDefaults;
  busy: boolean;
  onClose: () => void;
  onRename: (label: string) => void;
  onConfig: ConfigChange;
  onRemoveSetting: (key: string) => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onRun: () => void;
  onApprove: () => void;
}) {
  const { t } = useI18n();
  const i = t.editor.inspector;
  const d = node.data;
  const name = t.nodes[d.type].name;
  // Readiness describes the saved workflow; unsaved settings the backend would reject come first.
  const detail = step
    ? detailText(step.detail, t)
    : blocksSaving(errors)
      ? t.readiness.invalidSettings
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
              : d.type === "scenes"
                ? i.scenesHint
                : null;
  const assetId = typeof step?.output?.asset_id === "string" ? step.output.asset_id : null;
  const generatedText = TEXT_NODES.has(d.type) ? outputText(step?.output, ports) : null;
  const scenes = d.type === "scenes" ? outputScenes(step?.output) : null;
  const hasResult = Boolean(assetId || generatedText || (scenes && scenes.length));
  // The model this node will use: its own setting, else the first enabled model for the task.
  const toolField = ports.config.find((field) => field.type === "tool");
  const toolId = toolField ? d.config?.[toolField.key] : undefined;
  const tool = toolField
    ? typeof toolId === "string"
      ? tools?.find((item) => item.id === toolId)
      : toolChoices(toolField, tools ?? [])[0]
    : undefined;
  const used =
    typeof step?.output?.provider === "string" && typeof step.output.model === "string"
      ? `${step.output.provider} · ${step.output.model}`
      : null;

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

        <Section title={i.connections}>
          {ports.inputs.length === 0 && ports.outputs.length === 0 ? (
            <p className="text-xs text-muted-foreground">{i.noPorts}</p>
          ) : (
            <div className="space-y-3 rounded-lg border border-border bg-surface p-3 text-xs">
              {(
                [
                  [i.inputsTitle, ports.inputs.map((port) => port.name)],
                  [i.outputsTitle, ports.outputs.map((port) => port.name)],
                ] as const
              ).map(([title, names], group) =>
                names.length ? (
                  <div key={title}>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
                    <ul className="space-y-1">
                      {names.map((port) => {
                        const sources = group === 0 ? incoming.filter((e) => e.handle === port).map((e) => e.from) : [];
                        return (
                          <li key={port} className="flex justify-between gap-3">
                            <span className="shrink-0">
                              {portLabel(t, port)}
                              {advanced && <span className="ml-1 font-mono text-muted-foreground">{port}</span>}
                            </span>
                            {group === 0 && (
                              <span className={cn("min-w-0 break-words text-right", !sources.length && "text-muted-foreground")}>
                                {sources.length ? sources.join(", ") : i.notConnected}
                              </span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null,
              )}
            </div>
          )}
        </Section>

        {(EXECUTABLE.has(d.type) || ports.config.length > 0) && (
          <Section title={i.settings}>
            {ports.config.length ? (
              <>
                <ConfigFields
                  nodeId={node.id}
                  fields={ports.config}
                  config={d.config}
                  errors={errors}
                  tools={tools}
                  advanced={advanced}
                  defaults={defaults}
                  onChange={onConfig}
                  onRemoveKey={onRemoveSetting}
                />
                <p className="text-[11px] text-muted-foreground">{i.settingsHint}</p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">{i.noSettings}</p>
            )}
          </Section>
        )}

        {hasResult && (
          <Section title={i.result}>
            {assetId && (
              <div className="overflow-hidden rounded-lg border border-border bg-black">
                <video src={assetUrl(assetId)} controls playsInline preload="metadata" className="max-h-72 w-full" />
              </div>
            )}
            {generatedText && (
              <div>
                <FieldLabel>{i.generatedText}</FieldLabel>
                <div className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-surface p-3 text-xs leading-relaxed">
                  {generatedText}
                </div>
              </div>
            )}
            {scenes && scenes.length > 0 && (
              <div>
                <FieldLabel>{i.scenesTitle}</FieldLabel>
                <ol className="max-h-72 space-y-2 overflow-y-auto rounded-lg border border-border bg-surface p-3 text-xs leading-relaxed">
                  {scenes.map((scene) => (
                    <li key={scene.index} className="flex gap-2">
                      <span className="shrink-0 font-medium text-primary">{scene.index}</span>
                      <span className="min-w-0 break-words">
                        {scene.text}
                        {scene.duration ? <span className="text-muted-foreground"> · {i.seconds(scene.duration)}</span> : null}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </Section>
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
                ...(toolField
                  ? [
                      [i.provider, tool?.provider ?? "—"],
                      [i.modelId, tool?.model ?? "—"],
                    ]
                  : []),
                ...(used ? [[i.usedModel, used]] : []),
                ...(d.type === "video"
                  ? [[i.creditCost, readiness ? t.common.credits(String(readiness.credits_required)) : "—"]]
                  : []),
              ] as [string, string][]
            ).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <span className="text-muted-foreground">{k}</span>
                <span className="min-w-0 break-all text-right">{v}</span>
              </div>
            ))}
            {d.config && (
              <>
                <p className="pt-1 text-muted-foreground">{i.settings} · JSON</p>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-background p-2 text-[10px] text-muted-foreground">
                  {JSON.stringify(d.config, null, 2)}
                </pre>
              </>
            )}
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
  issues,
  readiness,
  busy,
  onStart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projects: Project[];
  projectId: string;
  onProject: (id: string) => void;
  /** Pre-run problems of the saved workflow, one line per step. */
  issues: string[];
  readiness?: Readiness;
  busy: boolean;
  onStart: () => void;
}) {
  const { t } = useI18n();
  const r = t.editor.runDialog;
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
          {issues.length > 0 && (
            <div className="rounded-lg border border-warning/40 bg-[color-mix(in_oklab,var(--warning)_10%,transparent)] p-3 text-xs">
              <p className="mb-1 font-medium">{r.issues}</p>
              <ul className="space-y-1 text-muted-foreground">
                {issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </div>
          )}
          {readiness && readiness.credits_required > 0 && (
            <p className="rounded-lg border border-border bg-surface p-3 text-xs text-muted-foreground">
              {r.credits(readiness.credits_required, readiness.credits_available)}
            </p>
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
  catalog,
  initialRunId,
  initialProjectId,
}: {
  workflow: Workflow;
  catalog: PortCatalog;
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
  // Every workspace tool, for the model fields; undefined while loading (model checks then wait).
  const tools = useAiTools().data;

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
  const { screenToFlowPosition } = useReactFlow();

  const readiness = useReadiness(workflow.id).data;
  const runQuery = useRun(selectedRunId);
  const run = runQuery.data;

  const past = useRef<Snapshot[]>([]);
  const future = useRef<Snapshot[]>([]);
  const latest = useRef({ nodes, edges });
  latest.current = { nodes, edges };
  // The node setting being typed into; its keystrokes share one undo step.
  const typing = useRef<string | null>(null);

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
    typing.current = null;
    setDirty(true);
  }, []);

  const undo = () => {
    const prev = past.current.pop();
    if (!prev) return;
    typing.current = null;
    future.current.push(structuredClone(latest.current));
    setNodes(prev.nodes);
    setEdges(prev.edges);
    setDirty(true);
  };
  const redo = () => {
    const next = future.current.pop();
    if (!next) return;
    typing.current = null;
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

  // Settings only change in memory; the Save button sends them with the rest of the graph.
  const setConfig = useCallback(
    (id: string, update: (config: NodeConfig | null | undefined) => NodeConfig | null, typingKey: string | null) => {
      if (typingKey === null || typing.current !== typingKey) snapshot();
      typing.current = typingKey;
      setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, config: update(n.data.config) } } : n)));
    },
    [setNodes, snapshot],
  );

  const typeOf = useCallback(
    (id: string | null | undefined) => latest.current.nodes.find((n) => n.id === id)?.data.type,
    [],
  );

  // Checked while dragging, so incompatible ports never light up as drop targets.
  const isValidConnection = useCallback(
    (connection: Edge | Connection) =>
      connection.source !== connection.target &&
      canConnect(catalog, typeOf(connection.source), connection.sourceHandle, typeOf(connection.target), connection.targetHandle),
    [catalog, typeOf],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      const { source, target, sourceHandle, targetHandle } = connection;
      if (!source || !target || source === target) return;
      const current = latest.current.edges;
      if (
        current.some(
          (e) =>
            e.source === source && e.target === target && e.sourceHandle === sourceHandle && e.targetHandle === targetHandle,
        )
      )
        return;
      if (!canConnect(catalog, typeOf(source), sourceHandle, typeOf(target), targetHandle)) {
        toast.error(t.editor.incompatible);
        return;
      }
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
      const input = portsOf(catalog, typeOf(target)).inputs.find((port) => port.name === targetHandle);
      setEdges((es) =>
        addEdge(
          { ...connection, id: edgeId(connection) },
          // An input that takes one connection swaps the old one for the new one.
          input && !input.multiple ? es.filter((e) => !(e.target === target && e.targetHandle === targetHandle)) : es,
        ),
      );
    },
    [catalog, setEdges, snapshot, t, typeOf],
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

  const selectNode = (id: string) => setNodes((ns) => ns.map((n) => ({ ...n, selected: n.id === id })));

  async function save(): Promise<boolean> {
    // The backend rejects invalid settings too; checking here points at the step to fix.
    const invalid = latest.current.nodes.find((n) =>
      blocksSaving(configErrors(portsOf(catalog, n.data.type).config, n.data.config, tools)),
    );
    if (invalid) {
      toast.error(t.editor.invalidSettings(invalid.data.label || t.nodes[invalid.data.type].name));
      selectNode(invalid.id);
      return false;
    }
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
        edges: latest.current.edges.map((e) => ({
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle ?? null,
          targetHandle: e.targetHandle ?? null,
        })),
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
      const nodeId = error instanceof ApiError ? error.error?.nodeId : null;
      if (nodeId) selectNode(nodeId);
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
        jsonRequest("POST", { project_id: projectId }),
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
  const errorsById = useMemo(
    () => new Map(nodes.map((n) => [n.id, configErrors(portsOf(catalog, n.data.type).config, n.data.config, tools)])),
    [catalog, nodes, tools],
  );

  const statusOf = useCallback(
    (id: string): NodeStatus => {
      const step = stepById.get(id);
      if (step) return runStatusToNode[step.status];
      if (run) return "idle";
      // Unsaved settings the backend would reject show before readiness catches up.
      if (blocksSaving(errorsById.get(id) ?? {})) return "attention";
      const ready = readinessById.get(id);
      if (!ready) return "idle";
      return ready.status === "ready" || ready.status === "configured" ? "ready" : "attention";
    },
    [errorsById, readinessById, run, stepById],
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
  const approvedId = approvedAsset(run);
  const orientation = settings?.workspace.video_orientation ?? "vertical";
  const defaults: WorkspaceDefaults = {
    language: settings?.workspace.default_language ?? "vi",
    aspect: WORKSPACE_ASPECT[orientation],
  };
  const nodeName = (id: string) => {
    const found = nodes.find((n) => n.id === id);
    return found ? found.data.label || t.nodes[found.data.type].name : id;
  };
  const issues =
    readiness?.steps
      .filter((s) => s.status !== "ready" && s.status !== "configured")
      .map((s) => `${nodeName(s.node_id)}: ${readinessText(s, readiness, t)}`) ?? [];
  const nodeContext = useMemo(
    () => ({ assetCount: data?.assets.length ?? 0, vertical: orientation !== "horizontal", catalog }),
    [catalog, data?.assets.length, orientation],
  );

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
                isValidConnection={isValidConnection}
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
                incoming={edges
                  .filter((e) => e.target === inspected.id)
                  .map((e) => ({
                    handle: e.targetHandle ?? null,
                    from: e.sourceHandle ? `${nodeName(e.source)} · ${portLabel(t, e.sourceHandle)}` : nodeName(e.source),
                  }))}
                ports={portsOf(catalog, inspected.data.type)}
                advanced={advanced}
                tools={tools}
                errors={errorsById.get(inspected.id) ?? {}}
                defaults={defaults}
                busy={busy}
                onClose={() => setNodes((ns) => ns.map((n) => ({ ...n, selected: false })))}
                onRename={(label) => {
                  setDirty(true);
                  setNodes((ns) => ns.map((n) => (n.id === inspected.id ? { ...n, data: { ...n.data, label } } : n)));
                }}
                onConfig={(field, value, keystroke) =>
                  setConfig(
                    inspected.id,
                    (config) => withValue(config, field, value),
                    keystroke ? `${inspected.id}:${field.key}` : null,
                  )
                }
                onRemoveSetting={(key) =>
                  setConfig(
                    inspected.id,
                    (config) => {
                      const next = { ...(config ?? {}) };
                      delete next[key];
                      return Object.keys(next).length ? next : null;
                    },
                    null,
                  )
                }
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
          onProject={setProjectId}
          issues={issues}
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
  catalog: PortCatalog;
  initialRunId: string | null;
  initialProjectId: string | null;
}) {
  return (
    <ReactFlowProvider>
      <Editor {...props} />
    </ReactFlowProvider>
  );
}
