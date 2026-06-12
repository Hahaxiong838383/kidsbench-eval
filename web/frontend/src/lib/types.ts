// 后端 API 返回的类型（与 web/backend/app/*.py 对齐）

export interface MethodInfo {
  name: string;
  kind: "abstract" | "overridable";
  file: string;
  line: number;
  logic: string;
}

export interface ContractInfo {
  abc_class: {
    name: string;
    file: string;
    line: number;
    doc: string;
  };
  abstract_methods: string[];
  overridable_methods: string[];
  helper_methods: string[];
  design_principles: string[];
}

export interface AdapterMeta {
  name: string;
  sdk: {
    package: string;
    version: string;
    github: string;
    install: string;
  };
  entry_class: {
    name: string;
    file: string;
    line: number;
  };
  methods: MethodInfo[];
  middleware_deps: string[];
  storage: string;
  venv?: string;
  known_issues: string[];
}

export interface MemoryLayer {
  level: string;
  analogy: string;
  content: string;
}

export interface MemoryIntroduction {
  tldr: string;
  problem: string;
  mechanism: string[];
  layers?: MemoryLayer[];
  key_diff?: string;
}

export interface MemorySystemMeta {
  name: string;
  kind: string;
  introduction?: MemoryIntroduction;
  schema: Record<string, unknown>;
  deployment: string;
  real_time_stats: boolean;
  stats_source: string;
}

export interface ArchitectureIndex {
  contract: ContractInfo;
  adapters: Record<string, AdapterMeta>;
  memory_systems: Record<string, MemorySystemMeta>;
  embedding_model: {
    name: string;
    dim: number;
    size_mb: number;
    max_tokens: number;
    is_asymmetric: boolean;
  };
  llm_model: {
    name: string;
    provider: string;
    endpoint: string;
    reasoning_effort: string;
  };
}

export interface RunGroup {
  name: string;
  target_adapter: string;
  era: string;
  items_count: number;
  summary: Record<string, AdapterStats> | null;
  mtime: number;
}

export interface AdapterStats {
  correct: number;
  wrong: number;
  evasive: number;
  error: number;
  total: number;
}

export interface ExperimentRow {
  group: string;
  era: string;
  qid: string;
  adapter: string;
  user_id?: string;
  success?: boolean;
  answer?: string;
  judge_verdict?: "correct" | "wrong" | "evasive" | "error";
  judge_score?: number;
  recalled_count?: number;
  recalled_texts?: string[];
  recall_metric?: {
    recall: number;
    precision: number;
    hit_count: number;
  };
  timestamp?: number;
  llm_latency_ms?: number;
  llm_tokens_in?: number;
  llm_tokens_out?: number;
}

// Trace span event（B1）
export interface SpanEvent {
  event_id: number;
  span_id: string;
  parent_id: string | null;
  name: string;
  type: "ENTER" | "EXIT" | "ATTR";
  ts: number;
  duration_ms?: number;
  // 任意 attrs
  [key: string]: unknown;
}

export interface PipelineResponse {
  group: string;
  adapter: string;
  qid: string;
  events: SpanEvent[];
  events_count?: number;
  source?: string;
  note?: string;
}

export interface PipelineListItem {
  filename: string;
  adapter: string;
  qid: string;
  size_bytes: number;
  mtime: number;
}

export interface StateSnapshot {
  adapter: string;
  real_time: boolean;
  source: string;
  warning?: string;
  snapshot?: {
    group: string;
    summary_in_group: AdapterStats | null;
    latest_row: ExperimentRow;
  } | null;
  // graphiti only
  graphs?: { name: string; nodes?: number; edges?: number; ok: boolean; error?: string }[] | null;
  graphs_count?: number | null;
  latest_run?: StateSnapshot["snapshot"];
}

// ===== Assistant (契约 §3.1 / §7) =====
export interface AssistantSession {
  token: string;
  expires_at: string;
  label?: string;
}

export interface AssistantMessage {
  role: "user" | "assistant";
  content: string;
  // assistant only (from meta)
  tier_label?: string;
  degraded?: boolean;
  model?: string;
}

export interface AssistantDoneInfo {
  tokens_in: number;
  tokens_out: number;
  quota_left: number;
  upgrades_left: number;
}

export interface AdminPhone {
  phone: string;
  label: string;
  enabled: number | boolean;
  daily_quota_tokens: number;
  daily_upgrade_limit: number;
  today_used_tokens?: number;
}

export interface AdminUsagePoint {
  phone: string;
  day: string;
  tokens: number;
  requests: number;
  upgrades: number;
  degraded: number;
}

export interface AdminSettings {
  [key: string]: string;
}
