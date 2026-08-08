import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FileText, Loader2, Paperclip, Send, Sparkles, X } from 'lucide-react';
import {
  workspaceGraphService,
  type TurnResponse,
  type WorkspaceNode,
  type WorkspaceSnapshot,
} from '../../services/workspaceGraphService';
import { assetService, assetDownloadUrl } from '../../services/assetService';
import { conversationService } from '../../services/conversationService';
import {
  WorkspaceNodeRenderer,
  NodeCarousel,
  type NodeAction,
} from './WorkspaceNodeRenderer';

interface MessageItem {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  nodes: WorkspaceNode[];
  pending?: boolean;
  failed?: boolean;
}

interface PendingAsset {
  id: string;
  url: string;
  name: string;
  isImage: boolean;
  uploading?: boolean;
}

interface ConversationPaneProps {
  conversationId?: string | null;
  initialPrompt?: string | null;
  onConversationCreated?: (conversationId: string, title?: string) => void;
  onNodeSelected?: (node: WorkspaceNode | null) => void;
  onMessageCountChange?: (count: number) => void;
  onNodesChanged?: (nodes: WorkspaceNode[]) => void;
}

const nextLocalId = (() => {
  let counter = 0;
  return () => `local-${Date.now()}-${(counter += 1)}`;
})();

const nodeIdsFromCardData = (cardData: Record<string, unknown> | null | undefined): string[] => {
  const value = cardData?.nodes;
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
};

const mergeNodes = (current: WorkspaceNode[], incoming: WorkspaceNode[]): WorkspaceNode[] => {
  const map = new Map(current.map((node) => [node.id, node]));
  incoming.forEach((node) => map.set(node.id, { ...(map.get(node.id) ?? {}), ...node } as WorkspaceNode));
  return Array.from(map.values()).sort((a, b) => a.createdAt.localeCompare(b.createdAt));
};

const MessageNodes: React.FC<{
  nodes: WorkspaceNode[];
  onAction: (node: WorkspaceNode, action: NodeAction) => void;
  onSelect: (node: WorkspaceNode) => void;
}> = ({ nodes, onAction, onSelect }) => {
  const directions = nodes.filter((node) => node.type === 'design_direction');
  const others = nodes.filter((node) => node.type !== 'design_direction');

  return (
    <>
      {directions.length > 1 ? (
        <div className="mb-2" onClick={() => onSelect(directions.find((node) => node.status === 'completed') ?? directions[0])}>
          <NodeCarousel
            title="设计方向"
            summary="AI 给出了多个差异化方向。选择一个，后续渲染、3D、CAD 都会沿这个方向继续。"
            nodes={directions}
            onAction={onAction}
          />
        </div>
      ) : null}
      {directions.length <= 1
        ? directions.map((node) => (
            <button key={node.id} type="button" className="mb-2 block w-full text-left" onClick={() => onSelect(node)}>
              <WorkspaceNodeRenderer node={node} onAction={onAction} />
            </button>
          ))
        : null}
      {others.map((node) => (
        <button key={node.id} type="button" className="mb-2 block w-full text-left" onClick={() => onSelect(node)}>
          <WorkspaceNodeRenderer node={node} onAction={onAction} />
        </button>
      ))}
    </>
  );
};

const buildMessagesFromHistory = (
  conversation: Awaited<ReturnType<typeof conversationService.get>>,
  snapshot: WorkspaceSnapshot | null,
): MessageItem[] => {
  const nodes = snapshot?.nodes ?? [];
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const attached = new Set<string>();

  const built = conversation.messages.map<MessageItem>((message) => {
    const ids = nodeIdsFromCardData(message.cardData);
    const messageNodes = ids.map((id) => nodeMap.get(id)).filter((node): node is WorkspaceNode => Boolean(node));
    messageNodes.forEach((node) => attached.add(node.id));
    return {
      id: `history-${message.id}`,
      role: message.role,
      text: message.text,
      nodes: messageNodes,
    };
  });

  // 旧消息可能没有 cardData.nodes，但 Graph 已经有镜像节点。把这些节点集中挂到最后一条 AI 消息，
  // 保证历史项目打开后不会再出现“数据明明在，但页面一片空”的情况。
  const unattached = nodes.filter((node) => !attached.has(node.id));
  if (unattached.length > 0) {
    let target = -1;
    for (let index = built.length - 1; index >= 0; index -= 1) {
      if (built[index].role === 'assistant') {
        target = index;
        break;
      }
    }
    if (target >= 0) {
      built[target] = { ...built[target], nodes: mergeNodes(built[target].nodes, unattached) };
    } else {
      built.push({
        id: `workspace-${nextLocalId()}`,
        role: 'assistant',
        text: '已恢复该项目的 Workspace 工作节点。',
        nodes: unattached,
      });
    }
  }
  return built;
};

export const ConversationPane: React.FC<ConversationPaneProps> = ({
  conversationId,
  initialPrompt,
  onConversationCreated,
  onNodeSelected,
  onMessageCountChange,
  onNodesChanged,
}) => {
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(Boolean(conversationId));
  const [error, setError] = useState<string | null>(null);
  const [assets, setAssets] = useState<PendingAsset[]>([]);
  const [allNodes, setAllNodes] = useState<WorkspaceNode[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const didAutoRunRef = useRef(false);
  const convIdRef = useRef<string | null>(conversationId ?? null);

  const publishNodes = useCallback((nodes: WorkspaceNode[]) => {
    setAllNodes(nodes);
    onNodesChanged?.(nodes);
  }, [onNodesChanged]);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const element = scrollRef.current;
      if (element) element.scrollTop = element.scrollHeight;
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    onMessageCountChange?.(messages.length);
  }, [messages.length, onMessageCountChange]);

  useEffect(() => {
    convIdRef.current = conversationId ?? null;
    didAutoRunRef.current = false;
    setMessages([]);
    setAllNodes([]);
    setError(null);
    setAssets([]);

    if (!conversationId) {
      setLoadingHistory(false);
      return;
    }

    let cancelled = false;
    setLoadingHistory(true);
    void Promise.all([
      conversationService.get(conversationId),
      workspaceGraphService.snapshot(conversationId).catch(() => null),
    ]).then(([conversation, snapshot]) => {
      if (cancelled) return;
      const built = buildMessagesFromHistory(conversation, snapshot);
      setMessages(built);
      publishNodes(snapshot?.nodes ?? []);
      const activeNodeId = snapshot?.uiState?.activeNodeId;
      if (typeof activeNodeId === 'string') {
        onNodeSelected?.((snapshot?.nodes ?? []).find((node) => node.id === activeNodeId) ?? null);
      }
    }).catch((reason) => {
      if (cancelled) return;
      setError(reason instanceof Error ? reason.message : '历史会话加载失败');
    }).finally(() => {
      if (!cancelled) setLoadingHistory(false);
    });

    return () => {
      cancelled = true;
    };
  }, [conversationId, onNodeSelected, publishNodes]);

  const applyTurn = useCallback((result: TurnResponse) => {
    convIdRef.current = result.conversationId;
    const incoming = [...result.nodesCreated, ...result.nodesUpdated];
    const merged = mergeNodes(allNodes, incoming);
    publishNodes(merged);
    onConversationCreated?.(result.conversationId, result.nodesCreated.find((node) => node.type === 'project')?.title);

    setMessages((previous) => {
      const pendingIndex = previous.findIndex((message) => message.pending);
      if (pendingIndex < 0) {
        return [...previous, {
          id: `assistant-${nextLocalId()}`,
          role: 'assistant',
          text: result.message.text,
          nodes: incoming,
        }];
      }
      return previous.map((message, index) => index === pendingIndex
        ? { ...message, pending: false, failed: false, text: result.message.text, nodes: incoming }
        : message);
    });
    setError(null);

    const previewId = result.workspace.previewNodeId;
    if (previewId) {
      onNodeSelected?.(incoming.find((node) => node.id === previewId) ?? merged.find((node) => node.id === previewId) ?? null);
    }
  }, [allNodes, onConversationCreated, onNodeSelected, publishNodes]);

  const send = useCallback(async (text: string, action?: { nodeId: string; type: string; value?: unknown }) => {
    const trimmed = text.trim();
    if ((!trimmed && !action) || sending || assets.some((asset) => asset.uploading)) return;

    setSending(true);
    setInput('');
    const sentAssets = assets;
    setAssets([]);
    const userText = trimmed;
    const visibleText = userText || action?.type || '继续';

    setMessages((previous) => [
      ...previous,
      { id: `user-${nextLocalId()}`, role: 'user', text: visibleText, nodes: [] },
      { id: `assistant-${nextLocalId()}`, role: 'assistant', text: action ? '正在推进当前工作节点…' : '正在理解你的想法…', nodes: [], pending: true },
    ]);

    try {
      const payload = {
        text: userText || null,
        assetIds: sentAssets.map((asset) => asset.id),
        action: action ? { nodeId: action.nodeId, type: action.type, value: action.value } : null,
      };
      const currentId = convIdRef.current;
      const result = currentId
        ? await workspaceGraphService.appendTurn(currentId, payload)
        : await workspaceGraphService.startTurn(payload);
      applyTurn(result);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '请求失败';
      setError(message);
      setMessages((previous) => previous.map((item) => item.pending
        ? { ...item, pending: false, failed: true, text: message }
        : item));
      setAssets(sentAssets);
    } finally {
      setSending(false);
    }
  }, [applyTurn, assets, sending]);

  const handleAction = useCallback((node: WorkspaceNode, action: NodeAction) => {
    void send('', { nodeId: node.id, type: action.type, value: action.value });
  }, [send]);

  const handleFilePick = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;

    const localUrl = URL.createObjectURL(file);
    const pendingId = `pending-${Date.now()}`;
    const isImage = file.type.startsWith('image/');
    setAssets((previous) => [...previous, { id: pendingId, url: localUrl, name: file.name, isImage, uploading: true }]);

    try {
      const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
      const kind = isImage
        ? 'image'
        : ['step', 'stp', 'stl', 'glb', 'dxf', 'dwg'].includes(extension)
          ? 'cad'
          : 'document';
      const record = await assetService.upload(file, { kind, source: 'conversation_attachment' });
      setAssets((previous) => previous.map((asset) => asset.url === localUrl
        ? { ...asset, id: record.id, url: isImage ? assetDownloadUrl(record.id) : localUrl, uploading: false }
        : asset));
    } catch (reason) {
      URL.revokeObjectURL(localUrl);
      setAssets((previous) => previous.filter((asset) => asset.url !== localUrl));
      setError(reason instanceof Error ? reason.message : '附件上传失败，请重试');
    }
  }, []);

  useEffect(() => {
    if (!initialPrompt || didAutoRunRef.current || loadingHistory) return;
    didAutoRunRef.current = true;
    void send(initialPrompt);
  }, [initialPrompt, loadingHistory, send]);

  const hasRunningNodes = useMemo(
    () => allNodes.some((node) => node.status === 'running' || node.status === 'queued'),
    [allNodes],
  );

  useEffect(() => {
    const conversationIdValue = convIdRef.current;
    if (!conversationIdValue || !hasRunningNodes) return;

    const interval = window.setInterval(() => {
      void workspaceGraphService.snapshot(conversationIdValue).then((snapshot) => {
        const freshNodes = snapshot.nodes;
        publishNodes(freshNodes);
        const freshMap = new Map(freshNodes.map((node) => [node.id, node]));
        setMessages((previous) => {
          const known = new Set(previous.flatMap((message) => message.nodes.map((node) => node.id)));
          const refreshed = previous.map((message) => ({
            ...message,
            nodes: message.nodes.map((node) => freshMap.get(node.id) ?? node),
          }));
          const newcomers = freshNodes.filter((node) => !known.has(node.id));
          newcomers.forEach((node) => {
            const parentIndex = refreshed.findIndex((message) => message.nodes.some((candidate) => candidate.id === node.parentId));
            const targetIndex = parentIndex >= 0 ? parentIndex : refreshed.map((message) => message.role).lastIndexOf('assistant');
            if (targetIndex >= 0) {
              refreshed[targetIndex] = {
                ...refreshed[targetIndex],
                nodes: mergeNodes(refreshed[targetIndex].nodes, [node]),
              };
            }
          });
          return refreshed;
        });

        const previewCandidate = [...freshNodes].reverse().find((node) =>
          ['render', 'model_3d', 'cad', 'engineering_package'].includes(node.type)
          && node.status === 'completed');
        if (previewCandidate) onNodeSelected?.(previewCandidate);
      }).catch(() => undefined);
    }, 3000);

    return () => window.clearInterval(interval);
  }, [hasRunningNodes, onNodeSelected, publishNodes]);

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col bg-white">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
        {loadingHistory ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            正在恢复工作区…
          </div>
        ) : messages.length === 0 && !sending ? (
          <div className="flex h-full items-center justify-center">
            <div className="max-w-md text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-950 text-white">
                <Sparkles className="h-6 w-6" />
              </div>
              <h2 className="text-lg font-semibold text-slate-900">今天你想设计什么？</h2>
              <p className="mt-2 text-sm leading-6 text-slate-400">只需要说你想要什么。AI 会建项目、理解需求、提出方向，并持续推进到渲染、3D、CAD、报价和工程包。</p>
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-5">
            {messages.map((message) => (
              <div key={message.id} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[88%] ${message.role === 'assistant' ? 'w-full' : ''}`}>
                  {message.text ? (
                    <div className={message.role === 'user'
                      ? 'inline-block whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-slate-950 px-4 py-2.5 text-sm leading-6 text-white'
                      : 'mb-2 whitespace-pre-wrap px-1 text-sm leading-6 text-slate-700'}>
                      {message.text}
                    </div>
                  ) : null}
                  {message.nodes.length > 0 ? (
                    <MessageNodes nodes={message.nodes} onAction={handleAction} onSelect={(node) => onNodeSelected?.(node)} />
                  ) : null}
                  {message.pending ? (
                    <div className="flex items-center gap-2 px-1 text-sm text-slate-400"><Loader2 className="h-4 w-4 animate-spin" />正在推进…</div>
                  ) : null}
                  {message.failed ? <div className="px-1 text-sm text-rose-500">本轮失败，可继续输入重试。</div> : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {error ? (
        <div className="mx-5 mb-2 flex items-center justify-between rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          <span className="truncate">{error}</span>
          <button type="button" onClick={() => setError(null)} className="ml-3 shrink-0 font-semibold">关闭</button>
        </div>
      ) : null}

      {assets.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 px-5 pb-2">
          {assets.map((asset) => (
            <div key={asset.url} className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600">
              {asset.isImage ? <img src={asset.url} alt={asset.name} className="h-7 w-7 rounded-lg object-cover" /> : <FileText className="h-4 w-4 text-slate-400" />}
              <span className="max-w-[160px] truncate">{asset.name}</span>
              {asset.uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" /> : (
                <button type="button" onClick={() => setAssets((previous) => previous.filter((item) => item.url !== asset.url))} className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><X className="h-3.5 w-3.5" /></button>
              )}
            </div>
          ))}
        </div>
      ) : null}

      <div className="shrink-0 bg-white px-5 pb-4 pt-2">
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-[0_10px_40px_rgba(15,23,42,0.06)] focus-within:border-slate-300">
          <input ref={fileInputRef} type="file" accept="image/*,.pdf,.step,.stp,.stl,.glb,.dxf,.dwg" className="hidden" onChange={(event) => void handleFilePick(event)} />
          <button type="button" onClick={() => fileInputRef.current?.click()} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-slate-700" title="上传图片 / PDF / CAD">
            <Paperclip className="h-4 w-4" />
          </button>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void send(input);
              }
            }}
            placeholder="继续说你的想法，AI 会自动决定下一步…"
            rows={1}
            className="max-h-32 min-h-9 flex-1 resize-none border-0 bg-transparent px-1 py-2 text-sm text-slate-900 outline-none placeholder:text-slate-400"
          />
          <button type="button" onClick={() => void send(input)} disabled={sending || (!input.trim() && assets.length === 0) || assets.some((asset) => asset.uploading)} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-950 text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-30">
            {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </button>
        </div>
      </div>
    </div>
  );
};
