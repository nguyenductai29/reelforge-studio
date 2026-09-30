import type { Graph, NodeType } from "./types";
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
};

export const NODE_TYPES = Object.keys(kindOf) as NodeType[];
export const MAX_NODES = 30;

type LibraryItemId = keyof Dictionary["editor"]["library"]["items"];
type CategoryId = keyof Dictionary["editor"]["library"]["categories"];

/** Library entries without a `type` have no backend node yet and are shown as coming soon. */
export type LibraryItem = { id: LibraryItemId; kind: NodeKind; type?: NodeType };

export const nodeLibrary: { category: CategoryId; items: LibraryItem[] }[] = [
  {
    category: "input",
    items: [
      { id: "topic", kind: "input", type: "idea" },
      { id: "studioMedia", kind: "input", type: "assets" },
      { id: "textInput", kind: "input" },
      { id: "url", kind: "input" },
      { id: "youtubeUrl", kind: "input" },
      { id: "uploadVideo", kind: "input" },
      { id: "uploadImage", kind: "input" },
      { id: "uploadAudio", kind: "input" },
      { id: "uploadSubtitle", kind: "input" },
      { id: "movieSource", kind: "input" },
      { id: "articleUrl", kind: "input" },
      { id: "transcript", kind: "input" },
    ],
  },
  {
    category: "ai",
    items: (
      [
        "research",
        "summarize",
        "aiWriter",
        "rewrite",
        "translate",
        "generateHook",
        "generateTitle",
        "movieAnalysis",
        "movieRecap",
        "movieReview",
        "endingExplained",
        "keyMoments",
        "generateCta",
      ] as const
    ).map((id) => ({ id, kind: "ai" as const })),
  },
  {
    category: "script",
    items: [
      { id: "scriptWriter", kind: "script", type: "script" },
      { id: "splitScenes", kind: "script", type: "scenes" },
      { id: "scenePlanner", kind: "script" },
      { id: "storyboard", kind: "script" },
      { id: "shortScript", kind: "script" },
      { id: "longScript", kind: "script" },
    ],
  },
  {
    category: "visual",
    items: [
      { id: "imageGenerator", kind: "image", type: "image" },
      { id: "thumbnailGenerator", kind: "image" },
      { id: "aiVideoGenerator", kind: "video", type: "video" },
      { id: "stockMedia", kind: "image" },
      { id: "clipMatcher", kind: "video" },
      { id: "imageToVideo", kind: "video" },
      { id: "textToVideo", kind: "video" },
    ],
  },
  {
    category: "audio",
    items: [
      { id: "textToSpeech", kind: "voice", type: "voice" },
      { id: "voiceClone", kind: "voice" },
      { id: "backgroundMusic", kind: "voice", type: "music" },
      { id: "soundEffects", kind: "voice" },
      { id: "audioMixer", kind: "voice" },
    ],
  },
  {
    category: "editing",
    items: [
      { id: "cropResize", kind: "edit" },
      { id: "aspectRatio", kind: "edit" },
      { id: "subtitle", kind: "subtitle", type: "subtitle" },
      { id: "overlayText", kind: "edit" },
      { id: "transition", kind: "edit" },
      { id: "timeline", kind: "edit" },
      { id: "mergeClips", kind: "edit" },
      { id: "renderVideo", kind: "render", type: "render" },
    ],
  },
  {
    category: "output",
    items: [
      { id: "preview", kind: "review" },
      { id: "review", kind: "review", type: "review" },
      { id: "download", kind: "review" },
      { id: "youtubeVideo", kind: "publish", type: "publish" },
      { id: "youtubeShorts", kind: "publish" },
      { id: "tiktok", kind: "publish" },
      { id: "facebook", kind: "publish" },
      { id: "facebookReels", kind: "publish" },
      { id: "schedulePost", kind: "publish" },
    ],
  },
];

/** Node types the backend can execute today; every other step stops a run with a reason. */
export const EXECUTABLE: ReadonlySet<NodeType> = new Set(["idea", "assets", "video", "review"]);

export type TemplateId = keyof Dictionary["templates"];

export type WorkflowTemplate = {
  id: TemplateId;
  /** Present when the template can be created and run with today's backend. */
  graph?: Graph;
  /** Design preview for templates that are not available yet. */
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

export const workflowTemplates: WorkflowTemplate[] = [
  { id: "social-video", graph: clipGraph, preview: ["input", "video", "review"], steps: 3 },
  { id: "youtube-video", preview: ["input", "ai", "script", "image", "video", "publish"], steps: 12 },
  { id: "youtube-short", graph: clipGraph, preview: ["input", "video", "review"], steps: 3 },
  { id: "tiktok-video", graph: clipGraph, preview: ["input", "video", "review"], steps: 3 },
  { id: "movie-recap", preview: ["input", "ai", "script", "video", "voice", "publish"], branches: 4, steps: 17 },
  { id: "movie-review", preview: ["input", "ai", "script", "image", "voice", "publish"], steps: 11 },
  { id: "repurpose", preview: ["input", "ai", "video", "publish"], branches: 4, steps: 9 },
  { id: "article-to-video", preview: ["input", "ai", "script", "image", "voice"], steps: 9 },
  { id: "product-video", preview: ["input", "script", "image", "video", "publish"], steps: 8 },
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
