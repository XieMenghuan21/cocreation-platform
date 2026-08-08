import React, { useEffect, useMemo, useState } from 'react';
import {
  Archive,
  ChevronLeft,
  ChevronRight,
  FileText,
  FolderKanban,
  History,
  Image,
  LogOut,
  MessageSquare,
  Plus,
  ReceiptText,
  Sparkles,
  Waypoints,
} from 'lucide-react';
import { conversationService, type Conversation } from '../../services/conversationService';
import type { WorkspacePrimaryView } from './workspaceResourceTypes';

interface WorkspaceNavigationProps {
  activeView: WorkspacePrimaryView;
  userLabel: string;
  onSelectView: (view: WorkspacePrimaryView) => void;
  onNewChat: () => void;
  onOpenConversation: (conversationId: string, title: string) => void;
  onLogout: () => void;
}

const NAV_ITEMS: Array<{
  id: WorkspacePrimaryView;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { id: 'chat', label: '对话', icon: MessageSquare },
  { id: 'projects', label: '项目', icon: FolderKanban },
  { id: 'files', label: '文件', icon: FileText },
  { id: 'assets', label: '资产', icon: Image },
  { id: 'versions', label: '版本', icon: Waypoints },
  { id: 'quotes', label: '报价', icon: ReceiptText },
];

const formatRelativeTime = (iso: string): string => {
  const time = new Date(iso).getTime();
  if (!Number.isFinite(time)) return '';
  const diff = Date.now() - time;
  if (diff < 60_000) return '刚刚';
  if (diff < 3_600_000) return `${Math.max(1, Math.floor(diff / 60_000))} 分钟前`;
  if (diff < 86_400_000) return `${Math.max(1, Math.floor(diff / 3_600_000))} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.max(1, Math.floor(diff / 86_400_000))} 天前`;
  return new Date(iso).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
};

export const WorkspaceNavigation: React.FC<WorkspaceNavigationProps> = ({
  activeView,
  userLabel,
  onSelectView,
  onNewChat,
  onOpenConversation,
  onLogout,
}) => {
  const [collapsed, setCollapsed] = useState(true);
  const [recent, setRecent] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    void conversationService.list()
      .then((items) => {
        if (disposed) return;
        const sorted = [...items].sort(
          (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
        );
        setRecent(sorted.slice(0, 5));
      })
      .catch(() => {
        if (!disposed) setRecent([]);
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [activeView]);

  const initials = useMemo(() => {
    const label = userLabel.trim();
    return label ? label.slice(0, 2).toUpperCase() : 'AI';
  }, [userLabel]);

  return (
    <aside
      className={`relative flex h-full shrink-0 flex-col border-r border-slate-200/80 bg-white transition-[width] duration-200 ${collapsed ? 'w-[68px]' : 'w-[248px]'}`}
    >
      <div className="flex h-16 shrink-0 items-center border-b border-slate-100 px-3">
        <button
          type="button"
          onClick={() => onSelectView('chat')}
          className={`flex min-w-0 items-center rounded-2xl text-left transition hover:bg-slate-50 ${collapsed ? 'h-10 w-10 justify-center' : 'w-full gap-3 px-2 py-2'}`}
          title="回到对话"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-white shadow-sm">
            <Sparkles className="h-4 w-4" />
          </span>
          {!collapsed ? (
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-slate-950">CoDesign</span>
              <span className="block truncate text-[10px] font-medium tracking-[0.08em] text-slate-400">AI DESIGN WORKSPACE</span>
            </span>
          ) : null}
        </button>
      </div>

      <div className="px-3 pt-3">
        <button
          type="button"
          onClick={onNewChat}
          className={`flex h-10 items-center rounded-xl bg-slate-950 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 ${collapsed ? 'w-10 justify-center' : 'w-full gap-2 px-3'}`}
          title="新对话"
        >
          <Plus className="h-4 w-4" />
          {!collapsed ? <span>新对话</span> : null}
        </button>
      </div>

      <nav className="mt-3 px-2">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = activeView === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelectView(item.id)}
              title={item.label}
              className={`mb-1 flex h-10 items-center rounded-xl text-sm transition ${
                collapsed ? 'w-full justify-center' : 'w-full gap-3 px-3'
              } ${
                active
                  ? 'bg-slate-100 font-semibold text-slate-950'
                  : 'font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-900'
              }`}
            >
              <Icon className="h-[18px] w-[18px] shrink-0" />
              {!collapsed ? <span>{item.label}</span> : null}
            </button>
          );
        })}
      </nav>

      {!collapsed ? (
        <div className="mt-4 min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          <div className="mb-2 flex items-center gap-2 px-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
            <History className="h-3.5 w-3.5" />
            最近对话
          </div>
          {loading ? (
            <div className="px-1 py-3 text-xs text-slate-400">正在读取...</div>
          ) : recent.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 px-3 py-4 text-xs leading-5 text-slate-400">
              还没有最近对话。说出一个产品想法，Workspace 会从这里开始生长。
            </div>
          ) : (
            <div className="space-y-1">
              {recent.map((conversation) => (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => onOpenConversation(conversation.id, conversation.title)}
                  className="group w-full rounded-xl px-2.5 py-2 text-left transition hover:bg-slate-50"
                >
                  <div className="truncate text-xs font-medium text-slate-700 group-hover:text-slate-950">
                    {conversation.title || '新对话'}
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-slate-400">
                    <span className="truncate">{conversation.projectId ? '已关联项目' : '对话'}</span>
                    <span className="shrink-0">{formatRelativeTime(conversation.updatedAt)}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 items-start justify-center pt-4 text-slate-300">
          <Archive className="h-4 w-4" />
        </div>
      )}

      <div className="shrink-0 border-t border-slate-100 p-2">
        {!collapsed ? (
          <div className="mb-1 flex items-center gap-2 rounded-xl px-2 py-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-700">
              {initials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-semibold text-slate-800">{userLabel || '用户'}</div>
              <div className="text-[10px] text-slate-400">工业设计工作台</div>
            </div>
            <button
              type="button"
              onClick={onLogout}
              className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
              title="退出登录"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={onLogout}
            className="flex h-10 w-full items-center justify-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            title="退出登录"
          >
            <LogOut className="h-4 w-4" />
          </button>
        )}
      </div>

      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="absolute -right-3 top-[76px] z-20 flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-400 shadow-sm transition hover:text-slate-900"
        title={collapsed ? '展开资源中心' : '收起资源中心'}
      >
        {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
      </button>
    </aside>
  );
};
