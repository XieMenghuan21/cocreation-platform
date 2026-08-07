import React, { useCallback, useEffect, useRef, useState } from 'react';
import { PanelLeft, PanelRight, PanelRightClose } from 'lucide-react';
import { ConversationPane } from './ConversationPane';
import { ResourceCenter, type ResourceTab } from './ResourceCenter';
import { PreviewPanel } from './PreviewPanel';
import type { WorkspaceNode } from '../../services/workspaceGraphService';
import { workspaceGraphService } from '../../services/workspaceGraphService';

interface WorkspaceShellProps {
  conversationId?: string | null;
  initialPrompt?: string | null;
  onConversationChanged?: (conversationId: string, title: string) => void;
  onNewChat: () => void;
}

export const WorkspaceShell: React.FC<WorkspaceShellProps> = ({
  conversationId,
  initialPrompt,
  onConversationChanged,
  onNewChat,
}) => {
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    conversationId ?? null,
  );
  const [showResource, setShowResource] = useState(true);
  const [showPreview, setShowPreview] = useState(false);
  const [resourceTab, setResourceTab] = useState<ResourceTab>('project');
  const [selectedNode, setSelectedNode] = useState<WorkspaceNode | null>(null);
  const [nodes, setNodes] = useState<WorkspaceNode[]>([]);
  const [title, setTitle] = useState('新对话');
  const [loaded, setLoaded] = useState(false);
  const [loadingError, setLoadingError] = useState<string | null>(null);
  const loadStartedRef = useRef(false);

  const loadSnapshot = useCallback(
    async (conversationIdValue: string) => {
      try {
        setLoadingError(null);
        const snapshot = await workspaceGraphService.snapshot(conversationIdValue);
        setNodes(snapshot.nodes);
        if (snapshot.conversation?.title) setTitle(snapshot.conversation.title);
        const activeNodeId = snapshot.uiState?.activeNodeId as string | undefined;
        if (activeNodeId) {
          const activeNode = snapshot.nodes.find((n) => n.id === activeNodeId);
          if (activeNode) setSelectedNode(activeNode);
        }
      } catch (err) {
        setLoadingError(err instanceof Error ? err.message : '加载工作区失败');
      } finally {
        setLoaded(true);
      }
    },
    [],
  );

  useEffect(() => {
    if (conversationId && !loadStartedRef.current) {
      loadStartedRef.current = true;
      void loadSnapshot(conversationId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  const handleConversationCreated = useCallback(
    (newConversationId: string) => {
      setActiveConversationId(newConversationId);
      onConversationChanged?.(newConversationId, title);
    },
    [onConversationChanged, title],
  );

  const handleMessageCountChange = useCallback(
    (count: number) => {
      if (count === 0) {
        setSelectedNode(null);
        setShowPreview(false);
      }
    },
    [],
  );

  return (
    <div className="flex h-full min-h-0 flex-1">
      {showResource ? (
        <ResourceCenter
          nodes={nodes}
          activeTab={resourceTab}
          onTabChange={setResourceTab}
          onNewChat={onNewChat}
          onSelectNode={(node) => {
            setSelectedNode(node);
            setShowPreview(true);
          }}
        />
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowResource((v) => !v)}
              className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
              title="资源中心"
            >
              {showResource ? <PanelLeft className="h-4 w-4" /> : <PanelLeft className="h-4 w-4" />}
              资源
            </button>
            <span className="text-sm font-semibold text-slate-800">{title}</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowPreview((v) => !v)}
              className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
              title="预览面板"
            >
              {showPreview ? (
                <PanelRightClose className="h-4 w-4" />
              ) : (
                <PanelRight className="h-4 w-4" />
              )}
              预览
            </button>
          </div>
        </div>

        {loadingError ? (
          <div className="border-b border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
            {loadingError}
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1">
          <ConversationPane
            key={activeConversationId ?? 'new'}
            conversationId={activeConversationId}
            initialPrompt={initialPrompt}
            onConversationCreated={handleConversationCreated}
            onNodeSelected={(node) => {
              setSelectedNode(node);
              setShowPreview(node != null);
            }}
            onMessageCountChange={handleMessageCountChange}
          />
          {showPreview ? (
            <PreviewPanel
              node={selectedNode}
              onClose={() => {
                setShowPreview(false);
                setSelectedNode(null);
              }}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
};
