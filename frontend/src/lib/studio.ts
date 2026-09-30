import type { Asset, Project, Publication, Run, RunStatus } from "./types";

export type ProjectStatus = "draft" | "generating" | "review" | "ready" | "published";

const ACTIVE: RunStatus[] = ["running", "queued", "submitting"];

export const isActiveRun = (run: Run) => ACTIVE.includes(run.status);

/** The backend only stores draft/approved, so the board status is derived from runs and uploads. */
export function projectStatus(project: Project, runs: Run[], publications: Publication[]): ProjectStatus {
  const own = runs.filter((run) => run.project_id === project.id);
  const runIds = new Set(own.map((run) => run.id));
  if (publications.some((p) => runIds.has(p.run_id) && p.state === "succeeded")) return "published";
  if (project.status === "approved") return "ready";
  if (own.some((run) => run.status === "awaiting_review")) return "review";
  if (own.some(isActiveRun)) return "generating";
  return "draft";
}

/** Badge colour for any backend status string. */
export const statusTone: Record<string, string> = {
  draft: "draft",
  generating: "generating",
  review: "review",
  ready: "ready",
  published: "published",
  running: "generating",
  queued: "queued",
  submitting: "processing",
  completed: "completed",
  blocked: "needs attention",
  skipped: "queued",
  failed: "failed",
  needs_attention: "needs attention",
  awaiting_review: "review",
  uploading: "publishing",
  succeeded: "published",
  pending: "scheduled",
  paid: "completed",
  paid_unapplied: "needs attention",
  cancelled: "draft",
  canceled: "draft",
  expired: "failed",
  active: "connected",
  paused: "needs attention",
  unavailable: "draft",
};

const TINTS = [
  "from-[oklch(0.34_0.07_250)] to-[oklch(0.2_0.03_270)]",
  "from-[oklch(0.42_0.13_52)] to-[oklch(0.24_0.05_40)]",
  "from-[oklch(0.3_0.02_280)] to-[oklch(0.18_0.01_280)]",
  "from-[oklch(0.38_0.1_190)] to-[oklch(0.2_0.04_200)]",
  "from-[oklch(0.36_0.09_150)] to-[oklch(0.2_0.03_160)]",
  "from-[oklch(0.36_0.1_330)] to-[oklch(0.2_0.04_330)]",
];

/** A stable gradient per record, standing in for thumbnails the backend does not produce. */
export function tintFor(id: string): string {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return TINTS[Math.abs(hash) % TINTS.length]!;
}

export type AssetKind = "video" | "image" | "audio" | "other";

export function assetKind(contentType: string): AssetKind {
  if (contentType.startsWith("video/")) return "video";
  if (contentType.startsWith("image/")) return "image";
  if (contentType.startsWith("audio/")) return "audio";
  return "other";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

/** Workflow videos are saved as MP4 assets that remember the run that made them. */
export const runVideos = (assets: Asset[]) =>
  assets.filter((asset) => asset.run_id && asset.content_type === "video/mp4");

export const ACCEPTED_UPLOADS = [
  "image/jpeg",
  "image/png",
  "image/webp",
  "video/mp4",
  "video/webm",
  "audio/mpeg",
  "audio/wav",
  "audio/ogg",
];
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

/** Approval is recorded on the review step; only approved clips may be published. */
/** The approved clip to download or publish: a single clip, or the first scene's clip of a multi-scene step. */
export function approvedAsset(run: Run | undefined): string | null {
  const review = run?.steps?.find((s) => s.node_type === "review");
  if (review?.status !== "completed" || typeof review.output?.approved_by !== "string") return null;
  for (const step of run?.steps ?? []) {
    if (step.node_type !== "video" || step.status !== "completed") continue;
    if (typeof step.output?.asset_id === "string") return step.output.asset_id;
    const clips = step.output?.video_assets;
    const first = Array.isArray(clips) ? clips.find((clip) => typeof clip?.id === "string") : undefined;
    if (first) return first.id as string;
  }
  return null;
}
