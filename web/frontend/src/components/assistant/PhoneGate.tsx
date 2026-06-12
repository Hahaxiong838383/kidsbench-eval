/**
 * PhoneGate（手机号门）
 * - 无 token 时显示
 * - 校验 ^1\d{10}$
 * - 提交 /api/assistant/session
 * - 显示时半遮蔽（展示用）
 */
import { useState } from "react";
import type { UseAssistantChatReturn } from "./useAssistantChat";

interface Props {
  chat: UseAssistantChatReturn;
}

export default function PhoneGate({ chat }: Props) {
  const [input, setInput] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const masked = (p: string | null) =>
    p ? p.replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2") : "";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!/^1\d{10}$/.test(input.trim())) {
      setErr("手机号格式错误（需 1 开头 11 位纯数字）");
      return;
    }
    setLoading(true);
    const res = await chat.login(input.trim());
    setLoading(false);
    if (!res.ok) {
      setErr(res.err || "登录失败");
    } else {
      setInput("");
    }
  }

  return (
    <div className="flex flex-col items-center justify-center h-full p-6 text-center">
      <div className="w-full max-w-xs">
        <div className="text-lg font-semibold mb-2">KidsBench AI 助手</div>
        <p className="text-sm text-slate-500 mb-4">
          输入已授权手机号以开始对话。会话有效期内可直接使用。
        </p>

        <form onSubmit={onSubmit} className="space-y-3">
          <input
            type="tel"
            inputMode="numeric"
            maxLength={11}
            placeholder="13800001234"
            value={input}
            onChange={(e) => setInput(e.target.value.replace(/\D/g, ""))}
            className="input"
            disabled={loading}
            autoFocus
          />
          <button
            type="submit"
            disabled={loading || input.trim().length !== 11}
            className="w-full px-4 py-2 rounded bg-emerald-600 text-white text-sm hover:bg-emerald-700 disabled:opacity-50"
          >
            {loading ? "验证中…" : "开始对话"}
          </button>
        </form>

        {err && <div className="mt-3 text-sm text-red-600">{err}</div>}

        {chat.phone && (
          <div className="mt-4 text-xs text-slate-500">
            当前已登录：{masked(chat.phone)}{" "}
            <button className="underline ml-1" onClick={chat.logout}>
              切换账号
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
