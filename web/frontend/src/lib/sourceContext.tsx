// Runs 数据源（Air / dev198）全局选择器 context。
//
// 评测引擎从 Air 迁到工作站 dev198，两台机器各自产出 eval 结果。
//
// 持久化策略（双层，localStorage 为真相源 + URL query 作可分享同步）：
//   - 选中的 source 写入 localStorage（key=kb_source）→ 跨页面导航 / 刷新都保持
//   - 同时同步到 URL query（?source=dev198）→ 链接可分享 / 可复现
//   - 默认源（air）= 不写 localStorage、不带 query，保持旧行为
//
// 为什么不只用 URL query：导航 tab 用裸路径 <NavLink to="/runs">，
// React Router 跳转会整体替换 URL（丢掉 query），导致 source 回退默认源（air）。
// localStorage 作真相源后，跨 tab / 刷新都不丢；URL query 仅作可选的可分享同步。
//
// 各页面通过 useSource() 读取当前 source，传给 api.runGroups/runGroup/... 调用。

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "./api";

const STORAGE_KEY = "kb_source";

interface SourceContextValue {
  /** 当前选中数据源；undefined = 用后端默认源（不显式传 source） */
  source: string | undefined;
  /** 可选数据源列表（从 /api/runs/sources 拉） */
  sources: string[];
  /** 后端默认源名 */
  defaultSource: string;
  setSource: (s: string) => void;
}

const SourceContext = createContext<SourceContextValue | null>(null);

/** 读 localStorage 持久值（SSR / 隐私模式下 localStorage 不可用时静默降级） */
function readStored(): string | undefined {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

/** 写/清 localStorage（key=kb_source）；异常静默，不阻塞 UI */
function writeStored(s: string | undefined): void {
  try {
    if (s) localStorage.setItem(STORAGE_KEY, s);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // 隐私模式 / 配额满：忽略，URL query 仍兜底
  }
}

export function SourceProvider({ children }: { children: ReactNode }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sources, setSources] = useState<string[]>([]);
  const [defaultSource, setDefaultSource] = useState<string>("air");

  // 真相源 = 组件状态（由 localStorage 初始化）；URL query 仅作可分享同步。
  // 初始优先级：URL ?source= > localStorage > undefined（默认 air）。
  // URL query 优先：保证分享链接打开时按链接里的源走，并把它持久化下来。
  const [source, setSourceState] = useState<string | undefined>(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("source");
    if (fromUrl) {
      writeStored(fromUrl);
      return fromUrl;
    }
    return readStored();
  });

  useEffect(() => {
    api
      .getSources()
      .then((r) => {
        setSources(r.sources);
        setDefaultSource(r.default);
      })
      .catch(() => {
        // 后端不可用时静默：保留默认，不阻塞页面
      });
  }, []);

  // 外部直接改 URL ?source=（如粘贴分享链接 / 浏览器前进后退）→ 同步进状态 + 持久化。
  // 仅当 URL 显式带 source 且与当前状态不同才接管，避免裸路径导航（无 query）误清空。
  const urlSource = searchParams.get("source") ?? undefined;
  useEffect(() => {
    if (urlSource && urlSource !== source) {
      setSourceState(urlSource);
      writeStored(urlSource);
    }
  }, [urlSource, source]);

  const setSource = useCallback(
    (s: string) => {
      // 默认源 = 清空（走后端默认行为 + URL / 存储都不留痕）
      const normalized = !s || s === defaultSource ? undefined : s;

      setSourceState(normalized);
      writeStored(normalized);

      // 同步 URL query（可分享）；选默认源时移除 query 保持 URL 干净
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (normalized) next.set("source", normalized);
          else next.delete("source");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams, defaultSource],
  );

  const value = useMemo<SourceContextValue>(
    () => ({ source, sources, defaultSource, setSource }),
    [source, sources, defaultSource, setSource],
  );

  return <SourceContext.Provider value={value}>{children}</SourceContext.Provider>;
}

export function useSource(): SourceContextValue {
  const ctx = useContext(SourceContext);
  if (!ctx) {
    throw new Error("useSource must be used within <SourceProvider>");
  }
  return ctx;
}
