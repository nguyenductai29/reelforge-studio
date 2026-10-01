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
  | "cta"
  | "metadata"
  | "source_text"
  | "source_url"
  | "source_media"
  | "transcribe"
  | "story_analysis"
  | "recap_script"
  | "match_scenes"
  | "extract_clips";

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
  type: "select" | "integer" | "number" | "text" | "tool" | "asset";
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
  /** Asset fields: the media types the step can read. */
  content_types?: string[];
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
  storage?: StorageLevelInfo;
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
  /** The AI model the step will use: its own setting (`chosen`), else the first enabled one for its task. */
  tool?: { id: string; provider: string; model: string; chosen: boolean } | null;
};
export type Readiness = {
  workflow_id: string;
  runnable: boolean;
  credits_required: number;
  credits_available: number;
  steps: ReadinessStep[];
};

export type AiTask = "script" | "image" | "video" | "voice" | "music" | "transcription";
export type AiTool = { id: string; task: AiTask; provider: string; model: string; is_enabled: boolean };

export type Plan = {
  code: string;
  name: string;
  project_limit: number | null;
  workflow_limit: number | null;
  monthly_credits: number;
  is_active: boolean;
  price_vnd: number | null;
  /** Media a workspace on this plan may store; null uses the server default. */
  storage_limit_bytes: number | null;
  /** The limit in force (the server may cap it); absent from an API older than Phase 17. */
  storage_quota_bytes?: number;
};
export type PaymentMethod = "vietqr" | "card";
export type Order = {
  id: string;
  plan_code: string;
  provider: string;
  /** vietqr (payOS bank transfer) or card (OnePAY). */
  method: PaymentMethod | null;
  /** Our order reference, which both providers know the order by. */
  reference: string;
  provider_reference: string | null;
  amount_vnd: number;
  status: string;
  created_at: string;
  paid_at: string | null;
  /** Manual VietQR only (Phase 20): the content the buyer must write, and when they said they paid. */
  transfer_content?: string | null;
  transfer_reported_at?: string | null;
};
/** Manual VietQR: everything the buyer needs to pay; the QR is drawn on the server. */
export type BankTransfer = {
  bank_bin: string;
  bank_name: string;
  account_number: string;
  account_name: string;
  amount_vnd: number;
  content: string;
  payload: string;
  /** An SVG data URI. */
  qr: string;
  note: string;
  sla_message: string;
};
export type CheckoutResult = { order_id: string; checkout_url: string | null; transfer?: BankTransfer };
export type Billing = {
  plans: Plan[];
  subscription: { plan_code: string; status: string; ends_at: string | null };
  orders: Order[];
  orders_total: number;
  /** Only the payment methods this server can take. */
  methods: { id: PaymentMethod; provider: string }[];
  payos_ready: boolean;
};
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };
export type Usage = {
  balance: number;
  ledger: { id: string; delta: number; reason: string; created_at: string }[];
  events: { id: string; tool: string; units: number; credits: number; created_at: string }[];
};

export type PrivacyStatus = "private" | "unlisted" | "public";
export type ChannelId = "youtube" | "tiktok" | "facebook";
export type PublicationState =
  | "scheduled"
  | "queued"
  | "uploading"
  | "succeeded"
  | "failed"
  | "needs_attention"
  | "cancelled";
export type Publication = {
  id: string;
  run_id: string;
  asset_id: string;
  channel: ChannelId;
  title: string;
  description: string;
  tags: string[];
  privacy_status: PrivacyStatus;
  state: PublicationState;
  remote_id: string | null;
  /** What the platform reported: YouTube "uploaded" (processing), TikTok "sent_to_inbox", Facebook "published"/"draft". */
  remote_status: string | null;
  remote_privacy: PrivacyStatus | null;
  youtube_url: string | null;
  /** The public page, once there is one (YouTube, a published Facebook Reel). */
  url?: string | null;
  last_error: string | null;
  can_retry: boolean;
  can_cancel?: boolean;
  can_reschedule?: boolean;
  /** UTC. */
  scheduled_for?: string | null;
  published_at?: string | null;
  created_at: string;
  finished_at: string | null;
};

export type PublishMetadata = { title: string; description: string; tags: string[]; privacy_status: PrivacyStatus };
export type PlatformMetadata = Record<ChannelId, PublishMetadata>;

export type ChannelStatus = {
  channel: ChannelId;
  status: "connected" | "not_connected" | "configuration_required" | "authorization_required";
  /** Why authorization is required: "expired", "missing_scope" or "page_required" (Facebook). */
  reason: string | null;
  account_name: string | null;
  connected_at: string | null;
  /** Facebook, while no Page is chosen: the Pages the account can publish to. */
  pages?: { id: string; name: string }[];
};

export type DefaultModels = Record<"text" | "image" | "video" | "voice" | "transcription", string | null>;
/** ok below 70 %, then notice (70), warning (80), critical (90) and full (100: nothing new can be stored). */
export type StorageLevel = "ok" | "notice" | "warning" | "critical" | "full";
export type StorageLevelInfo = { used_bytes: number; quota_bytes: number; percent: number; level: StorageLevel };
export type RetentionInfo = { intermediate_days: number; temp_days: number; partial_days: number };
export type StorageUsage = StorageLevelInfo & {
  by_type: Record<"video" | "audio" | "image" | "document", number>;
  /** Intermediate media a user may remove now (their run has a final render); null from an older API. */
  intermediate: { assets: number; bytes: number } | null;
  retention: RetentionInfo | null;
};
export type StorageCleanup = { assets: number; bytes: number; applied: boolean };
export type MediaDeleteResult = {
  deleted: string[];
  skipped: { id: string; reason: "not_found" | "asset_in_use" | "unsafe_path" }[];
  freed_bytes: number;
};

export type WorkerHealth = {
  worker: string;
  status: "ok" | "stale" | "error" | "missing";
  last_seen_at: string | null;
  seconds_ago?: number;
  host: string | null;
  pid: number | null;
  detail: string | null;
};
export type AdminJob = {
  id: string;
  queue: string;
  channel: string | null;
  state: "queued" | "leased" | "succeeded" | "failed";
  attempt_count: number;
  worker_id: string | null;
  workspace_id: string;
  run_id: string;
  step_id: string;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
  available_at: string | null;
  lease_expires_at: string | null;
  finished_at: string | null;
};
export type StuckJob = Pick<
  AdminJob,
  "id" | "queue" | "state" | "attempt_count" | "worker_id" | "workspace_id" | "run_id" | "step_id" | "available_at" | "lease_expires_at"
>;
export type AdminJobs = {
  jobs: AdminJob[];
  total: number;
  limit: number;
  offset: number;
  counts: Record<string, Partial<Record<AdminJob["state"], number>>>;
  stuck: {
    expired_leases: StuckJob[];
    overdue: StuckJob[];
    orphan_steps: { step_id: string; run_id: string; node_type: string; status: string }[];
  };
};
export type AdminStorage = {
  workspaces: (StorageLevelInfo & { workspace_id: string; name: string; files: number })[];
  total: number;
  limit: number;
  offset: number;
  levels: Record<Exclude<StorageLevel, "ok">, number>;
  disk: { total_bytes: number; used_bytes: number; free_bytes: number; percent: number } | null;
  default_quota_bytes: number;
  retention: RetentionInfo | null;
};
export type RunStepItem = { node_id: string; node_type: NodeType; status: RunStatus; detail: string; error_code: string | null };
/** GET /api/workflow-runs/{id}/summary: progress, results, credits and publishing of one run. */
export type RunSummary = {
  run_id: string;
  status: RunStatus;
  elapsed_seconds: number;
  steps: { total: number; completed: number };
  current: RunStepItem[];
  failed: RunStepItem[];
  needs_attention: RunStepItem[];
  blocked: RunStepItem[];
  render_failed: (RunStepItem & { message?: string | null }) | null;
  active_jobs: number;
  counts: { script_words: number; scenes: number; images: number; clips: number; narrations: number; subtitle_cues: number };
  final_video: {
    asset_id: string;
    filename: string;
    bytes: number;
    final: boolean;
    duration: number | null;
    width: number | null;
    height: number | null;
  } | null;
  review: { present: boolean; status: RunStatus | null; approved: boolean };
  credits: { reserved: number; refunded: number; consumed: number; held: number };
  publishing: {
    ready: boolean;
    defaults: PublishMetadata & { source: "publish" | "metadata" | "project"; platforms?: PlatformMetadata };
    publication: Publication | null;
    youtube_connected: boolean;
    /** Every channel's publication of this run (Phase 12). */
    publications?: Publication[];
    channels?: ChannelStatus[];
  };
};
export type YouTubeConnection = { connected: boolean; expires_at: string | null; scope: string | null };

export type ContentPlatform = "generic" | "youtube" | "youtube_shorts" | "tiktok" | "facebook";
export type ContentTone =
  | "neutral"
  | "casual"
  | "professional"
  | "cinematic"
  | "storytelling"
  | "documentary"
  | "dramatic"
  | "funny";
export type WorkspaceSettings = {
  default_language: "vi" | "en" | "ja";
  video_orientation: "vertical" | "horizontal" | "square";
  approval_required: boolean;
  /** Text steps that leave these empty use them. */
  default_platform: ContentPlatform;
  default_tone: ContentTone;
  default_duration: number | null;
  /** "HH:MM" in the viewer's time zone; prefills scheduled posts. */
  default_publish_time: string | null;
};
export type SystemSettings = {
  frontend_origin: string;
  secure_cookies: boolean;
  storage_dir: string;
  /** Where the folder in use comes from: REELFORGE_STORAGE_ROOT or the stored setting. */
  storage_dir_source?: "admin" | "environment" | "setting";
  trial_project_limit: number;
  registration_enabled: boolean;
};
export type Settings = {
  workspace: WorkspaceSettings;
  system: SystemSettings | null;
  profile: { display_name: string | null };
};

export type PaymentProviderStatus = {
  provider: "payos" | "bank_qr" | "onepay";
  method: PaymentMethod;
  /** Credentials are usable (callbacks of existing orders work). */
  configured: boolean;
  /** Phase 19: the system admin's switch, and whether buyers can choose it now. Absent from older APIs. */
  enabled?: boolean;
  available?: boolean;
  source?: PaymentConfigSource;
};
/** GET /api/admin: counts from COUNT queries; the collections have paginated endpoints of their own. */
export type AdminOverview = {
  counts: {
    users: number;
    active_users: number;
    admins: number;
    workspaces: number;
    plans: number;
    pending_reconciliation: number;
    failed_jobs_24h: number;
    stuck_jobs: number;
    pending_payments: number;
    /** Studios at 90 % of their storage or more. */
    storage_alerts: number;
    /** Support requests awaiting an answer (Phase 18C); absent from an older API. */
    support_open?: number;
    /** Manual VietQR transfers waiting for an admin (Phase 20). */
    transfers_to_confirm?: number;
  };
  storage_levels: Record<Exclude<StorageLevel, "ok">, number>;
  plans: Plan[];
  payment_providers: PaymentProviderStatus[];
  /** Set by the client when the API is older than this page (it has not been restarted after an update). */
  api_outdated?: boolean;
};
export type AdminUser = {
  id: string;
  email: string;
  display_name: string | null;
  is_admin: boolean;
  is_active: boolean;
  created_at: string | null;
  workspace: { id: string; name: string; role: string } | null;
  plan_code: string | null;
  subscription_status: string | null;
};
export type AdminUserDetail = AdminUser & {
  active_sessions: number;
  workspaces: { id: string; name: string; role: string; plan_code: string | null; status: string; credits: number }[];
};
export type AdminWorkspace = {
  id: string;
  name: string;
  owner_id: string;
  owner_email: string;
  plan_code: string | null;
  status: string;
  ends_at: string | null;
  credits: number;
  created_at: string | null;
  storage?: StorageLevelInfo;
};
export type AdminWorkspaceDetail = AdminWorkspace & {
  members: { email: string; role: string }[];
  counts: { projects: number; workflows: number; runs: number };
  storage_bytes: number;
  storage?: StorageLevelInfo;
  ledger: { id: string; delta: number; reason: string; created_at: string }[];
  orders: Order[];
};
export type AdminPayment = Order & { workspace_id: string; workspace_name: string; owner_email: string };

export type ScriptItem = {
  step_id: string;
  run_id: string;
  project_id: string;
  workflow_id: string;
  node_type: NodeType;
  node_id: string;
  text: string;
  words: number;
  truncated: boolean;
  created_at: string;
};

/** Phase 18B: one in-app notification; the frontend localizes it from ``type`` and ``params``. */
export type AppNotification = {
  id: number;
  type: string;
  title: string;
  message: string;
  link: string | null;
  params: Record<string, string | number | boolean | null>;
  workspace_id: string | null;
  read_at: string | null;
  created_at: string | null;
};
export type NotificationPage = Page<AppNotification> & { unread: number };

/** Phase 18C: support. */
export type SupportCategory = "billing" | "credits" | "generation" | "publishing" | "account" | "storage" | "bug" | "other";
export type SupportStatus = "open" | "waiting_support" | "waiting_user" | "resolved" | "closed";
export type SupportPriority = "normal" | "high";
export type SupportTicket = {
  id: string;
  subject: string;
  category: SupportCategory;
  status: SupportStatus;
  priority: SupportPriority;
  workspace_id: string;
  workspace_name: string | null;
  created_by_email: string | null;
  messages: number | null;
  context: Partial<Record<"run_id" | "project_id" | "payment_order_id" | "publication_id", string>>;
  created_at: string | null;
  updated_at: string | null;
  closed_at: string | null;
};
export type SupportMessage = {
  id: string;
  author_type: "user" | "admin";
  /** null for support replies seen by a user. */
  author_email: string | null;
  body: string;
  created_at: string | null;
};
export type SupportTicketDetail = SupportTicket & { thread: SupportMessage[] };

/** Phase 18A: payment provider setup as a system admin sees it (never a secret value). */
/** Phase 19: a payment gateway as the server resolves it. Secrets are write-only: never in this type. */
export type PaymentConfigSource = "admin" | "bootstrap" | "environment" | "missing";
export type PaymentMode = "sandbox" | "production" | "custom";
export type PaymentField = {
  configured: boolean;
  status: "configured" | "missing" | "invalid";
  secret: boolean;
  required: boolean;
  /** Where the legacy source keeps it (a bootstrap key or an environment variable). */
  legacy: string;
  /** Identifiers only (client ID, merchant ID), masked. */
  masked?: string;
};
export type PaymentIssue = { level: "error" | "warning"; code: string; field?: string };
export type PaymentSetup = {
  provider: "payos" | "bank_qr" | "onepay";
  method: PaymentMethod;
  enabled: boolean;
  configured: boolean;
  available: boolean;
  source: PaymentConfigSource;
  legacy_source: "bootstrap" | "environment" | null;
  mode: PaymentMode | null;
  fields: Record<string, PaymentField>;
  issues: PaymentIssue[];
  updated_at: string | null;
  updated_by: string | null;
  urls?: { payment_url: string; query_url: string };
  query_configured?: boolean;
  endpoints: { key: "webhook" | "ipn" | "return"; url: string }[];
  activity: Partial<Record<"webhook" | "ipn" | "query" | "check", string>>;
  history: { action: "created" | "updated" | "enabled" | "disabled" | "tested"; at: string; by: string | null;
             metadata: { changed?: string[]; local?: string; remote_status?: string } }[];
  /** Whether this provider takes the method's new checkouts (VietQR: payOS or the manual bank QR). */
  active?: boolean;
  /** Manual VietQR (bank_qr) only: the bank details (not secret) and a sample QR. */
  values?: BankQRValues;
  bank_name?: string;
  preview?: BankTransfer | null;
};
export type BankQRValues = {
  bank_bin: string;
  bank_name: string;
  account_number: string;
  account_name: string;
  transfer_prefix: string;
  note: string;
  sla_message: string;
};
export type PaymentSetupOverview = {
  providers: PaymentSetup[];
  any_available: boolean;
  /** Absent from older APIs. */
  vietqr_mode?: "manual" | "payos";
  banks?: { bin: string; name: string }[];
  encryption: { available: boolean; variable: string };
};
/** What a secret input sends: untouched fields are kept, never cleared by an empty string. */
export type SecretUpdate = { action: "keep" } | { action: "replace"; value: string } | { action: "clear" };
export type PaymentCheckStatus = "ok" | "warning" | "error" | "skipped" | "unsupported";
export type PaymentCheck = {
  provider: "payos" | "bank_qr" | "onepay";
  source: PaymentConfigSource;
  local: { status: PaymentCheckStatus; code?: string };
  remote: { status: PaymentCheckStatus; code?: string };
  checked_at: string;
};

/** Phase 18D: readiness checks and the manual checklist. */
export type SystemCheckStatus = "ok" | "warning" | "error" | "missing" | "off";
export type SystemCheck = { key: string; status: SystemCheckStatus; detail?: string } & Record<string, unknown>;
export type SystemReadiness = { checked_at: string; sections: { key: string; checks: SystemCheck[] }[] };
export type VerificationItem = {
  key: string;
  /** Absent from older APIs. */
  group?: "platform" | "ai" | "publishing" | "vietqr" | "card" | "operations";
  paid: boolean;
  how: string;
  verified: boolean;
  verified_at: string | null;
  verified_by: string | null;
  note: string | null;
};

/** Phase 20: one admin-managed setting; secrets only say whether they are configured. */
export type SystemSetting = {
  key: string;
  section: string;
  group: string;
  kind: "secret" | "str" | "int" | "float" | "bool";
  env: string | null;
  source: "admin" | "environment" | "default" | "error";
  minimum: number | null;
  maximum: number | null;
  default: string | number | boolean | null;
  updated_at: string | null;
  updated_by: string | null;
  error?: string;
  configured?: boolean;
  value?: string | number | boolean | null;
};
export type SystemSection = "ai" | "social" | "storage" | "runtime" | "credits" | "notifications";
export type SystemConfigOverview = {
  sections: Record<SystemSection, SystemSetting[]>;
  history: Record<SystemSection, { action: string; at: string; by: string | null;
                                   metadata: { changed?: string[]; provider?: string; local?: string; remote?: string } }[]>;
  redirects: Record<"youtube" | "tiktok" | "facebook", string>;
  derived_redirects: Record<"youtube" | "tiktok" | "facebook", string>;
  models_in_use: Record<string, number>;
  master_key: {
    source: "file" | "legacy_env" | "missing";
    path: string | null;
    problem: string | null;
    permissions_ok: boolean | null;
    legacy_env_set: boolean;
    legacy_env_matches: boolean | null;
    encryption_available: boolean;
  };
  storage: { root: string; source: string; files: number; disk: { free_bytes: number; total_bytes: number } | null };
  migrated: boolean;
};
export type ProviderTest = {
  provider: string;
  local: { status: string; code?: string };
  remote: { status: string; code?: string };
  checked_at: string;
};
