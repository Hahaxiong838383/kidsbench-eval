/**
 * AssistantFab（全局右下浮动按钮）
 * 样式跟随项目（emerald 强调）
 */
interface Props {
  onClick: () => void;
  hasUnread?: boolean;
}

export default function AssistantFab({ onClick, hasUnread }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="打开 AI 助手"
      className="fixed bottom-5 right-5 z-[100] flex h-12 w-12 items-center justify-center rounded-full bg-emerald-600 text-white shadow-lg ring-1 ring-emerald-700/30 hover:bg-emerald-700 active:scale-95 transition"
    >
      <span className="text-xl leading-none select-none">💬</span>
      {hasUnread && (
        <span className="absolute -top-0.5 -right-0.5 h-3 w-3 rounded-full bg-amber-400 ring-2 ring-white" />
      )}
    </button>
  );
}
