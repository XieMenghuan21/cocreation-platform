import React, { Suspense, lazy, useState, useEffect, useCallback } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
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
import type { GptView } from './components/GptSidebar';

const CoCreationLogin = lazy(() => import('./components/CoCreationLogin').then((module) => ({ default: module.CoCreationLogin })));
const LandingPage = lazy(() => import('./components/LandingPage').then((module) => ({ default: module.LandingPage })));
const CoCreationHistoryPage = lazy(() => import('./components/CoCreationHistoryPage').then((module) => ({ default: module.CoCreationHistoryPage })));
const GptSidebar = lazy(() => import('./components/GptSidebar').then((module) => ({ default: module.GptSidebar })));
const GptWorkspace = lazy(() => import('./components/GptWorkspace').then((module) => ({ default: module.GptWorkspace })));
const QuotesPage = lazy(() => import('./components/QuotesPage').then((module) => ({ default: module.QuotesPage })));
const WorkspaceShell = lazy(() => import('./components/workspace/WorkspaceShell').then((module) => ({ default: module.WorkspaceShell })));

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

const LandingRoute: React.FC<{
  userLabel: string | null;
  isAuthenticating: boolean;
  isIframe: boolean;
}> = ({ userLabel, isAuthenticating, isIframe }) => {
  const navigate = useNavigate();
  return (
    <Suspense fallback={<SurfaceLoader label="正在加载首页" />}>
      <LandingPage
        userLabel={userLabel}
        isAuthenticating={isAuthenticating}
        isIframe={isIframe}
        onEnter={() => navigate('/workspace')}
        onLogin={() => navigate('/login')}
        onSubmitPrompt={(prompt) => {
          navigate(`/workspace?graph=1&prompt=${encodeURIComponent(prompt)}`);
        }}
      />
    </Suspense>
  );
};

const ProtectedRoute: React.FC<{
  user: SessionUser | null;
  isLoading: boolean;
  children: React.ReactNode;
}> = ({ user, isLoading, children }) => {
  if (isLoading) {
    return <SurfaceLoader label="正在恢复会话" />;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

const LoginRoute: React.FC<{
  user: SessionUser | null;
  onLogin: (user: SessionUser) => void;
}> = ({ user, onLogin }) => {
  const navigate = useNavigate();
  if (user) {
    return <Navigate to="/workspace" replace />;
  }
  return (
    <Suspense fallback={<SurfaceLoader label="正在加载登录页" />}>
      <CoCreationLogin
        onLogin={onLogin}
        onBack={() => navigate('/')}
      />
    </Suspense>
  );
};

/**
 * 共创工作台独立应用
 * 支持两种认证模式：
 * 1. 独立访问：显示登录页面
 * 2. iframe 内嵌：通过 postMessage 接收主平台 token（SSO）
 *
 * 路由：
 * - /           首页
 * - /login      登录
 * - /workspace  工作台
 * - /projects   项目库
 * - /assets     资产库
 * - /quotes     报价
 */
export const CoCreationStandaloneApp: React.FC = () => {
  const [auth, setAuth] = useState<AuthState>({
    user: null,
    isLoading: true,
    isIframe: false,
  });
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const navigate = useNavigate();

  const isIframe = (() => {
    try { return window.self !== window.top; } catch { return true; }
  })();

  const handleLoginSuccess = useCallback((user: SessionUser) => {
    setAuth({ user, isLoading: false, isIframe });
    navigate('/workspace');
  }, [isIframe, navigate]);

  const handleSessionRestored = useCallback((user: SessionUser) => {
    setAuth({ user, isLoading: false, isIframe });
  }, [isIframe]);

  useEffect(() => {
    const urlToken = consumeUrlSessionToken();
    const onSessionError = (): void => {
      setAuth((previous) => ({ ...previous, isLoading: false }));
    };
    const unsubscribeBootstrap = sessionBootstrap.subscribeBootstrap(
      urlToken,
      handleSessionRestored,
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
      () => {
        setAuth({ user: null, isLoading: false, isIframe: false });
      },
    );
    setLogoutError(error);
  }, []);

  const userLabel = auth.user?.displayName || auth.user?.username || '';

  return (
    <Routes>
      <Route
        path="/"
        element={
          <LandingRoute
            userLabel={auth.user ? userLabel : null}
            isAuthenticating={auth.isLoading}
            isIframe={auth.isIframe}
          />
        }
      />
      <Route
        path="/login"
        element={
          <LoginRoute user={auth.user} onLogin={handleLoginSuccess} />
        }
      />
      <Route
        path="/workspace/:conversationId"
        element={
          <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
            <StandaloneShell
              isIframe={auth.isIframe}
              userLabel={userLabel}
              onLogout={handleLogout}
              logoutError={logoutError}
              graphMode
            />
          </ProtectedRoute>
        }
      />
      <Route
        path="/workspace"
        element={
          <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
            <StandaloneShell
              isIframe={auth.isIframe}
              userLabel={userLabel}
              onLogout={handleLogout}
              logoutError={logoutError}
            />
          </ProtectedRoute>
        }
      />
      <Route
        path="/projects"
        element={
          <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
            <StandaloneShell
              isIframe={auth.isIframe}
              userLabel={userLabel}
              onLogout={handleLogout}
              logoutError={logoutError}
            />
          </ProtectedRoute>
        }
      />
      <Route
        path="/assets"
        element={
          <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
            <StandaloneShell
              isIframe={auth.isIframe}
              userLabel={userLabel}
              onLogout={handleLogout}
              logoutError={logoutError}
            />
          </ProtectedRoute>
        }
      />
      <Route
        path="/quotes"
        element={
          <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
            <StandaloneShell
              isIframe={auth.isIframe}
              userLabel={userLabel}
              onLogout={handleLogout}
              logoutError={logoutError}
            />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
};

const StandaloneShell: React.FC<{
  isIframe: boolean;
  userLabel: string;
  onLogout: () => Promise<void>;
  logoutError: string | null;
  graphMode?: boolean;
}> = ({ isIframe, userLabel, onLogout, logoutError, graphMode = false }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [newChatKey, setNewChatKey] = useState(0);
  const graphConversationId = (location.pathname.match(/^\/workspace\/([^/]+)/) || [])[1];
  const isGraphMode = graphMode || searchParams.get('graph') === '1';
  const view: GptView = location.pathname.startsWith('/projects')
    ? 'projects'
    : location.pathname.startsWith('/assets')
      ? 'assets'
      : location.pathname.startsWith('/quotes')
        ? 'quotes'
        : 'workspace';

  const projectId = searchParams.get('project');
  const initialPrompt = searchParams.get('prompt');
  const initialPreview = searchParams.get('preview');
  const initialConversationId = searchParams.get('cid');

  const handleNewChat = useCallback(() => {
    setNewChatKey((prev) => prev + 1);
    navigate('/workspace?graph=1', { replace: true });
  }, [navigate]);

  useEffect(() => {
    if (initialPrompt) {
      const params = new URLSearchParams(searchParams);
      params.delete('prompt');
      setSearchParams(params, { replace: true });
    }
  }, [initialPrompt, searchParams, setSearchParams]);

  const handleProjectLinked = useCallback((linkedProjectId: string) => {
    if (view === 'workspace') {
      const params = new URLSearchParams(searchParams);
      params.set('project', linkedProjectId);
      params.delete('prompt');
      setSearchParams(params, { replace: true });
    }
  }, [view, searchParams, setSearchParams]);

  return (
    <div className="flex h-screen overflow-hidden bg-[#f5f5f4]">
      <Suspense fallback={null}>
        <GptSidebar
          view={view}
          userLabel={userLabel}
          onNavigate={(next) => navigate(next === 'workspace' ? '/workspace' : `/${next}`)}
          onNewChat={handleNewChat}
          onOpenProject={(pid, name, imageUrl) => {
            const params = new URLSearchParams();
            params.set('project', pid);
            params.set('name', name);
            if (imageUrl) params.set('preview', imageUrl);
            navigate(`/workspace?${params.toString()}`);
          }}
          onOpenConversation={(conversationId, title) => {
            const params = new URLSearchParams();
            params.set('name', title);
            navigate(`/workspace/${conversationId}?${params.toString()}`);
          }}
          onLogout={() => void onLogout()}
          activeProjectId={projectId}
        />
      </Suspense>

      <main className="flex h-full min-w-0 flex-1 flex-col">
        {logoutError ? (
          <div className="flex items-center justify-between gap-3 border-b border-rose-200 bg-rose-50 px-5 py-2.5 text-sm text-rose-700">
            <span>{logoutError}</span>
            <button
              type="button"
              onClick={() => void onLogout()}
              className="rounded-xl border border-rose-200 bg-white px-3 py-1 text-xs font-semibold"
            >
              重试退出
            </button>
          </div>
        ) : null}

        <div className="flex h-full min-h-0 flex-1">
          <Suspense fallback={<SurfaceLoader label="正在加载" />}>
            {isGraphMode ? (
              <WorkspaceShell
                key={newChatKey}
                conversationId={graphConversationId}
                initialPrompt={initialPrompt}
                onConversationChanged={(cid, title) => {
                  const params = new URLSearchParams();
                  if (title) params.set('name', title);
                  navigate(`/workspace/${cid}${params.toString() ? `?${params.toString()}` : ''}`, { replace: true });
                }}
                onNewChat={() => {
                  setNewChatKey((prev) => prev + 1);
                  navigate('/workspace?graph=1', { replace: true });
                }}
              />
            ) : view === 'workspace' ? (
              <GptWorkspace
                key={newChatKey}
                initialPrompt={initialPrompt}
                projectId={projectId}
                projectName={searchParams.get('name')}
                initialPreview={initialPreview}
                initialConversationId={initialConversationId}
                onProjectLinked={handleProjectLinked}
                onNavigateHome={() => navigate('/')}
              />
            ) : view === 'quotes' ? (
              <QuotesPage />
            ) : (
              <CoCreationHistoryPage
                view={view}
                onBack={() => navigate('/workspace')}
              />
            )}
          </Suspense>
        </div>

        {view === 'workspace' && projectId ? (
          <div className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-200 bg-white px-5 py-2">
            <div className="min-w-0 flex items-center gap-2">
              <span className="truncate text-xs font-medium text-slate-600">
                当前项目：{searchParams.get('name') || projectId}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {isIframe ? (
                <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">SSO</span>
              ) : null}
            </div>
          </div>
        ) : null}
      </main>
    </div>
  );
};