import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "总览" },
  { to: "/adapters", label: "Adapter" },
  { to: "/memory", label: "记忆系统" },
  { to: "/runs", label: "历史 Run" },
  { to: "/live", label: "实时监控" },
  { to: "/llm", label: "LLM 配置" },
  { to: "/system", label: "系统" },
];

export default function Layout() {
  return (
    <div className="min-h-full flex flex-col">
      <nav className="border-b border-slate-200 bg-white/90 backdrop-blur backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
          <div className="font-semibold text-emerald-600">KidsBench</div>
          <span className="text-xs text-slate-500">B0 · 架构白盒</span>
          <div className="flex-1" />
          <div className="flex gap-4 text-sm">
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
        <Outlet />
      </main>

      <footer className="border-t border-slate-200 py-4 text-xs text-slate-500 text-center">
        KidsBench Web · B0 阶段 · 架构白盒展示
      </footer>
    </div>
  );
}
