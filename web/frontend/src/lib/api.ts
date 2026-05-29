// 后端 API 客户端（fetch wrapper）。
// 开发期：vite proxy 把 /api 转到 127.0.0.1:8000
// 生产期：nginx 同源反代

import type {
  ArchitectureIndex,
  AdapterMeta,
  ContractInfo,
  ExperimentRow,
  MemorySystemMeta,
  RunGroup,
  StateSnapshot,
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

  runGroups: (params?: { adapter?: string; era?: string }) => {
    const q = new URLSearchParams();
    if (params?.adapter) q.set("adapter", params.adapter);
    if (params?.era) q.set("era", params.era);
    const qs = q.toString();
    return get<{ total: number; items: RunGroup[] }>(
      "/api/runs/groups" + (qs ? `?${qs}` : ""),
    );
  },
  runGroup: (name: string) =>
    get<{
      name: string;
      target_adapter: string;
      era: string;
      summary: Record<string, import("./types").AdapterStats> | null;
      results_count: number;
      results: ExperimentRow[];
    }>(`/api/runs/groups/${name}`),

  experiments: (params?: { adapter?: string; qid?: string; verdict?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.adapter) q.set("adapter", params.adapter);
    if (params?.qid) q.set("qid", params.qid);
    if (params?.verdict) q.set("verdict", params.verdict);
    if (params?.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return get<{ total: number; limit: number; items: ExperimentRow[] }>(
      "/api/runs/experiments" + (qs ? `?${qs}` : ""),
    );
  },

  runsLatest: () =>
    get<{
      items: {
        adapter: string;
        group: string;
        era: string;
        stats: import("./types").AdapterStats;
        mtime: number;
      }[];
    }>("/api/runs/latest"),
};
