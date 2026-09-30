"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type {
  AdminOverview,
  AiTool,
  Billing,
  Dashboard,
  NodeCatalog,
  Publication,
  Readiness,
  Run,
  Settings,
  Usage,
  YouTubeConnection,
} from "./types";

const ACTIVE_RUN = new Set(["running", "queued", "submitting"]);
const ACTIVE_UPLOAD = new Set(["queued", "uploading"]);
const POLL_MS = 5000;

export const keys = {
  dashboard: ["dashboard"] as const,
  runs: ["runs"] as const,
  workflowRuns: (id: string) => ["workflow-runs", id] as const,
  run: (id: string) => ["run", id] as const,
  readiness: (id: string) => ["readiness", id] as const,
  usage: ["usage"] as const,
  billing: ["billing"] as const,
  aiTools: ["ai-tools"] as const,
  youtube: ["youtube", "connection"] as const,
  publications: ["youtube", "publications"] as const,
  settings: ["settings"] as const,
  admin: ["admin"] as const,
  nodeTypes: ["workflow-node-types"] as const,
};

/** Each node type's ports; fixed for a server release, so fetched once. */
export function useNodeTypes() {
  return useQuery({
    queryKey: keys.nodeTypes,
    queryFn: () => api<NodeCatalog>("workflow-node-types"),
    staleTime: Infinity,
  });
}

export function useDashboard() {
  return useQuery({ queryKey: keys.dashboard, queryFn: () => api<Dashboard>("dashboard") });
}

/** Latest runs across all workflows; polls while any of them is still working. */
export function useRuns() {
  return useQuery({
    queryKey: keys.runs,
    queryFn: () => api<Run[]>("workflow-runs"),
    refetchInterval: (query) => (query.state.data?.some((run) => ACTIVE_RUN.has(run.status)) ? POLL_MS : false),
  });
}

export function useWorkflowRuns(workflowId: string) {
  return useQuery({
    queryKey: keys.workflowRuns(workflowId),
    queryFn: () => api<Run[]>(`workflows/${encodeURIComponent(workflowId)}/runs`),
    refetchInterval: (query) => (query.state.data?.some((run) => ACTIVE_RUN.has(run.status)) ? POLL_MS : false),
  });
}

export function useRun(runId: string | null) {
  return useQuery({
    queryKey: keys.run(runId ?? ""),
    queryFn: () => api<Run>(`workflow-runs/${encodeURIComponent(runId!)}`),
    enabled: Boolean(runId),
    refetchInterval: (query) => (query.state.data && ACTIVE_RUN.has(query.state.data.status) ? POLL_MS : false),
  });
}

/** Pre-run checks of the saved workflow; each node's settings choose its model. */
export function useReadiness(workflowId: string) {
  return useQuery({
    queryKey: keys.readiness(workflowId),
    queryFn: () => api<Readiness>(`workflows/${encodeURIComponent(workflowId)}/readiness`),
  });
}

export function useUsage() {
  return useQuery({ queryKey: keys.usage, queryFn: () => api<Usage>("usage") });
}

export function useBilling() {
  return useQuery({ queryKey: keys.billing, queryFn: () => api<Billing>("billing") });
}

export function useAiTools() {
  return useQuery({ queryKey: keys.aiTools, queryFn: () => api<AiTool[]>("ai-tools") });
}

export function useYouTubeConnection() {
  return useQuery({ queryKey: keys.youtube, queryFn: () => api<YouTubeConnection>("youtube/connection") });
}

export function usePublications() {
  return useQuery({
    queryKey: keys.publications,
    queryFn: () => api<Publication[]>("youtube/publications"),
    refetchInterval: (query) => (query.state.data?.some((p) => ACTIVE_UPLOAD.has(p.state)) ? POLL_MS : false),
  });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api<Settings>("settings") });
}

export function useAdmin(enabled: boolean) {
  return useQuery({ queryKey: keys.admin, queryFn: () => api<AdminOverview>("admin"), enabled });
}

/** Refreshes everything a finished run can change: runs, media, credits. */
export function useRefreshStudio() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys.dashboard }),
      client.invalidateQueries({ queryKey: keys.runs }),
      client.invalidateQueries({ queryKey: ["workflow-runs"] }),
      client.invalidateQueries({ queryKey: ["run"] }),
      client.invalidateQueries({ queryKey: ["readiness"] }),
      client.invalidateQueries({ queryKey: keys.usage }),
    ]);
}
