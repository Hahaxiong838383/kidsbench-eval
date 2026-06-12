/**
 * AssistantDrawer（侧边抽屉主组件）
 * - 右滑抽屉（fixed + transform）
 * - 含 PhoneGate 或完整 chat
 * - 手动 SSE 由 useAssistantChat 提供
 * - 错误条（红/黄）、tool 灰条、升级二次确认
 * - 历史存 state+localStorage（hook 负责）
 */
import { useEffect, useRef, useState } from "react";
import { useAssistantChat } from "./useAssistantChat";
import PhoneGate from "./PhoneGate";
import ChatMessage from "./ChatMessage";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function AssistantDrawer({ open, onClose }: Props) {
  const chat = useAssistantChat();
  const [input, setInput] = useState("");
  const [upgradeConfirm, setUpgradeConfirm] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 打开时滚动到底 + 聚焦
  useEffect(() => {
    if (open && chat.token) {
      // 下一帧滚
      requestAnimationFrame(() => scrollToBottom());
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open, chat.token]);

  // 消息或 streaming 变化时滚底
  useEffect(() => {
    if (open) scrollToBottom();
  }, [chat.messages, chat.isStreaming, open, chat.currentTool]);

  function scrollToBottom() {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }

  function onSend() {
    const q = input.trim();
    if (!q || chat.isStreaming) return;
    chat.sendQuestion(q);
    setInput("");
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  }

  function requestUpgrade(idx: number) {
    setUpgradeConfirm(idx);
  }

  function confirmUpgrade() {
    if (upgradeConfirm != null) {
      chat.upgradeAnswer(upgradeConfirm);
    }
    setUpgradeConfirm(null);
  }

  const upgradesLeft = chat.getUpgradesLeft();

  const maskedPhone = (p: string | null) =>
    p ? p.replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2") : "";

  return (
    <>
      {/* 遮罩 */}
      {open && (
        <div
          className="fixed inset-0 z-[90] bg-black/30"
          onClick={onClose}
          aria-hidden
        />
      )}

      {/* 抽屉 */}
      <aside
        className={`fixed right-0 top-0 bottom-0 z-[95] w-[380px] max-w-[92vw] bg-slate-50 border-l border-slate-200 shadow-xl flex flex-col transition-transform duration-200 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        aria-hidden={!open}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-4 py-3 border-b bg-white">
          <div>
            <div className="font-semibold text-emerald-700">KidsBench AI 助手</div>
            <div className="text-[11px] text-slate-500">三档路由 · 配额管控</div>
          </div>
          <div className="flex items-center gap-2">
            {chat.token && chat.phone && (
              <span className="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600">
                {maskedPhone(chat.phone)}
              </span>
            )}
            <button
              onClick={onClose}
              className="text-slate-500 hover:text-slate-700 px-2 py-1 text-lg leading-none"
              aria-label="关闭"
            >
              ×
            </button>
          </div>
        </div>

        {/* 主体 */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {!chat.token ? (
            <PhoneGate chat={chat} />
          ) : (
            <>
              {/* 消息区 */}
              <div
                ref={scrollRef}
                className="flex-1 overflow-y-auto px-3 py-3 space-y-1 bg-slate-50"
              >
                {chat.messages.length === 0 && (
                  <div className="text-xs text-slate-500 px-2 py-8 text-center">
                    你好！我是 KidsBench 评测助手。
                    <br />
                    支持题库查询、诊断分析与强模型重答。
                  </div>
                )}

                {chat.messages.map((m, i) => (
                  <ChatMessage
                    key={i}
                    msg={m}
                    index={i}
                    canUpgrade={m.role === "assistant"}
                    upgradesLeft={upgradesLeft}
                    onUpgrade={requestUpgrade}
                    isStreaming={chat.isStreaming && i === chat.messages.length - 1}
                  />
                ))}

                {/* tool 进行中灰条 */}
                {chat.currentTool && (
                  <div className="mx-1 my-2 rounded border border-slate-200 bg-slate-100 px-3 py-1 text-xs text-slate-600">
                    正在查询：<span className="font-mono">{chat.currentTool}</span>…
                  </div>
                )}

                {/* 错误提示条（独立，不混正文） */}
                {chat.errors.length > 0 && (
                  <div className="space-y-2 px-1 pt-1">
                    {chat.errors.map((e, idx) => (
                      <div
                        key={idx}
                        className={
                          "rounded px-3 py-2 text-xs flex justify-between gap-2 " +
                          (e.code.includes("QUOTA") || e.code.includes("FORBIDDEN")
                            ? "bg-amber-50 border border-amber-200 text-amber-700"
                            : "bg-rose-50 border border-rose-200 text-rose-700")
                        }
                      >
                        <div>
                          <span className="font-medium">{e.code}</span>：{e.message}
                        </div>
                        <button
                          onClick={() => {
                            // 简单移除该条（父层 errors 是只读，这里用 clear 简化）
                            chat.clearErrors();
                          }}
                          className="opacity-60 hover:opacity-100"
                        >
                          忽略
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* 输入区 */}
              <div className="border-t bg-white p-3">
                <div className="flex gap-2">
                  <textarea
                    ref={inputRef}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={onKeyDown}
                    placeholder="输入问题（Enter 发送，Shift+Enter 换行）"
                    rows={2}
                    className="input resize-y min-h-[42px] max-h-28 flex-1"
                    disabled={chat.isStreaming}
                  />
                  <button
                    onClick={onSend}
                    disabled={chat.isStreaming || !input.trim()}
                    className="self-end px-4 py-2 rounded bg-emerald-600 text-white text-sm hover:bg-emerald-700 disabled:opacity-50 whitespace-nowrap"
                  >
                    {chat.isStreaming ? "思考中" : "发送"}
                  </button>
                </div>

                <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                  <div>
                    {chat.isStreaming && "正在生成回答…"}
                    {!chat.isStreaming && chat.lastDone && (
                      <span>
                        剩余配额 {chat.lastDone.quota_left} · 升级 {upgradesLeft}
                      </span>
                    )}
                  </div>
                  <div className="flex gap-3">
                    <button
                      onClick={chat.clearHistory}
                      className="hover:text-slate-700 underline decoration-dotted"
                    >
                      清空历史
                    </button>
                    <button
                      onClick={chat.logout}
                      className="hover:text-slate-700 underline decoration-dotted"
                    >
                      退出登录
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* 底部小注 */}
        <div className="px-3 py-1.5 text-[10px] text-slate-400 border-t bg-white">
          回答仅供参考 · 三档自动路由 · 强模型需配额
        </div>
      </aside>

      {/* 升级二次确认 */}
      {upgradeConfirm != null && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40">
          <div className="w-[300px] rounded-lg bg-white border border-slate-200 shadow-xl p-4 text-sm">
            <div className="font-medium mb-2">确认升级重答？</div>
            <div className="text-slate-600 mb-3">
              将消耗<strong>强模型配额</strong>，今日剩余 <strong>{upgradesLeft}</strong> 次。
              <br />
              同一问题将替换原回答。
            </div>
            <div className="flex justify-end gap-2">
              <button
                className="px-3 py-1 rounded border text-sm"
                onClick={() => setUpgradeConfirm(null)}
              >
                取消
              </button>
              <button
                className="px-3 py-1 rounded bg-amber-600 text-white text-sm hover:bg-amber-700"
                onClick={confirmUpgrade}
              >
                确认重答（强模型）
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
