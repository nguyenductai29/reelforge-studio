"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type {
  AdminJobs,
  AdminOverview,
  AdminPayment,
  AdminStorage,
  AdminUser,
  AdminWorkspace,
  AiTool,
  Billing,
  ChannelStatus,
  Dashboard,
  DefaultModels,
  Order,
  Page,
  NodeCatalog,
  Publication,
  Readiness,
  ReconciliationPage,
  Run,
  RunSummary,
  ScriptItem,
  Settings,
  StorageUsage,
  Usage,
  WorkerHealth,
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
  runSummary: (id: string) => ["run", id, "summary"] as const,
  readiness: (id: string) => ["readiness", id] as const,
  usage: ["usage"] as const,
  billing: ["billing"] as const,
  aiTools: ["ai-tools"] as const,
  youtube: ["youtube", "connection"] as const,
  publications: ["publications"] as const,
  channels: ["channels"] as const,
  defaultModels: ["settings", "default-models"] as const,
  storage: ["storage"] as const,
  adminWorkers: ["admin", "workers"] as const,
  adminJobs: ["admin", "jobs"] as const,
  adminStorage: ["admin", "storage"] as const,
  adminUsers: ["admin", "users"] as const,
  adminWorkspaces: ["admin", "workspaces"] as const,
  adminPayments: ["admin", "payments"] as const,
  billingOrders: ["billing", "orders"] as const,
  scripts: ["scripts"] as const,
  settings: ["settings"] as const,
  admin: ["admin"] as const,
  reconciliation: ["admin", "reconciliation"] as const,
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

/** A run's progress, results, credits and publishing state; polled with the run while it is active. */
export function useRunSummary(runId: string | null, active: boolean) {
  return useQuery({
    queryKey: keys.runSummary(runId ?? ""),
    queryFn: () => api<RunSummary>(`workflow-runs/${encodeURIComponent(runId!)}/summary`),
    enabled: Boolean(runId),
    refetchInterval: active ? POLL_MS : false,
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

/** Publications of every channel (YouTube, TikTok, Facebook), newest first; polled while uploads run. */
export function usePublications() {
  return useQuery({
    queryKey: keys.publications,
    queryFn: () => api<{ publications: Publication[] }>("publications").then((data) => data.publications),
    refetchInterval: (query) => (query.state.data?.some((p) => ACTIVE_UPLOAD.has(p.state)) ? POLL_MS : false),
  });
}

/** YouTube, TikTok and Facebook with their connection status; never a token. */
export function useChannels() {
  return useQuery({
    queryKey: keys.channels,
    queryFn: () => api<{ channels: ChannelStatus[] }>("channels").then((data) => data.channels),
  });
}

export function useDefaultModels() {
  return useQuery({
    queryKey: keys.defaultModels,
    queryFn: () => api<{ default_models: DefaultModels }>("settings/default-models").then((data) => data.default_models),
  });
}

export function useStorage() {
  return useQuery({ queryKey: keys.storage, queryFn: () => api<StorageUsage>("storage") });
}

export function useAdminWorkers(enabled: boolean) {
  return useQuery({
    queryKey: keys.adminWorkers,
    queryFn: () => api<{ workers: WorkerHealth[]; stale_after_seconds: number }>("admin/workers"),
    enabled,
    refetchInterval: 30_000,
  });
}

/** One page of an admin collection; empty filters are left out of the query string. */
function adminPage<T>(endpoint: string, params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  return api<T>(`${endpoint}?${search}`);
}

export function useAdminJobs(enabled: boolean, state: string, queue: string, offset = 0, limit = 50) {
  return useQuery({
    queryKey: [...keys.adminJobs, state, queue, offset, limit],
    queryFn: () => adminPage<AdminJobs>("admin/jobs", { state, queue, limit, offset }),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export type AdminFilters = Record<string, string | number | undefined>;

export function useAdminUsers(filters: AdminFilters) {
  return useQuery({
    queryKey: [...keys.adminUsers, filters],
    queryFn: () => adminPage<Page<AdminUser>>("admin/users", filters),
    placeholderData: (previous) => previous,
  });
}

export function useAdminWorkspaces(filters: AdminFilters) {
  return useQuery({
    queryKey: [...keys.adminWorkspaces, filters],
    queryFn: () => adminPage<Page<AdminWorkspace>>("admin/workspaces", filters),
    placeholderData: (previous) => previous,
  });
}

export function useAdminPayments(filters: AdminFilters) {
  return useQuery({
    queryKey: [...keys.adminPayments, filters],
    queryFn: () => adminPage<Page<AdminPayment>>("admin/payments", filters),
    placeholderData: (previous) => previous,
  });
}

/** The workspace's payment history, one page at a time. */
export function useBillingOrders(offset: number, limit = 10) {
  return useQuery({
    queryKey: [...keys.billingOrders, offset, limit],
    queryFn: () => adminPage<Page<Order>>("billing/orders", { limit, offset }),
    placeholderData: (previous) => previous,
  });
}

/** Text that finished runs wrote; for one project when ``projectId`` is given. */
export function useScripts(offset: number, projectId?: string, limit = 20) {
  return useQuery({
    queryKey: [...keys.scripts, projectId ?? "", offset, limit],
    queryFn: () => adminPage<Page<ScriptItem>>("scripts", { project_id: projectId, limit, offset }),
    placeholderData: (previous) => previous,
  });
}

/** One page of every channel's publications, for the Publishing queue. */
export function usePublicationsPage(offset: number, limit = 20) {
  return useQuery({
    queryKey: [...keys.publications, "page", offset, limit],
    queryFn: () =>
      adminPage<{ publications: Publication[]; total: number }>("publications", { limit, offset }),
    placeholderData: (previous) => previous,
    refetchInterval: (query) => (query.state.data?.publications.some((p) => ACTIVE_UPLOAD.has(p.state)) ? POLL_MS : false),
  });
}

/** Publications whose calendar time falls in ``[start, end)`` (UTC ISO). */
export function useCalendarPublications(start: string, end: string) {
  return useQuery({
    queryKey: [...keys.publications, "range", start, end],
    queryFn: () =>
      adminPage<{ publications: Publication[] }>("publications", { start, end, limit: 300 }).then((data) => data.publications),
    placeholderData: (previous) => previous,
  });
}

export function useAdminStorage(enabled: boolean) {
  return useQuery({ queryKey: keys.adminStorage, queryFn: () => api<AdminStorage>("admin/storage"), enabled });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api<Settings>("settings") });
}

export function useAdmin(enabled: boolean) {
  return useQuery({ queryKey: keys.admin, queryFn: () => api<AdminOverview>("admin"), enabled });
}

export function useReconciliation(status: "pending" | "resolved", offset: number, limit = 50) {
  return useQuery({
    queryKey: [...keys.reconciliation, status, offset, limit],
    queryFn: () => api<ReconciliationPage>(`admin/reconciliation?status=${status}&limit=${limit}&offset=${offset}`),
  });
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
