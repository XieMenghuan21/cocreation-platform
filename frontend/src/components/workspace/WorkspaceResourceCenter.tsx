import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Archive,
  Box,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  FileText,
  FolderKanban,
  History,
  Layers3,
  Loader2,
  LogOut,
  MessageSquare,
  PackageOpen,
  Plus,
  RefreshCw,
  Sparkles,
} from 'lucide-react';
import PreviewImage from '../PreviewImage';
import { conversationService, type Conversation } from '../../services/conversationService';
import { cocreationHistoryService } from '../../services/cocreationHistoryService';
import { assetDownloadUrl, assetService, type AssetRecord } from '../../services/assetService';
import type { ProjectRecord, VersionSnapshot } from '../CoCreationAgentWorkspace.types';
import type { QuoteCardData, WorkflowCard } from '../workflowCards/types';

export type WorkspaceResourceSection = 'projects' | 'files' | 'assets' | 'versions' | 'quotes';

interface WorkspaceResourceCenterProps {
  userLabel: string;
  activeProjectId: string | null;
  activeConversationId: string | null;
  activeSection: WorkspaceResourceSection;
  initiallyExpanded?: boolean;
  refreshKey?: number;
  onSectionChange: (section: WorkspaceResourceSection) => void;
  onNewChat: () => void;
  onOpenProject: (projectId: string, projectName: string, imageUrl: string | null) => void;
  onOpenConversation: (conversationId: string, title: string) => void;
  onPreviewUrl: (url: string) => void;
  onLogout: () => void;
}

interface QuoteArchiveItem {
  id: string;
  conversationId: string;
  conversationTitle: string;
  projectId: string | null;
  createdAt: string;
  data: QuoteCardData;
}

const SECTION_ITEMS: Array<{
  id: WorkspaceResourceSection;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { id: 'projects', label: '项目', icon: FolderKanban },
  { id: 'files', label: '文件', icon: FileText },
  { id: 'assets', label: '资产', icon: Box },
  { id: 'versions', label: '版本', icon: Layers3 },
  { id: 'quotes', label: '报价', icon: CircleDollarSign },
];

const FILE_KINDS = new Set(['document', 'cad', 'model', 'archive', 'script', 'audio', 'drawing', 'step', 'stl', 'glb', 'pdf']);

const formatDate = (value?: string | null): string => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit' }).format(date);
};

const formatMoney = (value: number): string => {
  if (!Number.isFinite(value)) return '—';
  return `¥${Math.round(value).toLocaleString('zh-CN')}`;
};

const isImageAsset = (asset: AssetRecord): boolean =>
  asset.kind === 'image'
  || asset.contentType?.startsWith('image/')
  || ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes((asset.extension || '').toLowerCase());

const isFileAsset = (asset: AssetRecord): boolean =>
  FILE_KINDS.has((asset.kind || '').toLowerCase()) || !isImageAsset(asset);

const extractQuotes = (conversations: Conversation[]): QuoteArchiveItem[] => {
  const quotes: QuoteArchiveItem[] = [];
  conversations.forEach((conversation) => {
    conversation.messages.forEach((message) => {
      const rawCards = message.cardData?.cards;
      if (!Array.isArray(rawCards)) return;
      (rawCards as WorkflowCard[]).forEach((card) => {
        if (card.type !== 'quote') return;
        quotes.push({
          id: `${conversation.id}-${message.id}-${card.id}`,
          conversationId: conversation.id,
          conversationTitle: conversation.title,
          projectId: conversation.projectId,
          createdAt: message.createdAt,
          data: card.data as QuoteCardData,
        });
      });
    });
  });
  return quotes.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
};

const EmptyState: React.FC<{ title: string; description: string }> = ({ title, description }) => (
  <div className="rounded-xl border border-white/10 bg-white/[0.035] px-3 py-4 text-center">
    <div className="text-xs font-medium text-gray-300">{title}</div>
    <div className="mt-1 text-[11px] leading-5 text-gray-600">{description}</div>
  </div>
);

export const WorkspaceResourceCenter: React.FC<WorkspaceResourceCenterProps> = ({
  userLabel,
  activeProjectId,
  activeConversationId,
  activeSection,
  initiallyExpanded = false,
  refreshKey = 0,
  onSectionChange,
  onNewChat,
  onOpenProject,
  onOpenConversation,
  onPreviewUrl,
  onLogout,
}) => {
  const [collapsed, setCollapsed] = useState(!initiallyExpanded);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [snapshots, setSnapshots] = useState<VersionSnapshot[]>([]);
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [libraryAssets, setLibraryAssets] = useState<AssetRecord[]>([]);

  useEffect(() => {
    if (initiallyExpanded) setCollapsed(false);
  }, [initiallyExpanded]);

  const loadResources = useCallback(async (soft = false) => {
    if (soft) setRefreshing(true);
    else setLoading(true);

    const [conversationResult, historyResult, assetResult, libraryResult] = await Promise.allSettled([
      conversationService.list(),
      cocreationHistoryService.listHistory(120, 0),
      assetService.list({ limit: 120, offset: 0 }),
      assetService.list({ limit: 100, offset: 0, library: true }),
    ]);

    if (conversationResult.status === 'fulfilled') setConversations(conversationResult.value);
    if (historyResult.status === 'fulfilled') {
      setProjects(historyResult.value.data.projects ?? []);
      setSnapshots(historyResult.value.data.snapshots ?? []);
    }
    if (assetResult.status === 'fulfilled') setAssets(assetResult.value.items ?? []);
    if (libraryResult.status === 'fulfilled') setLibraryAssets(libraryResult.value.items ?? []);

    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void loadResources();
  }, [loadResources, activeProjectId, activeSection, refreshKey]);

  const recentConversations = useMemo(
    () => [...conversations]
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
      .slice(0, 18),
    [conversations],
  );

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId) ?? null,
    [projects, activeProjectId],
  );

  const activeProjectSnapshots = useMemo(
    () => snapshots
      .filter((snapshot) => !activeProjectId || snapshot.projectId === activeProjectId)
      .sort((a, b) => new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime()),
    [snapshots, activeProjectId],
  );

  const activeProjectAssets = useMemo(
    () => assets.filter((asset) => !activeProjectId || asset.projectId === activeProjectId),
    [assets, activeProjectId],
  );

  const files = useMemo(
    () => activeProjectAssets.filter(isFileAsset).slice(0, 30),
    [activeProjectAssets],
  );

  const visibleAssets = useMemo(() => {
    const source = libraryAssets.length > 0 ? libraryAssets : assets;
    return source
      .filter((asset) => !activeProjectId || !asset.projectId || asset.projectId === activeProjectId)
      .slice(0, 30);
  }, [libraryAssets, assets, activeProjectId]);

  const quotes = useMemo(() => {
    const all = extractQuotes(conversations);
    return all.filter((quote) => !activeProjectId || !quote.projectId || quote.projectId === activeProjectId).slice(0, 30);
  }, [conversations, activeProjectId]);

  const projectConversationCount = useMemo(
    () => conversations.filter((conversation) => conversation.projectId === activeProjectId).length,
    [conversations, activeProjectId],
  );

  const selectSection = (section: WorkspaceResourceSection): void => {
    onSectionChange(section);
    setCollapsed(false);
  };

  const renderProjectSection = () => (
    <div className="space-y-1">
      {projects.slice(0, 24).map((project) => (
        <button
          key={project.id}
          type="button"
          onClick={() => onOpenProject(project.id, project.name, project.lastImageUrl ?? null)}
          className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left transition ${
            project.id === activeProjectId ? 'bg-white/10 text-white' : 'text-gray-400 hover:bg-white/5 hover:text-gray-200'
          }`}
        >
          {project.lastImageUrl ? (
            <span className="size-9 shrink-0 overflow-hidden rounded-lg bg-white/10">
              <PreviewImage src={project.lastImageUrl} alt={project.name} className="h-full w-full object-cover" />
            </span>
          ) : (
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-white/5">
              <FolderKanban className="size-4" />
            </span>
          )}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium">{project.name}</span>
            <span className="mt-0.5 block truncate text-[10px] text-gray-600">
              {project.industry || '工业设计'} · {project.versionCount ?? 0} 个版本
            </span>
          </span>
        </button>
      ))}
      {!loading && projects.length === 0 ? (
        <EmptyState title="还没有项目" description="在中间对话里说出你想设计什么，AI 会自动建立项目。" />
      ) : null}
    </div>
  );

  const renderFileSection = () => (
    <div className="space-y-1">
      {files.map((asset) => (
        <a
          key={asset.id}
          href={assetDownloadUrl(asset.id)}
          download={asset.filename}
          className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-gray-400 transition hover:bg-white/5 hover:text-gray-200"
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-white/5">
            <FileText className="size-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium">{asset.filename}</span>
            <span className="mt-0.5 block truncate text-[10px] text-gray-600">
              {(asset.extension || asset.kind || 'file').toUpperCase()} · {formatDate(asset.createdAt)}
            </span>
          </span>
        </a>
      ))}
      {!loading && files.length === 0 ? (
        <EmptyState title="暂无文件" description="PDF、CAD、3D、工程包会在对话推进时自动归档到这里。" />
      ) : null}
    </div>
  );

  const renderAssetSection = () => (
    <div className="grid grid-cols-2 gap-2">
      {visibleAssets.map((asset) => {
        const image = isImageAsset(asset);
        const url = assetDownloadUrl(asset.id);
        return (
          <button
            key={asset.id}
            type="button"
            onClick={() => { if (image) onPreviewUrl(url); }}
            className="overflow-hidden rounded-xl border border-white/10 bg-white/[0.035] text-left transition hover:border-white/20 hover:bg-white/[0.055]"
          >
            <div className="flex aspect-[4/3] items-center justify-center overflow-hidden bg-black/20">
              {image ? (
                <PreviewImage src={url} alt={asset.filename} className="h-full w-full object-cover" />
              ) : (
                <PackageOpen className="size-5 text-gray-600" />
              )}
            </div>
            <div className="px-2 py-1.5">
              <div className="truncate text-[10px] font-medium text-gray-300">{asset.filename}</div>
              <div className="mt-0.5 text-[9px] text-gray-600">{asset.kind}</div>
            </div>
          </button>
        );
      })}
      {!loading && visibleAssets.length === 0 ? (
        <div className="col-span-2">
          <EmptyState title="暂无资产" description="效果图、参考素材和知识资产会持续沉淀。" />
        </div>
      ) : null}
    </div>
  );

  const renderVersionSection = () => (
    <div className="space-y-1">
      {activeProjectSnapshots.slice(0, 30).map((snapshot) => (
        <button
          key={snapshot.id}
          type="button"
          onClick={() => { if (snapshot.previewImageUrl) onPreviewUrl(snapshot.previewImageUrl); }}
          className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-gray-400 transition hover:bg-white/5 hover:text-gray-200"
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-white/5 text-[10px] font-semibold text-gray-300">
            V{snapshot.versionNumber ?? '·'}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium">{snapshot.label || '设计版本'}</span>
            <span className="mt-0.5 block truncate text-[10px] text-gray-600">
              {snapshot.status || 'completed'} · {formatDate(snapshot.createdAt)}
            </span>
          </span>
        </button>
      ))}
      {!loading && activeProjectSnapshots.length === 0 ? (
        <EmptyState title="暂无版本" description="每次确认、深化和修改都可以继续沉淀为版本。" />
      ) : null}
    </div>
  );

  const renderQuoteSection = () => (
    <div className="space-y-1">
      {quotes.map((quote) => (
        <button
          key={quote.id}
          type="button"
          onClick={() => onOpenConversation(quote.conversationId, quote.conversationTitle)}
          className="w-full rounded-xl border border-white/10 bg-white/[0.035] px-3 py-2.5 text-left transition hover:border-white/20 hover:bg-white/[0.055]"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-xs font-medium text-gray-200">{quote.data.schemeName || '方案报价'}</span>
            <span className="shrink-0 text-xs font-semibold text-emerald-300">{formatMoney(quote.data.totalCustomer)}</span>
          </div>
          <div className="mt-1 flex items-center justify-between text-[10px] text-gray-600">
            <span className="truncate">{quote.conversationTitle}</span>
            <span>{formatDate(quote.createdAt)}</span>
          </div>
        </button>
      ))}
      {!loading && quotes.length === 0 ? (
        <EmptyState title="暂无报价" description="报价不是独立页面；当方案具备条件时，AI 会在对话里生成报价卡并归档。" />
      ) : null}
    </div>
  );

  const sectionContent = {
    projects: renderProjectSection,
    files: renderFileSection,
    assets: renderAssetSection,
    versions: renderVersionSection,
    quotes: renderQuoteSection,
  }[activeSection];

  return (
    <aside
      className={`relative flex h-full shrink-0 flex-col border-r border-white/10 bg-[#171717] transition-[width] duration-200 ${
        collapsed ? 'w-[60px]' : 'w-[286px]'
      }`}
    >
      <div className={`flex h-14 shrink-0 items-center border-b border-white/10 ${collapsed ? 'justify-center px-2' : 'justify-between px-3'}`}>
        <div className="flex min-w-0 items-center gap-2">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-white/10">
            <Sparkles className="size-4 text-purple-300" />
          </div>
          {!collapsed ? (
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-white">CoDesign</div>
              <div className="text-[9px] uppercase tracking-[0.18em] text-gray-600">AI Design Workspace</div>
            </div>
          ) : null}
        </div>
        {!collapsed ? (
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="flex size-8 items-center justify-center rounded-lg text-gray-500 transition hover:bg-white/5 hover:text-white"
            aria-label="收起资源中心"
          >
            <ChevronLeft className="size-4" />
          </button>
        ) : null}
      </div>

      <div className={collapsed ? 'flex flex-col items-center gap-2 px-2 py-3' : 'px-3 py-3'}>
        <button
          type="button"
          onClick={onNewChat}
          className={`flex w-full items-center gap-2 rounded-xl bg-white text-sm font-semibold text-[#171717] transition hover:bg-gray-100 ${
            collapsed ? 'justify-center px-2 py-2.5' : 'px-3 py-2.5'
          }`}
          title="新建对话"
        >
          <Plus className="size-4 shrink-0" />
          {!collapsed ? <span>新建对话</span> : null}
        </button>
      </div>

      <nav className={collapsed ? 'flex flex-col items-center gap-1 px-2' : 'px-2'}>
        {!collapsed ? (
          <div className="px-2 pb-1.5 pt-1 text-[10px] font-medium uppercase tracking-[0.18em] text-gray-600">资源中心</div>
        ) : null}
        {SECTION_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = item.id === activeSection && !collapsed;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => selectSection(item.id)}
              className={`flex w-full items-center gap-2 rounded-lg py-2 text-sm transition ${
                collapsed ? 'justify-center px-2' : 'px-3'
              } ${active ? 'bg-white/10 text-white' : 'text-gray-400 hover:bg-white/5 hover:text-gray-200'}`}
              title={item.label}
            >
              <Icon className="size-4 shrink-0" />
              {!collapsed ? <span>{item.label}</span> : null}
            </button>
          );
        })}
      </nav>

      {!collapsed ? (
        <>
          {activeProjectId ? (
            <div className="mx-3 mt-3 rounded-2xl border border-white/10 bg-white/[0.035] p-3">
              <div className="flex items-start gap-2">
                <Archive className="mt-0.5 size-4 shrink-0 text-purple-300" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-semibold text-gray-100">{activeProject?.name || activeProjectId}</div>
                  <div className="mt-0.5 text-[10px] text-gray-600">当前项目档案</div>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-5 gap-1">
                {[
                  ['对话', projectConversationCount],
                  ['文件', files.length],
                  ['资产', activeProjectAssets.length],
                  ['版本', activeProjectSnapshots.length],
                  ['报价', quotes.length],
                ].map(([label, count]) => (
                  <div key={String(label)} className="rounded-lg bg-black/20 px-1 py-1.5 text-center">
                    <div className="text-[11px] font-semibold text-gray-300">{count}</div>
                    <div className="mt-0.5 text-[8px] text-gray-600">{label}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="mt-3 flex min-h-0 flex-1 flex-col border-t border-white/10">
            <div className="flex shrink-0 items-center justify-between px-4 pb-2 pt-3">
              <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-gray-600">
                {SECTION_ITEMS.find((item) => item.id === activeSection)?.label}
              </div>
              <button
                type="button"
                onClick={() => void loadResources(true)}
                className="flex size-7 items-center justify-center rounded-lg text-gray-600 transition hover:bg-white/5 hover:text-gray-300"
                aria-label="刷新资源"
              >
                <RefreshCw className={`size-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
              {loading ? (
                <div className="flex justify-center py-8"><Loader2 className="size-4 animate-spin text-gray-600" /></div>
              ) : sectionContent()}
            </div>
          </div>

          <div className="max-h-[220px] shrink-0 border-t border-white/10 px-2 py-2">
            <div className="flex items-center gap-1.5 px-2 py-1.5 text-[10px] font-medium uppercase tracking-[0.18em] text-gray-600">
              <History className="size-3" />
              最近对话
            </div>
            <div className="max-h-[165px] space-y-0.5 overflow-y-auto">
              {recentConversations.map((conversation) => (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => onOpenConversation(conversation.id, conversation.title)}
                  className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition ${
                    conversation.id === activeConversationId ? 'bg-white/10 text-white' : 'text-gray-500 hover:bg-white/5 hover:text-gray-300'
                  }`}
                >
                  <MessageSquare className="size-3.5 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{conversation.title || '新对话'}</span>
                  <span className="shrink-0 text-[9px] text-gray-700">{formatDate(conversation.updatedAt)}</span>
                </button>
              ))}
              {!loading && recentConversations.length === 0 ? (
                <div className="px-2 py-3 text-center text-[10px] text-gray-700">暂无对话</div>
              ) : null}
            </div>
          </div>
        </>
      ) : (
        <div className="flex-1" />
      )}

      {collapsed ? (
        <div className="px-2 pb-2">
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            className="flex size-9 w-full items-center justify-center rounded-lg text-gray-500 transition hover:bg-white/5 hover:text-white"
            aria-label="展开资源中心"
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
      ) : null}

      <div className={`shrink-0 border-t border-white/10 py-3 ${collapsed ? 'flex justify-center px-2' : 'px-3'}`}>
        <div className={`flex items-center gap-2 ${collapsed ? 'flex-col' : 'w-full'}`}>
          <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-purple-500/20 text-[11px] font-bold text-purple-300">
            {(userLabel || '?').slice(0, 1).toUpperCase()}
          </div>
          {!collapsed ? <div className="min-w-0 flex-1 truncate text-xs font-medium text-gray-300">{userLabel}</div> : null}
          <button
            type="button"
            onClick={onLogout}
            className="flex size-8 items-center justify-center rounded-lg text-gray-500 transition hover:bg-white/5 hover:text-white"
            aria-label="退出登录"
            title="退出登录"
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </div>
    </aside>
  );
};
