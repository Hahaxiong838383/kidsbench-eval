// 后端 API 客户端（fetch wrapper）。
// 开发期：vite proxy 把 /api 转到 127.0.0.1:8000
// 生产期：nginx 同源反代

import type {
  ArchitectureIndex,
  AdapterMeta,
  ContractInfo,
  ExperimentRow,
  MemorySystemMeta,
  PipelineListItem,
  PipelineResponse,
  RunGroup,
  StateSnapshot,
  BankDetail,
  BankListResponse,
  BankCli,
} from "./types";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} on ${path}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<{ status: string; version: string; phase: string }>("/healthz"),

  architecture: () => get<ArchitectureIndex>("/api/architecture"),
  contract: () => get<ContractInfo>("/api/architecture/contract"),
  adapter: (name: string) => get<AdapterMeta>(`/api/architecture/adapter/${name}`),
  memory: (name: string) => get<MemorySystemMeta>(`/api/architecture/memory/${name}`),

  stateMem0: () => get<StateSnapshot>("/api/state/mem0"),
  stateMemoryos: () => get<StateSnapshot>("/api/state/memoryos"),
  stateGraphiti: () => get<StateSnapshot>("/api/state/graphiti"),

  // ===== Runs 数据源（Air / dev198 切换） =====
  // 评测引擎从 Air 迁到 dev198，两台机器各自产出 runs。
  // 列 runs 的函数都可带可选 source；不传走后端默认源（air），保持旧行为。
  getSources: () =>
    get<{ sources: string[]; default: string }>("/api/runs/sources"),

  // 手动同步：触发 QNAP 后端 pull 一次 dev198 的 runs（方案 A）
  syncSource: async (source: string) => {
    const res = await fetch(
      `/api/runs/sync?source=${encodeURIComponent(source)}`,
      { method: "POST" },
    );
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body?.detail ?? body?.message ?? `${res.status}`);
    }
    return body as {
      ok: boolean;
      source: string;
      groups: number;
      synced_at: number;
    };
  },

  runGroups: (params?: { adapter?: string; era?: string; source?: string }) => {
    const q = new URLSearchParams();
    if (params?.adapter) q.set("adapter", params.adapter);
    if (params?.era) q.set("era", params.era);
    if (params?.source) q.set("source", params.source);
    const qs = q.toString();
    return get<{ total: number; items: RunGroup[] }>(
      "/api/runs/groups" + (qs ? `?${qs}` : ""),
    );
  },
  runGroup: (name: string, source?: string) => {
    const q = new URLSearchParams();
    if (source) q.set("source", source);
    const qs = q.toString();
    return get<{
      name: string;
      target_adapter: string;
      era: string;
      summary: Record<string, import("./types").AdapterStats> | null;
      results_count: number;
      results: ExperimentRow[];
    }>(`/api/runs/groups/${name}` + (qs ? `?${qs}` : ""));
  },

  experiments: (params?: {
    adapter?: string;
    qid?: string;
    verdict?: string;
    limit?: number;
    source?: string;
  }) => {
    const q = new URLSearchParams();
    if (params?.adapter) q.set("adapter", params.adapter);
    if (params?.qid) q.set("qid", params.qid);
    if (params?.verdict) q.set("verdict", params.verdict);
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.source) q.set("source", params.source);
    const qs = q.toString();
    return get<{ total: number; limit: number; items: ExperimentRow[] }>(
      "/api/runs/experiments" + (qs ? `?${qs}` : ""),
    );
  },

  listPipelines: (group: string, source?: string) => {
    const q = new URLSearchParams();
    if (source) q.set("source", source);
    const qs = q.toString();
    return get<{ group: string; pipelines: PipelineListItem[]; count: number }>(
      `/api/runs/groups/${encodeURIComponent(group)}/pipelines` + (qs ? `?${qs}` : ""),
    );
  },
  getPipeline: (group: string, adapter: string, qid: string, source?: string) => {
    const q = new URLSearchParams();
    if (source) q.set("source", source);
    const qs = q.toString();
    return get<PipelineResponse>(
      `/api/runs/groups/${encodeURIComponent(group)}/pipeline/${encodeURIComponent(adapter)}/${encodeURIComponent(qid)}` +
        (qs ? `?${qs}` : ""),
    );
  },

  runsLatest: (source?: string) => {
    const q = new URLSearchParams();
    if (source) q.set("source", source);
    const qs = q.toString();
    return get<{
      items: {
        adapter: string;
        group: string;
        era: string;
        stats: import("./types").AdapterStats;
        mtime: number;
      }[];
    }>("/api/runs/latest" + (qs ? `?${qs}` : ""));
  },

  // ===== Banks (题库上传) =====
  listBanks: () => get<BankListResponse>("/api/banks"),
  getBank: (version: string) =>
    get<BankDetail>(`/api/banks/${encodeURIComponent(version)}`),
  getBankCli: (version: string, adapters: string[]) => {
    const q = new URLSearchParams({ adapters: adapters.join(",") });
    return get<BankCli>(`/api/banks/${encodeURIComponent(version)}/cli?${q.toString()}`);
  },
  uploadBank: async (file: File): Promise<BankDetail> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/banks/upload", { method: "POST", body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body?.detail ?? body?.message ?? `${res.status}`);
    }
    return body as BankDetail;
  },
};
