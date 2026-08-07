import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  PanelLeftClose,
  PanelLeft,
  Plus,
  Sparkles,
  MessageSquare,
  LogOut,
  FolderKanban,
  Box,
  Calculator,
  Loader2,
} from 'lucide-react';
import PreviewImage from './PreviewImage';
import { conversationService, type Conversation } from '../services/conversationService';

export type GptView = 'workspace' | 'projects' | 'assets' | 'quotes';

interface GptSidebarProps {
  view: GptView;
  userLabel: string;
  onNavigate: (view: GptView) => void;
  onNewChat: () => void;
  onOpenProject: (projectId: string, projectName: string, imageUrl: string | null) => void;
  onOpenConversation: (conversationId: string, title: string) => void;
  onLogout: () => void;
  activeProjectId: string | null;
}

const NAV_ITEMS: Array<{
  view: GptView;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { view: 'projects', label: '项目库', icon: FolderKanban },
  { view: 'assets', label: '资产库', icon: Box },
  { view: 'quotes', label: '报价', icon: Calculator },
];

export const GptSidebar: React.FC<GptSidebarProps> = ({
  view,
  userLabel,
  onNavigate,
  onNewChat,
  onOpenProject,
  onOpenConversation,
  onLogout,
  activeProjectId,
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);

  const loadRecent = useCallback(async () => {
    try {
      const list = await conversationService.list();
      setConversations(list);
    } catch {
      setConversations([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRecent();
  }, [loadRecent, view]);

  const recentConversations = useMemo(() => conversations.slice(0, 20), [conversations]);

  return (
    <aside
      className={`relative flex shrink-0 flex-col bg-[#171717] transition-[width] duration-200 ${collapsed ? 'w-[60px]' : 'w-[248px]'}`}
    >
      <div className={`flex h-14 items-center border-b border-white/10 ${collapsed ? 'justify-center px-2' : 'justify-between px-3'}`}>
        <div className="flex items-center gap-2">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-white/10">
            <Sparkles className="size-4 text-purple-300" />
          </div>
          {!collapsed ? <span className="truncate text-sm font-semibold text-white">CoDesign</span> : null}
        </div>
        {!collapsed ? (
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="flex size-7 items-center justify-center rounded-lg text-gray-500 transition hover:bg-white/5 hover:text-white"
            aria-label="收起侧边栏"
          >
            <PanelLeftClose className="size-4" />
          </button>
        ) : null}
      </div>

      <div className={collapsed ? 'flex flex-col items-center gap-1 px-2 py-3' : 'px-3 py-3'}>
        <button
          type="button"
          onClick={onNewChat}
          className={`flex w-full items-center gap-2 rounded-lg bg-white text-[#171717] text-sm font-semibold transition hover:opacity-90 ${collapsed ? 'justify-center px-2 py-2' : 'px-3 py-2'}`}
        >
          <Plus className="size-4 shrink-0" />
          {!collapsed ? <span>新建对话</span> : null}
        </button>
      </div>

      <nav className={collapsed ? 'flex flex-col items-center gap-1 px-2' : 'px-2'}>
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = view === item.view;
          return (
            <button
              key={item.view}
              type="button"
              onClick={() => onNavigate(item.view)}
              className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${collapsed ? 'justify-center px-2' : ''} ${
                active ? 'bg-white/10 text-white' : 'text-gray-400 hover:bg-white/5 hover:text-gray-200'
              }`}
            >
              <Icon className="size-4 shrink-0" />
              {!collapsed ? <span>{item.label}</span> : null}
            </button>
          );
        })}
      </nav>

      {!collapsed ? (
        <div className="mt-3 flex-1 overflow-y-auto px-2">
          <div className="px-2 py-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-500">
            最近
          </div>
          <div className="space-y-0.5">
            {loading ? (
              <div className="flex justify-center py-4">
                <Loader2 className="size-4 animate-spin text-gray-500" />
              </div>
            ) : null}
            {recentConversations.map((conversation) => {
              const lastMessage = conversation.messages[conversation.messages.length - 1];
              const cardData = lastMessage?.cardData ?? {};
              const cardOutputs = cardData.outputs as Record<string, unknown> | undefined;
              const previewUrl = typeof cardOutputs?.renderPng === 'string'
                ? cardOutputs.renderPng
                : typeof cardOutputs?.enhancedImage === 'string'
                  ? cardOutputs.enhancedImage
                  : null;
              return (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => onOpenConversation(conversation.id, conversation.title)}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
                    activeProjectId === conversation.projectId
                      ? 'bg-white/10 text-white'
                      : 'text-gray-400 hover:bg-white/5 hover:text-gray-200'
                  }`}
                >
                  {previewUrl ? (
                    <span className="flex size-7 shrink-0 items-center justify-center overflow-hidden rounded-md bg-white/10">
                      <PreviewImage src={previewUrl} alt={conversation.title} className="h-full w-full object-cover" />
                    </span>
                  ) : (
                    <MessageSquare className="size-4 shrink-0" />
                  )}
                  <span className="truncate">{conversation.title}</span>
                </button>
              );
            })}
            {!loading && recentConversations.length === 0 ? (
              <p className="px-3 py-4 text-center text-xs text-gray-600">暂无对话</p>
            ) : null}
          </div>
        </div>
      ) : null}

      {!collapsed ? null : <div className="flex-1" />}

      {!collapsed ? null : (
        <div className="px-2 py-2">
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            className="flex size-8 w-full items-center justify-center rounded-lg text-gray-500 transition hover:bg-white/5 hover:text-white"
            aria-label="展开侧边栏"
          >
            <PanelLeft className="size-4" />
          </button>
        </div>
      )}

      <div className={`border-t border-white/10 py-3 ${collapsed ? 'flex justify-center px-2' : 'px-3'}`}>
        <div className={`flex items-center gap-2 ${collapsed ? 'flex-col' : ''}`}>
          <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-purple-500/20 text-[11px] font-bold text-purple-300">
            {(userLabel || '?').slice(0, 1).toUpperCase()}
          </div>
          {!collapsed ? (
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-gray-200">{userLabel}</div>
            </div>
          ) : null}
          <button
            type="button"
            onClick={onLogout}
            className={`flex items-center justify-center rounded-lg text-gray-400 transition hover:bg-white/5 hover:text-white ${collapsed ? 'size-7' : 'size-7'}`}
            aria-label="退出登录"
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </div>

    </aside>
  );
};
