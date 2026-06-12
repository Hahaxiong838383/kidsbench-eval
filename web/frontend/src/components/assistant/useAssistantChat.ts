/**
 * useAssistantChat (契约 §3.1 / §7)
 * - localStorage: assistant_messages / assistant_token / assistant_phone
 * - 手动 SSE：fetch + ReadableStream + AbortController（严禁 EventSource）
 * - 逐行解析 event:/data: ，分发 meta/delta/tool/done/error
 * - 升级：replace 模式（截断到问题，force_tier 重发，in-place 替换回答）
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AssistantDoneInfo,
  AssistantMessage,
  AssistantSession,
} from "../../lib/types";

const LS_MESSAGES = "assistant_messages";
const LS_TOKEN = "assistant_token";
const LS_PHONE = "assistant_phone";

interface ChatError {
  code: string;
  message: string;
}

export function useAssistantChat() {
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [token, setToken] = useState<string | null>(null);
  const [phone, setPhone] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentTool, setCurrentTool] = useState<string | null>(null);
  const [errors, setErrors] = useState<ChatError[]>([]);
  const [lastDone, setLastDone] = useState<AssistantDoneInfo | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // 初次恢复（只一次）
  useEffect(() => {
    try {
      const savedToken = localStorage.getItem(LS_TOKEN);
      const savedPhone = localStorage.getItem(LS_PHONE);
      const savedMsgs = localStorage.getItem(LS_MESSAGES);
      if (savedToken) setToken(savedToken);
      if (savedPhone) setPhone(savedPhone);
      if (savedMsgs) {
        const parsed = JSON.parse(savedMsgs) as AssistantMessage[];
        if (Array.isArray(parsed)) setMessages(parsed);
      }
    } catch {
      // ignore corrupt storage
    }
  }, []);

  // 持久化
  useEffect(() => {
    if (messages.length > 0) {
      localStorage.setItem(LS_MESSAGES, JSON.stringify(messages));
    }
  }, [messages]);

  useEffect(() => {
    if (token) localStorage.setItem(LS_TOKEN, token);
    else localStorage.removeItem(LS_TOKEN);
  }, [token]);

  useEffect(() => {
    if (phone) localStorage.setItem(LS_PHONE, phone);
    else localStorage.removeItem(LS_PHONE);
  }, [phone]);

  const clearErrors = useCallback(() => setErrors([]), []);

  const addError = useCallback((e: ChatError) => {
    setErrors((prev) => [...prev, e].slice(-3)); // 最多保留最近 3 条
  }, []);

  const setSession = useCallback((sess: AssistantSession, p: string) => {
    setToken(sess.token);
    setPhone(p);
    // 新登录清空旧聊天（按需，也可不清）
    setMessages([]);
    setLastDone(null);
    localStorage.removeItem(LS_MESSAGES);
  }, []);

  const clearSession = useCallback(() => {
    setToken(null);
    setPhone(null);
    setMessages([]);
    setLastDone(null);
    setErrors([]);
    setCurrentTool(null);
    localStorage.removeItem(LS_MESSAGES);
    localStorage.removeItem(LS_TOKEN);
    localStorage.removeItem(LS_PHONE);
  }, []);

  // 手动 SSE 解析（铁律：fetch + ReadableStream + AbortController）
  async function consumeSSE(
    res: Response,
    onEvent: (evType: string, data: unknown) => void,
    signal: AbortSignal
  ) {
    if (!res.body) throw new Error("No response body");
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let currentEvent = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (signal.aborted) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) {
            // 空行 = 一个事件结束
            currentEvent = "";
            continue;
          }
          if (trimmed.startsWith("event:")) {
            currentEvent = trimmed.slice(6).trim();
            continue;
          }
          if (trimmed.startsWith("data:")) {
            const dataStr = trimmed.slice(5).trim();
            let data: unknown = null;
            if (dataStr) {
              try {
                data = JSON.parse(dataStr);
              } catch {
                data = { text: dataStr };
              }
            }
            if (currentEvent) {
              onEvent(currentEvent, data);
            }
          }
        }
      }
      // flush 最后可能残留
      if (buffer.trim()) {
        // 极少情况
      }
    } finally {
      reader.releaseLock();
    }
  }

  const startStream = useCallback(
    async (baseMessages: AssistantMessage[], forceTier?: "upgrade") => {
      if (!token) {
        addError({ code: "NO_TOKEN", message: "请先登录手机号" });
        return;
      }
      // abort 旧流
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;

      setIsStreaming(true);
      setCurrentTool(null);
      clearErrors();

      // 准备请求体：只发 role+content（hook 调用方已保证末尾是待填充的 assistant 占位）
      const reqMessages = baseMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      try {
        const res = await fetch("/api/assistant/chat", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            messages: reqMessages,
            ...(forceTier ? { force_tier: forceTier } : {}),
          }),
          signal: ac.signal,
        });

        if (!res.ok) {
          if (res.status === 401) {
            addError({ code: "UNAUTHORIZED", message: "会话已过期，请重新输入手机号" });
            clearSession();
          } else if (res.status === 403) {
            const j = await res.json().catch(() => ({}));
            addError({ code: "FORBIDDEN", message: j?.detail || "配额不足或未授权" });
          } else {
            addError({ code: `HTTP_${res.status}`, message: `请求失败（${res.status}）` });
          }
          setIsStreaming(false);
          return;
        }

        // 流期间始终操作“最后一个消息”（调用方已预先 append 了空的 assistant 占位）
        const handleEvent = (evType: string, data: unknown) => {
          if (ac.signal.aborted) return;

          if (evType === "meta") {
            const d = data as { tier_label?: string; degraded?: boolean; model?: string };
            setMessages((prev) => {
              const idx = prev.length - 1;
              if (idx < 0) return prev;
              const copy = [...prev];
              copy[idx] = {
                ...copy[idx],
                tier_label: d.tier_label,
                degraded: !!d.degraded,
                model: d.model,
              } as AssistantMessage;
              return copy;
            });
          } else if (evType === "delta") {
            const d = data as { text?: string };
            const txt = d?.text ?? "";
            if (!txt) return;
            setMessages((prev) => {
              const idx = prev.length - 1;
              if (idx < 0) return prev;
              const copy = [...prev];
              copy[idx] = {
                ...copy[idx],
                content: (copy[idx].content || "") + txt,
              } as AssistantMessage;
              return copy;
            });
          } else if (evType === "tool") {
            const d = data as { name?: string; status?: string };
            if (d?.status === "calling" && d.name) {
              setCurrentTool(d.name);
            } else if (d?.status === "done") {
              setCurrentTool(null);
            }
          } else if (evType === "done") {
            const d = data as AssistantDoneInfo;
            setLastDone(d);
            setCurrentTool(null);
            setIsStreaming(false);
            abortRef.current = null;
          } else if (evType === "error") {
            const d = data as { code?: string; message?: string };
            addError({ code: d?.code || "STREAM_ERROR", message: d?.message || "服务异常" });
            setCurrentTool(null);
            setIsStreaming(false);
            abortRef.current = null;
          }
        };

        await consumeSSE(res, handleEvent, ac.signal);
      } catch (e: unknown) {
        if ((e as Error)?.name !== "AbortError") {
          addError({ code: "NETWORK", message: "网络或解析错误，请重试" });
        }
      } finally {
        setIsStreaming(false);
        setCurrentTool(null);
        if (abortRef.current === ac) abortRef.current = null;
      }
    },
    [token, addError, clearErrors, clearSession]
  );

  // 正常提问（追加 user + 流）
  const sendQuestion = useCallback(
    (content: string) => {
      const q = content.trim();
      if (!q || isStreaming) return;
      const newMsgs: AssistantMessage[] = [
        ...messages,
        { role: "user", content: q },
        { role: "assistant", content: "" },
      ];
      setMessages(newMsgs);
      void startStream(newMsgs);
    },
    [messages, isStreaming, startStream]
  );

  // 升级重答：二次确认在 UI 层做，这里负责 truncate + 启动 replace 流
  const upgradeAnswer = useCallback(
    (assistantIndex: number) => {
      if (isStreaming || !lastDone) return;
      if (assistantIndex <= 0 || assistantIndex >= messages.length) return;
      const userIdx = assistantIndex - 1;
      if (messages[userIdx]?.role !== "user") return;

      const baseUpToUser: AssistantMessage[] = messages.slice(0, userIdx + 1);
      const withPlaceholder: AssistantMessage[] = [
        ...baseUpToUser,
        { role: "assistant", content: "" },
      ];
      setMessages(withPlaceholder); // 旧回答已被 slice 掉，新占位接在同一 user 后 = 替换语义
      void startStream(withPlaceholder, "upgrade");
    },
    [messages, isStreaming, lastDone, startStream]
  );

  // 供 UI 取“本次可升级剩余次数”
  const getUpgradesLeft = useCallback(() => lastDone?.upgrades_left ?? 0, [lastDone]);

  // 取消当前流
  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setCurrentTool(null);
  }, []);

  // 清理
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  return {
    messages,
    token,
    phone,
    isStreaming,
    currentTool,
    errors,
    lastDone,
    sendQuestion,
    upgradeAnswer,
    getUpgradesLeft,
    login: async (p: string): Promise<{ ok: boolean; err?: string }> => {
      const phone = p.trim();
      if (!/^1\d{10}$/.test(phone)) {
        return { ok: false, err: "手机号格式错误（需 1 开头 11 位数字）" };
      }
      try {
        const res = await fetch("/api/assistant/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phone }),
        });
        if (res.status === 403) {
          const j = await res.json().catch(() => ({}));
          return { ok: false, err: j?.detail || "该手机号未被授权，请联系管理员" };
        }
        if (!res.ok) return { ok: false, err: `登录失败（${res.status}）` };
        const sess = (await res.json()) as AssistantSession;
        setSession(sess, phone);
        return { ok: true };
      } catch {
        return { ok: false, err: "网络错误，请检查后端" };
      }
    },
    logout: clearSession,
    clearErrors,
    cancelStream,
    // 供 Drawer 清空历史（保留 token）
    clearHistory: () => {
      setMessages([]);
      setLastDone(null);
      localStorage.removeItem(LS_MESSAGES);
    },
  };
}

export type UseAssistantChatReturn = ReturnType<typeof useAssistantChat>;
