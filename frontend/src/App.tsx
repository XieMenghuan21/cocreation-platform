import React, { Suspense, lazy, useState, useEffect, useCallback } from 'react';
import {
  runLogout,
  sessionService,
  type SessionUser,
} from './services/sessionService';
import {
  configuredSsoParentOrigin,
  consumeUrlSessionToken,
  isTrustedParentMessage,
  sessionBootstrap,
} from './services/sessionBootstrap';

const CoCreationLogin = lazy(() => import('./components/CoCreationLogin').then((module) => ({ default: module.CoCreationLogin })));
const CoCreationHistoryPage = lazy(() => import('./components/CoCreationHistoryPage').then((module) => ({ default: module.CoCreationHistoryPage })));
const CoCreationAgentWorkspace = lazy(() => import('./components/CoCreationAgentWorkspace'));

interface AuthState {
  user: SessionUser | null;
  isLoading: boolean;
  isIframe: boolean;
}

const SurfaceLoader: React.FC<{ label: string }> = ({ label }) => (
  <div className="flex min-h-[420px] items-center justify-center px-6">
    <div className="flex max-w-sm flex-col items-center gap-4 rounded-[28px] border border-white/70 bg-white/88 px-8 py-9 text-center shadow-[0_24px_80px_rgba(15,23,42,0.08)] backdrop-blur-xl">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-950 text-white">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/30 border-t-white" />
      </div>
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">Loading Surface</div>
        <div className="mt-2 text-sm font-semibold text-slate-900">{label}</div>
      </div>
    </div>
  </div>
);

/**
 * 共创工作台独立应用
 * 支持两种认证模式：
 * 1. 独立访问：显示登录页面
 * 2. iframe 内嵌：通过 postMessage 接收主平台 token（SSO）
 */
export const CoCreationStandaloneApp: React.FC = () => {
  const [auth, setAuth] = useState<AuthState>({
    user: null,
    isLoading: true,
    isIframe: false,
  });
  const [logoutError, setLogoutError] = useState<string | null>(null);

  const isIframe = (() => {
    try { return window.self !== window.top; } catch { return true; }
  })();

  const handleLoginSuccess = useCallback((user: SessionUser) => {
    setAuth({ user, isLoading: false, isIframe });
  }, [isIframe]);

  useEffect(() => {
    const urlToken = consumeUrlSessionToken();
    const onSessionError = (): void => {
      setAuth((previous) => ({ ...previous, isLoading: false }));
    };
    const unsubscribeBootstrap = sessionBootstrap.subscribeBootstrap(
      urlToken,
      handleLoginSuccess,
      onSessionError,
    );

    const handleMessage = (event: MessageEvent<unknown>): void => {
      if (
        !isIframe
        || !isTrustedParentMessage(
          event,
          configuredSsoParentOrigin,
          window.parent,
        )
        || !event.data
        || typeof event.data !== 'object'
      ) return;
      const data = event.data as { type?: unknown; token?: unknown };
      if (data.type === 'cocreation-sso' && typeof data.token === 'string') {
        sessionBootstrap.exchangeFromParent(
          data.token,
          handleLoginSuccess,
          onSessionError,
        );
      }
    };

    if (isIframe && configuredSsoParentOrigin) {
      window.addEventListener('message', handleMessage);
      window.parent.postMessage(
        { type: 'cocreation-auth-request' },
        configuredSsoParentOrigin,
      );
    }

    return () => {
      unsubscribeBootstrap();
      window.removeEventListener('message', handleMessage);
    };
  }, [handleLoginSuccess, isIframe]);

  const handleLogout = useCallback(async (): Promise<void> => {
    setLogoutError(null);
    const error = await runLogout(
      () => sessionService.logout(),
      () => setAuth({ user: null, isLoading: false, isIframe: false }),
    );
    setLogoutError(error);
  }, []);

  if (auth.isLoading) {
    return (
      <div className="relative flex h-screen items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(59,130,246,0.16),_transparent_22%),linear-gradient(180deg,#f8fafc_0%,#e9f0f7_100%)] px-6">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_80%_18%,rgba(15,23,42,0.08),transparent_18%)]" />
        <div className="relative flex min-w-[280px] max-w-sm flex-col items-center gap-5 rounded-[28px] border border-white/70 bg-white/88 px-8 py-9 text-center shadow-[0_24px_80px_rgba(15,23,42,0.12)] backdrop-blur-xl">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-950 text-white shadow-lg shadow-slate-900/15">
            <div className="h-7 w-7 animate-spin rounded-full border-4 border-white/30 border-t-white" />
          </div>
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.26em] text-slate-400">System Gate</div>
            <div className="mt-2 text-lg font-semibold text-slate-900">正在验证身份</div>
            <span className="mt-2 block text-sm leading-6 text-slate-500">系统正在同步账号状态并恢复当前工作空间。</span>
          </div>
        </div>
      </div>
    );
  }

  if (!auth.user) {
    return (
      <Suspense fallback={<SurfaceLoader label="正在加载登录页" />}>
        <CoCreationLogin onLogin={handleLoginSuccess} />
      </Suspense>
    );
  }

  return (
    <StandaloneShell
      isIframe={auth.isIframe}
      userLabel={auth.user?.displayName || auth.user?.username || ''}
      onLogout={handleLogout}
      logoutError={logoutError}
    />
  );
};

const StandaloneShell: React.FC<{
  isIframe: boolean;
  userLabel: string;
  onLogout: () => Promise<void>;
  logoutError: string | null;
}> = ({ isIframe, userLabel, onLogout, logoutError }) => {
  const [view, setView] = useState<'workspace' | 'projects' | 'assets'>(() => {
    if (window.location.hash === '#projects') return 'projects';
    if (window.location.hash === '#assets') return 'assets';
    return 'workspace';
  });

  useEffect(() => {
    const onHash = () => {
      if (window.location.hash === '#projects') return setView('projects');
      if (window.location.hash === '#assets') return setView('assets');
      setView('workspace');
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  return (
    <div className="relative min-h-screen bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.12),_transparent_16%),radial-gradient(circle_at_85%_14%,_rgba(14,165,233,0.08),_transparent_18%),linear-gradient(180deg,#f8fafc_0%,#edf3f9_100%)]">
      <div className="px-2 py-2 sm:px-3">
        <div className="w-full overflow-hidden rounded-[34px] border border-white/70 bg-white/82 shadow-[0_24px_90px_rgba(15,23,42,0.08)] backdrop-blur-2xl">
          <div className="sticky top-0 z-50 border-b border-slate-200/80 bg-white/88 backdrop-blur-2xl">
            <div className="flex flex-col gap-4 px-4 py-3 sm:px-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-950 text-sm font-bold text-white shadow-lg shadow-slate-900/10">AI</div>
                <div className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-slate-950">AI 共创设计工作台</span>
                  <span className="block text-[11px] uppercase tracking-[0.22em] text-slate-400">Industrial Creative System</span>
                </div>
                {isIframe ? (
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[10px] font-semibold text-emerald-700">SSO</span>
                ) : null}
              </div>
              <div className="flex items-center gap-3">
                <span className="max-w-[180px] truncate text-xs text-slate-500 sm:max-w-none">{userLabel}</span>
                <button
                  onClick={onLogout}
                  className="rounded-xl border border-slate-200/80 bg-white/80 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 hover:text-slate-900"
                >
                  退出
                </button>
              </div>
            </div>

            <div className="overflow-x-auto">
              <div className="inline-flex min-w-full items-center gap-1 rounded-2xl border border-slate-200/80 bg-slate-100/70 p-1.5 sm:min-w-0">
                <button
                  onClick={() => { setView('workspace'); window.location.hash = ''; }}
                  className={`flex-1 whitespace-nowrap rounded-xl px-4 py-2 text-sm font-semibold transition sm:flex-none ${view === 'workspace' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:bg-white/60 hover:text-slate-700'}`}
                >
                  工作台
                </button>
                <button
                  onClick={() => { setView('projects'); window.location.hash = 'projects'; }}
                  className={`flex-1 whitespace-nowrap rounded-xl px-4 py-2 text-sm font-semibold transition sm:flex-none ${view === 'projects' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:bg-white/60 hover:text-slate-700'}`}
                >
                  项目库
                </button>
                <button
                  onClick={() => { setView('assets'); window.location.hash = 'assets'; }}
                  className={`flex-1 whitespace-nowrap rounded-xl px-4 py-2 text-sm font-semibold transition sm:flex-none ${view === 'assets' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:bg-white/60 hover:text-slate-700'}`}
                >
                  资产库
                </button>
              </div>
            </div>
          </div>
          </div>
          <div className="bg-[linear-gradient(180deg,rgba(248,250,252,0.72)_0%,rgba(237,243,249,0.94)_100%)]">
            {logoutError ? (
              <div className="mx-4 mt-4 flex items-center justify-between gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                <span>{logoutError}</span>
                <button
                  type="button"
                  onClick={() => void onLogout()}
                  className="rounded-xl border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold"
                >
                  重试退出
                </button>
              </div>
            ) : null}
            <Suspense fallback={<SurfaceLoader label={view === 'workspace' ? '正在加载工作台' : '正在加载内容页'} />}>
              {view === 'workspace' ? (
                <CoCreationAgentWorkspace variant="standalone" />
              ) : (
                <CoCreationHistoryPage
                  view={view}
                  onBack={() => { setView('workspace'); window.location.hash = ''; }}
                />
              )}
            </Suspense>
          </div>
        </div>
      </div>
    </div>
  );
};
