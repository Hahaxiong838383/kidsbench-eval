/**
 * 错误边界（避免任一页面渲染崩溃导致整站"消失"/白屏）
 *
 * React ErrorBoundary 必须是 class 组件。
 * 任一子组件抛错 → 显示降级 UI（错误详情 + 强刷 + 重试），nav 仍可用。
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 打到 console 便于排查（生产可接 Sentry 等）
    console.error("[ErrorBoundary] 页面渲染出错:", error, info.componentStack);
  }

  private reset = () => this.setState({ hasError: false, error: null });

  render() {
    if (this.state.hasError) {
      return (
        <div className="card border-l-4 border-l-rose-500">
          <h2 className="text-lg font-semibold text-rose-700">页面渲染出错</h2>
          <p className="text-sm text-slate-600 mt-2">
            这个页面遇到了一个错误。多数情况是浏览器缓存了旧版本，
            <strong>强制刷新</strong>即可恢复（Mac: Cmd+Shift+R / Win: Ctrl+Shift+R）。
          </p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded text-sm"
            >
              强制刷新
            </button>
            <button
              type="button"
              onClick={this.reset}
              className="bg-slate-100 hover:bg-slate-200 text-slate-700 px-4 py-2 rounded text-sm"
            >
              重试本页
            </button>
          </div>
          {this.state.error && (
            <details className="mt-3 text-xs text-slate-500">
              <summary className="cursor-pointer">错误详情（给 cc 排查用）</summary>
              <pre className="mt-2 bg-slate-50 p-2 rounded overflow-x-auto whitespace-pre-wrap">
                {this.state.error.message}
                {"\n"}
                {this.state.error.stack?.slice(0, 500)}
              </pre>
            </details>
          )}
        </div>
      );
    }
    return this.props.children;
  }
}
