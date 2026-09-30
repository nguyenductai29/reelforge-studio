export type NodeType =
  | "idea"
  | "assets"
  | "script"
  | "scenes"
  | "image"
  | "video"
  | "voice"
  | "music"
  | "subtitle"
  | "render"
  | "review"
  | "publish"
  | "ai_writer"
  | "summarize"
  | "rewrite"
  | "translate"
  | "hook"
  | "title"
  | "cta";

/** Per-node settings; the backend validates them for each node type. */
export type NodeConfig = Record<string, unknown>;
export type GraphNode = {
  id: string;
  type: NodeType;
  x: number;
  y: number;
  label?: string | null;
  config?: NodeConfig | null;
};
/** Handles name the ports an edge connects; edges saved before ports existed have none. */
export type GraphEdge = { source: string; target: string; sourceHandle?: string | null; targetHandle?: string | null };
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

/** Typed ports per node type, served by the backend's node registry. */
export type InputPort = { name: string; accepts: string[]; multiple: boolean };
export type OutputPort = { name: string; type: string };
/**
 * One setting of a node type, as the backend validates it. A value equal to the
 * default is left out of the node's config; `code` is the error the backend returns.
 */
export type ConfigField = {
  key: string;
  type: "select" | "integer" | "number" | "text" | "tool";
  label: string;
  default: string | number | null;
  required: boolean;
  advanced: boolean;
  code: string;
  options?: string[];
  presets?: number[];
  minimum?: number;
  maximum?: number;
  max_length?: number;
  multiline?: boolean;
  task?: AiTask;
  providers?: string[];
};
export type NodePorts = { inputs: InputPort[]; outputs: OutputPort[]; requires: string[][]; config: ConfigField[] };
export type NodeCatalog = { data_types: string[]; node_types: Record<string, NodePorts> };
export type Workflow = { id: string; name: string; graph: Graph };

export type Project = { id: string; title: string; topic: string; status: string; created_at: string | null };
export type Asset = {
  id: string;
  filename: string;
  bytes: number;
  content_type: string;
  project_id: string | null;
  run_id: string | null;
  created_at: string | null;
};

export type Dashboard = {
  workspace: { id: string; name: string; plan: string; subscription_status: string };
  user: { email: string };
  is_admin: boolean;
  projects: Project[];
  assets: Asset[];
  workflows: Workflow[];
  limits: { projects: number | null; workflows: number | null };
};

export type RunStatus =
  | "running"
  | "queued"
  | "submitting"
  | "completed"
  | "blocked"
  | "skipped"
  | "failed"
  | "needs_attention"
  | "awaiting_review";

export type RunStep = {
  node_id: string;
  node_type: NodeType;
  status: RunStatus;
  detail: string;
  output: (Record<string, unknown> & { reconciliation?: ReconciliationSummary }) | null;
};

export type ReconciliationSummary = {
  status: "confirmed_charge" | "refunded";
  reconciled_at: string;
  credits: number;
};

export type ReconciliationItem = {
  step_id: string;
  run_id: string;
  job_id: string;
  /** Set for one scene's image or clip; each such job is decided on its own. */
  scene_index: number | null;
  operation: string | null;
  workspace_id: string;
  workspace_name: string;
  user_email: string;
  workflow_id: string;
  workflow_name: string;
  node_id: string;
  node_type: string;
  provider: string;
  model: string;
  remote_request_id: string | null;
  credits: number;
  created_at: string;
  stage: string | null;
  submitted_at: string | null;
  last_polled_at: string | null;
  last_provider_status: string | null;
  submission_succeeded: boolean;
  error_category: string | null;
  error_message: string;
  has_asset: boolean;
  asset_ids: string[];
  reconciliation_status: "pending" | ReconciliationSummary["status"];
  reconciled_at: string | null;
  reconciled_by: string | null;
  reconciled_by_email: string | null;
  note: string | null;
};

export type ReconciliationPage = { items: ReconciliationItem[]; total: number };

export type Run = {
  id: string;
  workflow_id: string;
  project_id: string;
  retry_of_id: string | null;
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  steps?: RunStep[];
};

export type ReadinessStep = {
  node_id: string;
  task: NodeType;
  status: string;
  detail: string;
  /** For invalid settings or inputs: a stable error code and the setting or port it concerns. */
  code?: string | null;
  field?: string | null;
  /** Credits this step holds when it starts; per scene when `code` is "per_scene". */
  credits?: number;
};
export type Readiness = {
  workflow_id: string;
  runnable: boolean;
  credits_required: number;
  credits_available: number;
  steps: ReadinessStep[];
};

export type AiTask = "script" | "image" | "video" | "voice" | "music";
export type AiTool = { id: string; task: AiTask; provider: string; model: string; is_enabled: boolean };

export type Plan = {
  code: string;
  name: string;
  project_limit: number | null;
  workflow_limit: number | null;
  monthly_credits: number;
  is_active: boolean;
  price_vnd: number | null;
};
export type Order = {
  id: string;
  plan_code: string;
  provider: string;
  amount_vnd: number;
  status: string;
  created_at: string;
  paid_at: string | null;
};
export type Billing = {
  plans: Plan[];
  subscription: { plan_code: string; status: string; ends_at: string | null };
  orders: Order[];
  payos_ready: boolean;
};
export type Usage = {
  balance: number;
  ledger: { id: string; delta: number; reason: string; created_at: string }[];
  events: { id: string; tool: string; units: number; credits: number; created_at: string }[];
};

export type Publication = {
  id: string;
  run_id: string;
  asset_id: string;
  channel: string;
  title: string;
  description: string;
  state: "queued" | "uploading" | "succeeded" | "failed" | "needs_attention";
  remote_id: string | null;
  last_error: string | null;
  can_retry: boolean;
  created_at: string;
  finished_at: string | null;
};
export type YouTubeConnection = { connected: boolean; expires_at: string | null; scope: string | null };

export type WorkspaceSettings = {
  default_language: "vi" | "en" | "ja";
  video_orientation: "vertical" | "horizontal" | "square";
  approval_required: boolean;
};
export type SystemSettings = {
  frontend_origin: string;
  secure_cookies: boolean;
  storage_dir: string;
  trial_project_limit: number;
  registration_enabled: boolean;
};
export type Settings = { workspace: WorkspaceSettings; system: SystemSettings | null };

export type AdminOverview = {
  users: { id: string; email: string; is_admin: boolean; is_active: boolean }[];
  workspaces: {
    id: string;
    name: string;
    owner_id: string;
    plan_code: string | null;
    status: string;
    ends_at: string | null;
    credits: number;
  }[];
  plans: Plan[];
};
