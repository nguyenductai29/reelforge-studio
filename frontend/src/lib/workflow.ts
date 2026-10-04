import type { Graph, NodeConfig, NodeType } from "./types";
import type { Dictionary } from "./i18n/vi";

/** Visual families from the design; several backend node types share one. */
export type NodeKind =
  | "input"
  | "ai"
  | "script"
  | "image"
  | "video"
  | "voice"
  | "subtitle"
  | "edit"
  | "render"
  | "review"
  | "publish";

export const kindOf: Record<NodeType, NodeKind> = {
  idea: "input",
  assets: "input",
  script: "script",
  scenes: "script",
  image: "image",
  video: "video",
  voice: "voice",
  music: "voice",
  subtitle: "subtitle",
  render: "render",
  review: "review",
  publish: "publish",
  ai_writer: "ai",
  summarize: "ai",
  rewrite: "ai",
  translate: "ai",
  hook: "ai",
  title: "ai",
  metadata: "ai",
  cta: "ai",
  source_text: "input",
  source_url: "input",
  source_media: "input",
  transcribe: "ai",
  story_analysis: "ai",
  recap_script: "script",
  match_scenes: "video",
  extract_clips: "video",
  movie_source: "input",
  movie_prepare: "edit",
  visual_analysis: "ai",
  movie_timeline: "ai",
  review_script: "script",
  clip_select: "video",
};

/** Nodes that generate text with the workspace's text model. */
export const TEXT_NODES: ReadonlySet<NodeType> = new Set([
  "ai_writer",
  "summarize",
  "rewrite",
  "translate",
  "hook",
  "title",
  "cta",
  "metadata",
]);

/** Steps that bring existing content in (Phase 10). */
export const SOURCE_NODES: ReadonlySet<NodeType> = new Set(["source_text", "source_url", "source_media", "transcribe"]);

export const NODE_TYPES = Object.keys(kindOf) as NodeType[];
export const MAX_NODES = 30;

type LibraryItemId = keyof Dictionary["editor"]["library"]["items"];
type CategoryId = keyof Dictionary["editor"]["library"]["categories"];

/**
 * One entry of the step library. Every entry adds a step the backend runs; several entries may add the
 * same node type with preset settings (`config`), e.g. "Short script" is AI Writer with 60 seconds.
 * Features that do not exist yet are not listed at all (see docs/FINAL_PRODUCT_AUDIT.md).
 */
export type LibraryItem = { id: LibraryItemId; kind: NodeKind; type: NodeType; config?: NodeConfig };

export const nodeLibrary: { category: CategoryId; items: LibraryItem[] }[] = [
  {
    category: "input",
    items: [
      { id: "topic", kind: "input", type: "idea" },
      { id: "studioMedia", kind: "input", type: "assets" },
      { id: "textInput", kind: "input", type: "source_text" },
      { id: "url", kind: "input", type: "source_url" },
      { id: "articleUrl", kind: "input", type: "source_url" },
      { id: "uploadVideo", kind: "input", type: "source_media" },
      { id: "uploadImage", kind: "input", type: "source_media" },
      { id: "uploadAudio", kind: "input", type: "source_media" },
      { id: "uploadSubtitle", kind: "input", type: "source_media" },
      { id: "movieSource", kind: "input", type: "movie_source" },
      { id: "transcript", kind: "ai", type: "transcribe" },
    ],
  },
  {
    category: "ai",
    items: [
      { id: "summarize", kind: "ai", type: "summarize" },
      { id: "aiWriter", kind: "ai", type: "ai_writer" },
      { id: "rewrite", kind: "ai", type: "rewrite" },
      { id: "translate", kind: "ai", type: "translate" },
      { id: "generateHook", kind: "ai", type: "hook" },
      { id: "generateTitle", kind: "ai", type: "title" },
      { id: "movieAnalysis", kind: "ai", type: "story_analysis" },
      { id: "keyMoments", kind: "ai", type: "story_analysis" },
      { id: "movieRecap", kind: "script", type: "recap_script" },
      { id: "movieReview", kind: "script", type: "recap_script", config: { style: "review", spoiler_level: "light" } },
      { id: "endingExplained", kind: "script", type: "recap_script", config: { style: "explainer", spoiler_level: "full" } },
      { id: "visualAnalysis", kind: "ai", type: "visual_analysis" },
      { id: "movieTimeline", kind: "ai", type: "movie_timeline" },
      { id: "movieReviewScript", kind: "script", type: "review_script" },
      { id: "movieRecapScript", kind: "script", type: "review_script", config: { mode: "recap", spoiler_level: "full" } },
      { id: "endingExplainedScript", kind: "script", type: "review_script", config: { mode: "ending_explained", spoiler_level: "full" } },
      { id: "generateCta", kind: "ai", type: "cta" },
      { id: "publishMetadata", kind: "ai", type: "metadata" },
    ],
  },
  {
    category: "script",
    items: [
      { id: "scriptWriter", kind: "ai", type: "ai_writer" },
      { id: "shortScript", kind: "ai", type: "ai_writer", config: { duration: 60, platform: "youtube_shorts" } },
      { id: "longScript", kind: "ai", type: "ai_writer", config: { duration: 600, platform: "youtube" } },
      { id: "splitScenes", kind: "script", type: "scenes" },
    ],
  },
  {
    category: "visual",
    items: [
      { id: "imageGenerator", kind: "image", type: "image" },
      { id: "thumbnailGenerator", kind: "image", type: "image", config: { aspect_ratio: "16:9", quality: "high" } },
      { id: "aiVideoGenerator", kind: "video", type: "video" },
      { id: "textToVideo", kind: "video", type: "video" },
      { id: "clipMatcher", kind: "video", type: "match_scenes" },
      { id: "clipSelector", kind: "video", type: "clip_select" },
    ],
  },
  {
    category: "audio",
    items: [
      { id: "textToSpeech", kind: "voice", type: "voice" },
      { id: "backgroundMusic", kind: "voice", type: "music" },
    ],
  },
  {
    category: "editing",
    items: [
      { id: "subtitle", kind: "subtitle", type: "subtitle" },
      { id: "prepareMovie", kind: "edit", type: "movie_prepare" },
      { id: "extractClips", kind: "video", type: "extract_clips" },
      { id: "mergeClips", kind: "render", type: "render" },
      { id: "renderVideo", kind: "render", type: "render" },
    ],
  },
  {
    category: "output",
    items: [
      { id: "review", kind: "review", type: "review" },
      { id: "preview", kind: "review", type: "review" },
      { id: "youtubeVideo", kind: "publish", type: "publish" },
      { id: "youtubeShorts", kind: "publish", type: "publish" },
      { id: "tiktok", kind: "publish", type: "publish" },
      { id: "facebook", kind: "publish", type: "publish" },
      { id: "facebookReels", kind: "publish", type: "publish" },
      { id: "schedulePost", kind: "publish", type: "publish" },
    ],
  },
];

export const libraryItemById = (id: string) =>
  nodeLibrary.flatMap((group) => group.items).find((item) => item.id === id);

/** Node types the backend can execute today; every other step stops a run with a reason. */
export const EXECUTABLE: ReadonlySet<NodeType> = new Set([
  "idea",
  "assets",
  "scenes",
  "image",
  "video",
  "voice",
  "subtitle",
  "render",
  "review",
  "publish",
  ...TEXT_NODES,
  ...SOURCE_NODES,
  "story_analysis",
  "recap_script",
  "match_scenes",
  "extract_clips",
  "music",
  "movie_source",
  "movie_prepare",
  "visual_analysis",
  "movie_timeline",
  "review_script",
  "clip_select",
]);

export type TemplateId = keyof Dictionary["templates"];

export type WorkflowTemplate = {
  id: TemplateId;
  /** Present when the template can be created and run with today's backend. */
  graph?: Graph;
  /** A starter workflow the backend builds (app/workflow/templates.py), with its own node settings. */
  backend?:
    | "youtube_short"
    | "youtube_landscape"
    | "tiktok_short"
    | "facebook_reel"
    | "repurpose"
    | "movie_recap"
    | "movie_review"
    | "movie_source_review"
    | "movie_source_recap"
    | "article_to_video"
    | "product_video";
  /** The compact diagram's node kinds. */
  preview: NodeKind[];
  branches?: number;
  steps: number;
};

const X = 330;
const clipGraph: Graph = {
  nodes: [
    { id: "idea", type: "idea", x: 0, y: 160 },
    { id: "video", type: "video", x: X, y: 160 },
    { id: "review", type: "review", x: X * 2, y: 160 },
  ],
  edges: [
    { source: "idea", target: "video" },
    { source: "video", target: "review" },
  ],
};

// Idea → AI Writer → Scenes → Video + Voice + Subtitle → Render → Review → Publish (with Metadata).
const fullPreview: NodeKind[] = ["input", "ai", "script", "video", "voice", "subtitle", "render", "review", "publish"];
// The same with one AI image per scene instead of a clip (Article to Video, Product Video).
const slidePreview: NodeKind[] = ["input", "ai", "script", "image", "voice", "subtitle", "render", "review", "publish"];
// A movie source prepared once → transcript + described frames → timeline → story → review script → clips → render.
const moviePreview: NodeKind[] = ["input", "edit", "ai", "ai", "script", "voice", "video", "render", "review", "publish"];

export const workflowTemplates: WorkflowTemplate[] = [
  { id: "social-video", graph: clipGraph, preview: ["input", "video", "review"], steps: 3 },
  { id: "youtube-short", backend: "youtube_short", preview: fullPreview, steps: 10 },
  { id: "youtube-video", backend: "youtube_landscape", preview: fullPreview, steps: 10 },
  { id: "tiktok-video", backend: "tiktok_short", preview: fullPreview, steps: 10 },
  { id: "facebook-reel", backend: "facebook_reel", preview: fullPreview, steps: 10 },
  {
    id: "movie-recap",
    backend: "movie_recap",
    preview: ["input", "ai", "script", "voice", "video", "render", "review", "publish"],
    steps: 12,
  },
  {
    id: "movie-review",
    backend: "movie_review",
    preview: ["input", "ai", "script", "voice", "video", "render", "review", "publish"],
    steps: 12,
  },
  { id: "movie-source-review", backend: "movie_source_review", preview: moviePreview, steps: 15 },
  { id: "movie-source-recap", backend: "movie_source_recap", preview: moviePreview, steps: 15 },
  { id: "repurpose", backend: "repurpose", preview: fullPreview, steps: 10 },
  { id: "article-to-video", backend: "article_to_video", preview: slidePreview, steps: 10 },
  { id: "product-video", backend: "product_video", preview: slidePreview, steps: 10 },
  {
    id: "blank",
    graph: { nodes: [{ id: "idea", type: "idea", x: 0, y: 160 }], edges: [] },
    preview: [],
    steps: 0,
  },
];

export const templateById = (id: TemplateId) => workflowTemplates.find((template) => template.id === id)!;

/** Node kinds along the graph in execution order, for the compact diagram. */
export function previewKinds(graph: Graph): NodeKind[] {
  return orderNodes(graph).map((node) => kindOf[node.type]);
}

export function orderNodes(graph: Graph) {
  const indegree = new Map(graph.nodes.map((node) => [node.id, 0]));
  graph.edges.forEach((edge) => indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1));
  const queue = graph.nodes.filter((node) => !indegree.get(node.id)).sort((a, b) => a.x - b.x);
  const order: typeof graph.nodes = [];
  while (queue.length) {
    const node = queue.shift()!;
    order.push(node);
    for (const edge of graph.edges.filter((e) => e.source === node.id)) {
      const left = (indegree.get(edge.target) ?? 0) - 1;
      indegree.set(edge.target, left);
      const target = graph.nodes.find((n) => n.id === edge.target);
      if (left === 0 && target) queue.push(target);
    }
  }
  graph.nodes.forEach((node) => !order.includes(node) && order.push(node));
  return order;
}

export const newNodeId = () => `n${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
