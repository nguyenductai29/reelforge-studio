import type { Node } from "@xyflow/react";
import type { Dictionary } from "@/lib/i18n/vi";
import type { NodeConfig, NodeType, Readiness, ReadinessStep, RunStatus } from "@/lib/types";

export type NodeStatus = keyof Dictionary["status"]["node"];

export type StudioNodeData = {
  type: NodeType;
  label?: string;
  config?: NodeConfig | null;
  status: NodeStatus;
  output?: Record<string, unknown> | null;
  [key: string]: unknown;
};

export type StudioNode = Node<StudioNodeData, "studio">;

export const runStatusToNode: Record<RunStatus, NodeStatus> = {
  completed: "completed",
  running: "running",
  submitting: "running",
  queued: "queued",
  awaiting_review: "review",
  blocked: "blocked",
  skipped: "skipped",
  failed: "failed",
  needs_attention: "failed",
};

const AI_TASKS = new Set(["script", "image", "video", "voice", "music"]);

/** Readiness comes back with Vietnamese details; translate from its status code instead. */
export function readinessText(step: ReadinessStep, readiness: Readiness, t: Dictionary): string {
  const r = t.readiness;
  switch (step.status) {
    case "ready":
      if (step.code === "per_scene") return r.readyPerScene(step.credits ?? 0);
      if (step.task === "image") return r.readyImage(step.credits ?? 0);
      return step.task === "video" ? r.ready(step.credits ?? readiness.credits_required) : r.readyText;
    case "configured":
      return r.configured;
    case "missing_tool":
      return step.task === "video" ? r.missingVideoTool : step.task === "image" ? r.missingImageTool : r.missingTool;
    case "needs_connection":
      return AI_TASKS.has(step.task) ? r.needsProvider : r.needsService;
    case "unsupported_graph":
      return r.unsupportedGraph;
    case "unsupported_aspect":
      return r.unsupportedAspect;
    case "unsupported_model":
      return r.unsupportedModel;
    case "insufficient_credits":
      return r.insufficientCredits(readiness.credits_required, readiness.credits_available);
    case "experimental_disabled":
      return r.experimentalDisabled;
    case "missing_key":
      return r.missingKey;
    case "missing_config":
      return r.missingConfig;
    case "invalid_config":
      return r.invalidConfig;
    case "invalid_settings":
      return (step.code && t.config.errors[step.code]) || r.invalidSettings;
    case "missing_input":
      return r.missingInput(step.field ? (t.ports[step.field] ?? step.field) : "");
    case "tool_unavailable":
      return r.toolUnavailable;
    default:
      return step.detail;
  }
}

export const detailText = (detail: string, t: Dictionary) => t.details[detail] ?? detail;
