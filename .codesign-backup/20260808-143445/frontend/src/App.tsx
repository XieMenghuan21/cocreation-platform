import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom';
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
import type { WorkspaceResourceSection } from './components/workspace/WorkspaceResourceCenter';

const CoCreationLogin = lazy(() => import('./components/CoCreationLogin').then((module) => ({ default: module.CoCreationLogin })));
const LandingPage = lazy(() => import('./components/LandingPage').then((module) => ({ default: module.LandingPage })));
const GptWorkspace = lazy(() => import('./components/GptWorkspace').then((module) => ({ default: module.GptWorkspace })));
const WorkspaceResourceCenter = lazy(() => import('./components/workspace/WorkspaceResourceCenter').then((module) => ({ default: module.WorkspaceResourceCenter })));

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
          navigate(`/workspace?prompt=${encodeURIComponent(prompt)}`);
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
  if (isLoading) return <SurfaceLoader label="正在恢复会话" />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
};

const LoginRoute: React.FC<{
  user: SessionUser | null;
  onLogin: (user: SessionUser) => void;
}> = ({ user, onLogin }) => {
  const navigate = useNavigate();
  if (user) return <Navigate to="/workspace" replace />;
  return (
    <Suspense fallback={<SurfaceLoader label="正在加载登录页" />}>
      <CoCreationLogin onLogin={onLogin} onBack={() => navigate('/')} />
    </Suspense>
  );
};

/**
 * 产品路由收口：
 * - /                       极简首页
 * - /login                  登录
 * - /workspace              唯一核心工作台
 * - /workspace/:conversationId  历史对话兼容入口，仍进入 GptWorkspace
 * - /projects /assets /quotes   仅做旧链接兼容，重定向回工作台资源中心
 *
 * Workspace Graph 实验代码仍可保留在仓库中，但不再接管生产入口。
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
        || !isTrustedParentMessage(event, configuredSsoParentOrigin, window.parent)
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
  }, [handleLoginSuccess, handleSessionRestored, isIframe]);

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

  const protectedWorkspace = (
    <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
      <StandaloneShell
        isIframe={auth.isIframe}
        userLabel={userLabel}
        onLogout={handleLogout}
        logoutError={logoutError}
      />
    </ProtectedRoute>
  );

  return (
    <Routes>
      <Route
        path="/"
        element={(
          <LandingRoute
            userLabel={auth.user ? userLabel : null}
            isAuthenticating={auth.isLoading}
            isIframe={auth.isIframe}
          />
        )}
      />
      <Route path="/login" element={<LoginRoute user={auth.user} onLogin={handleLoginSuccess} />} />
      <Route path="/workspace" element={protectedWorkspace} />
      <Route path="/workspace/:conversationId" element={protectedWorkspace} />
      <Route
        path="/projects"
        element={(
          <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
            <Navigate to="/workspace?resource=projects" replace />
          </ProtectedRoute>
        )}
      />
      <Route
        path="/assets"
        element={(
          <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
            <Navigate to="/workspace?resource=assets" replace />
          </ProtectedRoute>
        )}
      />
      <Route
        path="/quotes"
        element={(
          <ProtectedRoute user={auth.user} isLoading={auth.isLoading}>
            <Navigate to="/workspace?resource=quotes" replace />
          </ProtectedRoute>
        )}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
};

const isResourceSection = (value: string | null): value is WorkspaceResourceSection =>
  value === 'projects' || value === 'files' || value === 'assets' || value === 'versions' || value === 'quotes';

const StandaloneShell: React.FC<{
  isIframe: boolean;
  userLabel: string;
  onLogout: () => Promise<void>;
  logoutError: string | null;
}> = ({ isIframe, userLabel, onLogout, logoutError }) => {
  const navigate = useNavigate();
  const params = useParams<{ conversationId?: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [newChatKey, setNewChatKey] = useState(0);
  const [resourceRefreshKey, setResourceRefreshKey] = useState(0);

  const projectId = searchParams.get('project');
  const projectName = searchParams.get('name');
  const initialPrompt = searchParams.get('prompt');
  const initialPreview = searchParams.get('preview');
  const queryConversationId = searchParams.get('cid');
  const initialConversationId = queryConversationId || params.conversationId || null;
  const resourceQuery = searchParams.get('resource');
  const activeResource: WorkspaceResourceSection = isResourceSection(resourceQuery) ? resourceQuery : 'projects';

  const handleNewChat = useCallback(() => {
    setNewChatKey((previous) => previous + 1);
    setResourceRefreshKey((previous) => previous + 1);
    navigate('/workspace', { replace: true });
  }, [navigate]);

  useEffect(() => {
    if (!initialPrompt) return;
    const next = new URLSearchParams(searchParams);
    next.delete('prompt');
    setSearchParams(next, { replace: true });
  }, [initialPrompt, searchParams, setSearchParams]);

  const handleProjectLinked = useCallback((linkedProjectId: string, linkedProjectName: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('project', linkedProjectId);
    if (linkedProjectName) next.set('name', linkedProjectName);
    next.delete('prompt');
    setSearchParams(next, { replace: true });
    setResourceRefreshKey((previous) => previous + 1);
  }, [searchParams, setSearchParams]);

  const handleResourceChange = useCallback((section: WorkspaceResourceSection) => {
    const next = new URLSearchParams(searchParams);
    next.set('resource', section);
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const handleOpenProject = useCallback((pid: string, name: string, imageUrl: string | null) => {
    // Switching projects must remount the legacy GptWorkspace so old messages do not
    // leak into the newly selected project. This is a UI reset only; the existing
    // project/history/workflow services remain untouched.
    setNewChatKey((previous) => previous + 1);
    const next = new URLSearchParams();
    next.set('project', pid);
    next.set('name', name);
    next.set('resource', 'projects');
    if (imageUrl) next.set('preview', imageUrl);
    navigate(`/workspace?${next.toString()}`);
  }, [navigate]);

  const handleOpenConversation = useCallback((conversationId: string, title: string) => {
    const next = new URLSearchParams();
    next.set('cid', conversationId);
    next.set('name', title);
    navigate(`/workspace?${next.toString()}`);
  }, [navigate]);

  const handlePreviewUrl = useCallback((url: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('preview', url);
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  return (
    <div className="flex h-screen overflow-hidden bg-[#f5f5f4]">
      <Suspense fallback={null}>
        <WorkspaceResourceCenter
          userLabel={userLabel}
          activeProjectId={projectId}
          activeConversationId={initialConversationId}
          activeSection={activeResource}
          initiallyExpanded={Boolean(resourceQuery)}
          refreshKey={resourceRefreshKey}
          onSectionChange={handleResourceChange}
          onNewChat={handleNewChat}
          onOpenProject={handleOpenProject}
          onOpenConversation={handleOpenConversation}
          onPreviewUrl={handlePreviewUrl}
          onLogout={() => void onLogout()}
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
          <Suspense fallback={<SurfaceLoader label="正在加载工作台" />}>
            <GptWorkspace
              key={`${newChatKey}-${initialConversationId || 'new'}`}
              initialPrompt={initialPrompt}
              projectId={projectId}
              projectName={projectName}
              initialPreview={initialPreview}
              initialConversationId={initialConversationId}
              onProjectLinked={handleProjectLinked}
              externalResourceCenter
              onNavigateHome={() => navigate('/')}
            />
          </Suspense>
        </div>

        {projectId ? (
          <div className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-200 bg-white px-5 py-2">
            <div className="min-w-0 flex items-center gap-2">
              <span className="truncate text-xs font-medium text-slate-600">
                当前项目：{projectName || projectId}
              </span>
            </div>
            {isIframe ? (
              <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">SSO</span>
            ) : null}
          </div>
        ) : null}
      </main>
    </div>
  );
};
