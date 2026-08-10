import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
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
import { WorkspaceNavigation } from './components/workspace/WorkspaceNavigation';
import { WorkspaceResourceView } from './components/workspace/WorkspaceResourceView';
import {
  normalizeWorkspacePrimaryView,
  type WorkspacePrimaryView,
} from './components/workspace/workspaceResourceTypes';
import {
  buildProjectLinkedPath,
  buildWorkspaceViewPath,
} from './components/workspace/workspaceViewNavigation';

const CoCreationLogin = lazy(() => import('./components/CoCreationLogin').then((module) => ({ default: module.CoCreationLogin })));
const LandingPage = lazy(() => import('./components/LandingPage').then((module) => ({ default: module.LandingPage })));
const GptWorkspace = lazy(() => import('./components/GptWorkspace').then((module) => ({ default: module.GptWorkspace })));

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
 * AI 共创设计工作台：产品层只有一个 Workspace。
 *
 * /workspace                         默认 Conversation
 * /workspace/:conversationId         恢复 Conversation（仍是旧稳定 GptWorkspace 主链）
 * /workspace?view=projects           Workspace 内项目档案馆
 * /workspace?view=files              Workspace 内文件中心
 * /workspace?view=assets             Workspace 内 AI 资产中心
 * /workspace?view=versions           Workspace 内版本中心
 * /workspace?view=quotes             Workspace 内报价中心
 *
 * 旧 /projects /assets /quotes 仅做兼容跳转，不再作为产品一级页面。
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
  }, [handleLoginSuccess, isIframe]);

  const handleLogout = useCallback(async (): Promise<void> => {
    setLogoutError(null);
    const error = await runLogout(
      () => sessionService.logout(),
      () => setAuth({ user: null, isLoading: false, isIframe: false }),
    );
    setLogoutError(error);
  }, []);

  const userLabel = auth.user?.displayName || auth.user?.username || '';

  const protectedShell = (
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
      <Route path="/workspace/:conversationId" element={protectedShell} />
      <Route path="/workspace" element={protectedShell} />

      {/* 旧地址只做兼容，不再进入独立业务页。 */}
      <Route path="/projects" element={<Navigate to="/workspace?view=projects" replace />} />
      <Route path="/assets" element={<Navigate to="/workspace?view=assets" replace />} />
      <Route path="/quotes" element={<Navigate to="/workspace?view=quotes" replace />} />
      <Route path="/files" element={<Navigate to="/workspace?view=files" replace />} />
      <Route path="/versions" element={<Navigate to="/workspace?view=versions" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
};

const StandaloneShell: React.FC<{
  isIframe: boolean;
  userLabel: string;
  onLogout: () => Promise<void>;
  logoutError: string | null;
}> = ({ isIframe, userLabel, onLogout, logoutError }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [newChatKey, setNewChatKey] = useState(0);

  const pathConversationId = (location.pathname.match(/^\/workspace\/([^/]+)/) || [])[1] || null;
  const activeView = normalizeWorkspacePrimaryView(searchParams.get('view'));
  const projectId = searchParams.get('project');
  const projectName = searchParams.get('name');
  const initialPrompt = searchParams.get('prompt');
  const initialPreview = searchParams.get('preview');
  const initialConversationId = searchParams.get('cid') || pathConversationId;
  const resourceProjectId = searchParams.get('archiveProject');

  const setWorkspaceView = useCallback((next: WorkspacePrimaryView) => {
    navigate(
      buildWorkspaceViewPath({
        pathname: location.pathname,
        search: location.search,
        next,
      }),
      { replace: false },
    );
  }, [location.pathname, location.search, navigate]);

  const handleNewChat = useCallback(() => {
    setNewChatKey((prev) => prev + 1);
    navigate('/workspace', { replace: false });
  }, [navigate]);

  useEffect(() => {
    if (!initialPrompt) return;
    const params = new URLSearchParams(searchParams);
    params.delete('prompt');
    setSearchParams(params, { replace: true });
  }, [initialPrompt, searchParams, setSearchParams]);

  const handleProjectLinked = useCallback((linkedProjectId: string, linkedProjectName: string) => {
    navigate(
      buildProjectLinkedPath({
        pathname: location.pathname,
        search: location.search,
        projectId: linkedProjectId,
        projectName: linkedProjectName,
      }),
      { replace: true },
    );
  }, [location.pathname, location.search, navigate]);

  const handleOpenConversation = useCallback((conversationId: string, title: string) => {
    const params = new URLSearchParams();
    if (title) params.set('name', title);
    navigate(`/workspace/${encodeURIComponent(conversationId)}${params.toString() ? `?${params.toString()}` : ''}`);
  }, [navigate]);

  const handleOpenResourceProject = useCallback((pid: string) => {
    const params = new URLSearchParams(searchParams);
    params.set('view', 'projects');
    params.set('archiveProject', pid);
    setSearchParams(params, { replace: false });
  }, [searchParams, setSearchParams]);

  const handleClearResourceProject = useCallback(() => {
    const params = new URLSearchParams(searchParams);
    params.set('view', 'projects');
    params.delete('archiveProject');
    setSearchParams(params, { replace: true });
  }, [searchParams, setSearchParams]);

  return (
    <div className="flex h-screen overflow-hidden bg-[#f5f5f4]">
      <WorkspaceNavigation
        activeView={activeView}
        userLabel={userLabel}
        onSelectView={setWorkspaceView}
        onNewChat={handleNewChat}
        onOpenConversation={handleOpenConversation}
        onLogout={() => void onLogout()}
      />

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

        <div className="relative flex h-full min-h-0 flex-1 overflow-hidden">
          <Suspense fallback={<SurfaceLoader label="正在加载工作台" />}>
            {/* GptWorkspace 始终保持挂载。切换资源页时只隐藏，避免正在运行的轮询/生成被卸载。 */}
            <div className={activeView === 'chat' ? 'flex h-full min-w-0 flex-1' : 'hidden'}>
              <GptWorkspace
                key={`chat-${newChatKey}`}
                initialPrompt={initialPrompt}
                projectId={projectId}
                projectName={projectName}
                initialPreview={initialPreview}
                initialConversationId={initialConversationId}
                onProjectLinked={handleProjectLinked}
                onNavigateHome={() => navigate('/')}
                externalResourceCenter
              />
            </div>

            {activeView !== 'chat' ? (
              <WorkspaceResourceView
                view={activeView}
                selectedProjectId={resourceProjectId}
                onBackToConversation={() => setWorkspaceView('chat')}
                onOpenProject={(project) => handleOpenResourceProject(project.id)}
                onClearProject={handleClearResourceProject}
                onOpenConversation={handleOpenConversation}
              />
            ) : null}
          </Suspense>
        </div>

        {activeView === 'chat' && projectId ? (
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
