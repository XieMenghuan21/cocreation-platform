import React, { useCallback, useEffect, useState } from 'react';
import { PanelRight, PanelRightClose } from 'lucide-react';
import { ConversationPane } from './ConversationPane';
import { PreviewPanel } from './PreviewPanel';
import type { WorkspaceNode } from '../../services/workspaceGraphService';
import { workspaceGraphService } from '../../services/workspaceGraphService';

interface WorkspaceShellProps {
  conversationId?: string | null;
  initialPrompt?: string | null;
  onConversationChanged?: (conversationId: string, title: string) => void;
  onNewChat: () => void;
  externalResourceCenter?: boolean;
}

/**
 * Graph-native conversation surface.
 *
 * Production App currently keeps the proven GptWorkspace as the safe default and exposes
 * this shell with ?graph=1 / VITE_WORKSPACE_GRAPH_DEFAULT. The shell itself no longer owns
 * a second resource sidebar: App's WorkspaceNavigation is the single resource navigation.
 */
export const WorkspaceShell: React.FC<WorkspaceShellProps> = ({
  conversationId,
  initialPrompt,
  onConversationChanged,
  onNewChat: _onNewChat,
  externalResourceCenter = true,
}) => {
  const [activeConversationId, setActiveConversationId] = useState<string | null>(conversationId ?? null);
  const [showPreview, setShowPreview] = useState(false);
  const [selectedNode, setSelectedNode] = useState<WorkspaceNode | null>(null);
  const [nodes, setNodes] = useState<WorkspaceNode[]>([]);
  const [title, setTitle] = useState('新对话');
  const [loadingError, setLoadingError] = useState<string | null>(null);

  useEffect(() => {
    setActiveConversationId(conversationId ?? null);
    setSelectedNode(null);
    setShowPreview(false);
    setLoadingError(null);
    if (!conversationId) {
      setTitle('新对话');
      setNodes([]);
      return;
    }
    let cancelled = false;
    void workspaceGraphService.snapshot(conversationId).then((snapshot) => {
      if (cancelled) return;
      setNodes(snapshot.nodes);
      setTitle(snapshot.conversation?.title || '设计工作台');
      const activeNodeId = snapshot.uiState?.activeNodeId;
      if (typeof activeNodeId === 'string') {
        const active = snapshot.nodes.find((node) => node.id === activeNodeId) ?? null;
        setSelectedNode(active);
      }
    }).catch((reason) => {
      if (!cancelled) setLoadingError(reason instanceof Error ? reason.message : '工作区加载失败');
    });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const handleConversationCreated = useCallback((newConversationId: string, projectTitle?: string) => {
    setActiveConversationId(newConversationId);
    if (projectTitle) setTitle(projectTitle);
    onConversationChanged?.(newConversationId, projectTitle || title);
  }, [onConversationChanged, title]);

  const handleNodeSelected = useCallback((node: WorkspaceNode | null) => {
    setSelectedNode(node);
    setShowPreview(Boolean(node));
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-1 bg-white">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-11 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-900">{title}</div>
            {nodes.length > 0 ? <div className="text-[10px] text-slate-400">{nodes.length} 个工作节点</div> : null}
          </div>
          <button type="button" onClick={() => setShowPreview((value) => !value)} className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800">
            {showPreview ? <PanelRightClose className="h-4 w-4" /> : <PanelRight className="h-4 w-4" />}
            预览
          </button>
        </header>

        {loadingError ? <div className="border-b border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">{loadingError}</div> : null}

        <div className="flex min-h-0 flex-1">
          <ConversationPane
            key={activeConversationId ?? 'new'}
            conversationId={activeConversationId}
            initialPrompt={initialPrompt}
            onConversationCreated={handleConversationCreated}
            onNodeSelected={handleNodeSelected}
            onNodesChanged={setNodes}
          />
          {showPreview ? <PreviewPanel node={selectedNode} onClose={() => handleNodeSelected(null)} /> : null}
        </div>
      </div>
      {/* externalResourceCenter is intentionally consumed as product contract: resource navigation lives in App. */}
      {externalResourceCenter ? null : null}
    </div>
  );
};
