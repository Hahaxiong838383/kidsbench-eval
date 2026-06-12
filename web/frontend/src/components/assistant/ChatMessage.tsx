/**
 * ChatMessage（单条消息渲染）
 * - 档位角标：tier_label 直接显示
 * - degraded：角标黄底“已降级”
 * - 仅 assistant 显示「用强模型重答」按钮（调用 upgradeAnswer）
 * - tool 事件由父层独立渲染，这里只负责消息本身
 */
import type { AssistantMessage } from "../../lib/types";

interface Props {
  msg: AssistantMessage;
  index: number; // assistant 的 index（用于 upgrade）
  canUpgrade?: boolean;
  upgradesLeft?: number;
  onUpgrade?: (idx: number) => void;
  isStreaming?: boolean;
}

export default function ChatMessage({
  msg,
  index,
  canUpgrade,
  upgradesLeft,
  onUpgrade,
  isStreaming,
}: Props) {
  const isUser = msg.role === "user";

  const tierBadge = msg.tier_label ? (
    <span
      className={
        "inline-block text-[10px] px-1.5 py-0.5 rounded-full border " +
        (msg.degraded
          ? "bg-amber-100 text-amber-700 border-amber-200"
          : "bg-emerald-50 text-emerald-700 border-emerald-200")
      }
    >
      {msg.tier_label}
      {msg.degraded ? " · 已降级" : ""}
    </span>
  ) : null;

  const handleUpgrade = () => {
    if (onUpgrade && !isStreaming) onUpgrade(index);
  };

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}>
      <div
        className={
          "max-w-[82%] rounded-2xl px-3 py-2 text-sm " +
          (isUser
            ? "bg-emerald-600 text-white"
            : "bg-white border border-slate-200 text-slate-800")
        }
      >
        {!isUser && tierBadge && <div className="mb-1">{tierBadge}</div>}

        <div className="whitespace-pre-wrap break-words leading-relaxed">
          {msg.content || (isStreaming && !isUser ? "…" : "")}
        </div>

        {!isUser && canUpgrade && onUpgrade && (
          <div className="mt-2">
            <button
              type="button"
              onClick={handleUpgrade}
              disabled={isStreaming}
              className="text-[11px] px-2 py-0.5 rounded border border-amber-300 text-amber-700 hover:bg-amber-50 disabled:opacity-50"
              title={upgradesLeft != null ? `今日剩余 ${upgradesLeft} 次强模型升级` : undefined}
            >
              用强模型重答
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
