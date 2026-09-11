import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { ToastProvider } from './components/ui/Toast';
import { Button } from './components/ui/Button';
import Dashboard from './pages/Dashboard';
import Signals from './pages/Signals';
import Stocks from './pages/Stocks';
import StockDetail from './pages/StockDetail';
import Backtest from './pages/Backtest';
import Pool from './pages/Pool';
import Rules from './pages/Rules';
import Settings from './pages/Settings';

interface ErrorBoundaryState {
  error: Error | null;
}

/** 全局错误边界：任何未捕获异常都给出可恢复的中文界面，绝不白屏 */
class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // 仅在前端控制台保留现场，便于排障
    console.error('[界面异常]', error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="content">
          <div className="card">
            <div className="card-body">
              <div className="state state-error" role="alert">
                <div className="state-title">界面渲染出现异常</div>
                <div className="state-desc">
                  已捕获未处理的运行时错误，可尝试重新加载页面；若持续出现请检查后端接口返回是否与接口契约一致。
                  <br />
                  错误信息：{this.state.error.message}
                </div>
                <div className="state-actions">
                  <Button variant="primary" size="sm" onClick={() => this.setState({ error: null })}>
                    重试渲染
                  </Button>
                  <Button size="sm" onClick={() => window.location.reload()}>
                    重新加载页面
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/signals" element={<Signals />} />
              <Route path="/stocks" element={<Stocks />} />
              <Route path="/stock/:code" element={<StockDetail />} />
              <Route path="/backtest" element={<Backtest />} />
              <Route path="/pool" element={<Pool />} />
              <Route path="/rules" element={<Rules />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </ErrorBoundary>
  );
}
