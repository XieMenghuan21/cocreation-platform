import React from 'react';
import {
  Boxes,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  MessageSquare,
  Package,
  Plus,
} from 'lucide-react';
import type { WorkspaceNode } from '../../services/workspaceGraphService';

export type ResourceTab = 'project' | 'nodes' | 'assets' | 'files';

interface ResourceCenterProps {
  nodes: WorkspaceNode[];
  activeTab: ResourceTab;
  onTabChange: (tab: ResourceTab) => void;
  onNewChat: () => void;
  onSelectNode: (node: WorkspaceNode) => void;
}

const TABS: Array<{ id: ResourceTab; label: string; icon: React.ReactNode }> = [
  { id: 'project', label: '项目', icon: <FolderOpen className="h-4 w-4" /> },
  { id: 'nodes', label: '节点', icon: <MessageSquare className="h-4 w-4" /> },
  { id: 'assets', label: '资产', icon: <ImageIcon className="h-4 w-4" /> },
  { id: 'files', label: '文件', icon: <FileText className="h-4 w-4" /> },
];

export const ResourceCenter: React.FC<ResourceCenterProps> = ({
  nodes,
  activeTab,
  onTabChange,
  onNewChat,
  onSelectNode,
}) => {
  const project = nodes.find((n) => n.type === 'project');
  const childNodes = nodes.filter((n) => n.type !== 'project');
  const assets = nodes.flatMap(
    (n) =>
      n.assets?.map((a) => ({ ...a, nodeTitle: n.title, nodeId: n.id })) ?? [],
  );

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="border-b border-slate-100 px-4 py-3">
        <button
          type="button"
          onClick={onNewChat}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-slate-900 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700"
        >
          <Plus className="h-4 w-4" />
          新对话
        </button>
      </div>

      <div className="flex border-b border-slate-100 px-2 py-1.5">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => onTabChange(tab.id)}
            className={`flex flex-1 items-center justify-center gap-1 rounded-lg px-1.5 py-1.5 text-[11px] font-medium transition ${
              activeTab === tab.id
                ? 'bg-slate-100 text-slate-900'
                : 'text-slate-500 hover:bg-slate-50'
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {activeTab === 'project' ? (
          project ? (
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                <Boxes className="h-4 w-4 text-slate-500" />
                <span className="truncate">{project.title}</span>
              </div>
              {project.projectId ? (
                <div className="mt-1 text-[11px] text-slate-400">{project.projectId}</div>
              ) : null}
              <div className="mt-2 flex items-center gap-1 text-[11px] text-emerald-600">
                <Package className="h-3 w-3" />
                {nodes.length} 个节点
              </div>
            </div>
          ) : (
            <p className="px-2 text-xs text-slate-400">尚无项目，输入一句话开始。</p>
          )
        ) : activeTab === 'nodes' ? (
          childNodes.length > 0 ? (
            <ul className="space-y-1">
              {childNodes.map((node) => (
                <li key={node.id}>
                  <button
                    type="button"
                    onClick={() => onSelectNode(node)}
                    className="w-full rounded-lg px-2 py-1.5 text-left text-xs transition hover:bg-slate-50"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span
                        className={`truncate ${node.status === 'superseded' ? 'text-slate-300 line-through' : 'text-slate-700'}`}
                      >
                        {node.title}
                      </span>
                      <span
                        className={`shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${
                          node.status === 'completed'
                            ? 'bg-emerald-50 text-emerald-600'
                            : node.status === 'waiting_user'
                              ? 'bg-amber-50 text-amber-600'
                              : 'bg-slate-100 text-slate-500'
                        }`}
                      >
                        {node.status}
                      </span>
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">{node.type}</div>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-2 text-xs text-slate-400">节点将随对话生成。</p>
          )
        ) : activeTab === 'assets' ? (
          assets.length > 0 ? (
            <ul className="space-y-1.5">
              {assets.map((asset, index) => (
                <li key={`${asset.nodeId}-${asset.assetId}-${index}`}>
                  <button
                    type="button"
                    className="w-full rounded-xl border border-slate-100 p-2 text-left transition hover:border-slate-200 hover:bg-slate-50"
                  >
                    <div className="text-[11px] font-medium text-slate-600">{asset.nodeTitle}</div>
                    <div className="text-[10px] text-slate-400">
                      {asset.role} · {String(asset.assetId).slice(0, 8)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-2 text-xs text-slate-400">暂无资产。</p>
          )
        ) : (
          <p className="px-2 text-xs text-slate-400">文件将随工程包生成。</p>
        )}
      </div>
    </aside>
  );
};
