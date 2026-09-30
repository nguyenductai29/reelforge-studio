import type { Dictionary } from "@/lib/i18n/vi";
import type { GraphEdge, NodePorts } from "@/lib/types";

export type PortCatalog = Record<string, NodePorts>;

export const NO_PORTS: NodePorts = { inputs: [], outputs: [], requires: [], config: [] };

/** A node type's ports and settings; an API from before Phase 3.5 sends no `config`, which reads as none. */
export const portsOf = (catalog: PortCatalog, type: string | undefined): NodePorts => {
  const ports = type ? catalog[type] : undefined;
  return ports ? { ...NO_PORTS, ...ports } : NO_PORTS;
};

export const portLabel = (t: Dictionary, name: string | null | undefined) => (name ? (t.ports[name] ?? name) : "");

/** One edge per port pair; two nodes may be joined through several ports. */
export const edgeId = (edge: GraphEdge) =>
  `${edge.source}:${edge.sourceHandle ?? ""}->${edge.target}:${edge.targetHandle ?? ""}`;

/** Whether an output can feed an input, using the same data types the backend checks. */
export function canConnect(
  catalog: PortCatalog,
  sourceType: string | undefined,
  sourceHandle: string | null | undefined,
  targetType: string | undefined,
  targetHandle: string | null | undefined,
) {
  const output = portsOf(catalog, sourceType).outputs.find((port) => port.name === sourceHandle);
  const input = portsOf(catalog, targetType).inputs.find((port) => port.name === targetHandle);
  return Boolean(output && input && input.accepts.includes(output.type));
}

/** The text a step produced: all of it when the node keeps a "text" copy, else its main output. */
export function outputText(output: Record<string, unknown> | null | undefined, ports: NodePorts): string | null {
  if (!output) return null;
  for (const key of ["text", ports.outputs[0]?.name]) {
    const value = key ? output[key] : undefined;
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

export type Scene = { index: number; text: string; duration?: number | null };

export function outputScenes(output: Record<string, unknown> | null | undefined): Scene[] | null {
  const scenes = output?.scenes;
  if (!Array.isArray(scenes)) return null;
  return scenes.filter((scene): scene is Scene => typeof scene?.text === "string");
}

/** One stored file of an image, video or voice step; `scene_index` is null for prompt-mode files and single clips. */
export type MediaAsset = {
  id: string;
  filename?: string;
  content_type?: string;
  provider?: string;
  model?: string;
  scene_index?: number | null;
  duration?: number | null;
  width?: number;
  height?: number;
};

/** The files a step stored, in scene order. A single clip from before scene mode only has `asset_id`. */
export function outputMedia(
  output: Record<string, unknown> | null | undefined,
  key: "image_assets" | "video_assets" | "audio_assets",
): MediaAsset[] {
  const list = output?.[key];
  const assets = Array.isArray(list)
    ? list.filter((item): item is MediaAsset => typeof item?.id === "string")
    : [];
  if (!assets.length && key === "video_assets" && typeof output?.asset_id === "string") {
    return [{ id: output.asset_id, filename: typeof output.filename === "string" ? output.filename : undefined }];
  }
  return assets;
}

export type SubtitleCue = { start: number; end: number; text: string; scene_index?: number | null };
export type SubtitleFile = {
  id: string;
  filename?: string;
  format?: string;
  cue_count?: number;
  duration?: number;
  timing?: "audio" | "video" | "scenes" | "estimate";
  cues: SubtitleCue[];
};

/** The subtitle file a Subtitle step stored, with its cues for previewing. */
export function outputSubtitle(output: Record<string, unknown> | null | undefined): SubtitleFile | null {
  const value = output?.subtitle_asset as Record<string, unknown> | undefined;
  if (!value || typeof value.id !== "string") return null;
  const cues = Array.isArray(value.cues)
    ? value.cues.filter((cue): cue is SubtitleCue => typeof cue?.text === "string" && typeof cue?.start === "number")
    : [];
  return { ...(value as Omit<SubtitleFile, "cues">), id: value.id, cues };
}

/** "1:05.2", for cue times. */
export function clockTime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(1).padStart(4, "0")}`;
}

export type JobState = "queued" | "submitting" | "running" | "succeeded" | "failed" | "needs_attention";
export type JobRecord = { scene_index?: number | null; operation?: string; status?: JobState; error?: { category?: string } };

/** The media a Review step is showing: the files its parents produced, found in the run's step outputs. */
export function reviewMedia(
  steps: { output?: Record<string, unknown> | null }[] | undefined,
  ids: unknown,
): (MediaAsset & { kind: "video" | "image" })[] {
  const wanted = new Set(Array.isArray(ids) ? ids.filter((id): id is string => typeof id === "string") : []);
  const found = new Map<string, MediaAsset & { kind: "video" | "image" }>();
  for (const step of steps ?? []) {
    for (const [key, kind] of [["video_assets", "video"], ["image_assets", "image"]] as const) {
      for (const asset of outputMedia(step.output, key)) {
        if (wanted.has(asset.id) && !found.has(asset.id)) found.set(asset.id, { ...asset, kind });
      }
    }
  }
  return [...wanted].flatMap((id) => (found.has(id) ? [found.get(id)!] : []));
}

/** Per-job progress of a step that makes one file per scene or image; null for single-job steps. */
export function outputJobs(output: Record<string, unknown> | null | undefined) {
  const jobs = output?.jobs;
  const expected = typeof output?.expected === "number" ? output.expected : null;
  if (!expected || (jobs !== undefined && (typeof jobs !== "object" || jobs === null))) return null;
  const records = Object.values((jobs as Record<string, JobRecord>) ?? {}).sort(
    (a, b) => (a.scene_index ?? 0) - (b.scene_index ?? 0) || (a.operation ?? "").localeCompare(b.operation ?? ""),
  );
  const done = records.filter((record) => record.status === "succeeded").length;
  return { expected, records, done };
}
