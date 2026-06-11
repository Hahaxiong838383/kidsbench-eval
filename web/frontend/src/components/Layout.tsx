import { NavLink, Outlet, useLocation } from "react-router-dom";
import ErrorBoundary from "./ErrorBoundary";

const NAV = [
  { to: "/", label: "总览" },
  { to: "/adapters", label: "Adapter" },
  { to: "/memory", label: "记忆系统" },
  { to: "/questionbank", label: "题库" },
  { to: "/runs", label: "历史 Run" },
  { to: "/live", label: "实时监控" },
  { to: "/llm", label: "LLM 配置" },
  { to: "/system", label: "系统" },
];

// build 时刻（vite 注入），用于页脚版本提示，帮用户确认是否最新
const BUILD_ID =
  (import.meta.env?.VITE_BUILD_ID as string | undefined) ?? "dev";

export default function Layout() {
  const location = useLocation();
  return (
    <div className="min-h-full flex flex-col">
      <nav className="border-b border-slate-200 bg-white/90 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
          <div className="font-semibold text-emerald-600">KidsBench</div>
          <span className="text-xs text-slate-500">评测白盒平台</span>
          <div className="flex-1" />
          <div className="flex gap-4 text-sm flex-wrap">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) =>
                  isActive
                    ? "text-emerald-700 font-medium"
                    : "text-slate-600 hover:text-slate-900"
                }
              >
                {n.label}
              </NavLink>
            ))}
          </div>
        </div>
      </nav>

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6">
        {/* key=pathname：切换路由时重置 ErrorBoundary，一页崩溃不影响其他页 */}
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>

      <footer className="border-t border-slate-200 py-4 text-xs text-slate-500 text-center">
        KidsBench Web · build {BUILD_ID} · 若页面异常请强刷（Cmd/Ctrl+Shift+R）
      </footer>
    </div>
  );
}
