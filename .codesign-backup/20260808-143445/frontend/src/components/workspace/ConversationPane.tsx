import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Paperclip, Send, Sparkles } from 'lucide-react';
import {
  workspaceGraphService,
  type TurnResponse,
  type WorkspaceNode,
} from '../../services/workspaceGraphService';
import { assetService } from '../../services/assetService';
import {
  WorkspaceNodeRenderer,
  NodeCarousel,
  NodeFallback,
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
  uploading?: boolean;
}

interface ConversationPaneProps {
  conversationId?: string | null;
  initialPrompt?: string | null;
  onConversationCreated?: (conversationId: string) => void;
  onNodeSelected?: (node: WorkspaceNode | null) => void;
  onMessageCountChange?: (count: number) => void;
}

const nextLocalId = (() => {
  let counter = 0;
  return () => `msg-${Date.now()}-${(counter += 1)}`;
})();

const MessageNodes: React.FC<{
  nodes: WorkspaceNode[];
  onAction: (node: WorkspaceNode, action: NodeAction) => void;
}> = ({ nodes, onAction }) => {
  const directions = nodes.filter((n) => n.type === 'design_direction');
  const others = nodes.filter((n) => n.type !== 'design_direction');
  return (
    <>
      {directions.length > 1 ? (
        <div className="mb-2">
          <NodeCarousel
            title="设计方向"
            summary="滑动查看所有方向，选择一个继续。"
            nodes={directions}
            onAction={onAction}
          />
        </div>
      ) : null}
      {directions.length > 1
        ? null
        : directions.map((node) => (
            <div key={node.id} className="mb-2">
              <WorkspaceNodeRenderer node={node} onAction={onAction} />
            </div>
          ))}
      {others.map((node) => (
        <div key={node.id} className="mb-2">
          <WorkspaceNodeRenderer node={node} onAction={onAction} />
        </div>
      ))}
    </>
  );
};

export const ConversationPane: React.FC<ConversationPaneProps> = ({
  conversationId,
  initialPrompt,
  onConversationCreated,
  onNodeSelected,
  onMessageCountChange,
}) => {
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [assets, setAssets] = useState<PendingAsset[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const didAutoRunRef = useRef(false);
  const convIdRef = useRef<string | null>(conversationId ?? null);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    onMessageCountChange?.(messages.length);
  }, [messages.length, onMessageCountChange]);

  const applyTurn = useCallback(
    (result: TurnResponse, userText: string) => {
      convIdRef.current = result.conversationId;
      onConversationCreated?.(result.conversationId);
      const assistantNodes = [...result.nodesCreated, ...result.nodesUpdated];
      setMessages((prev) => {
        const hasPending = prev.some((m) => m.pending);
        const updated = prev.map((m) =>
          m.pending
            ? {
                ...m,
                pending: false,
                text: result.message.text,
                nodes: assistantNodes,
              }
            : m,
        );
        if (!hasPending) {
          updated.push({
            id: `assistant-${nextLocalId()}`,
            role: 'assistant',
            text: result.message.text,
            nodes: assistantNodes,
          });
        }
        return updated;
      });
      setError(null);
      if (result.workspace.previewNodeId) {
        const previewNode = [...result.nodesCreated, ...result.nodesUpdated].find(
          (n) => n.id === result.workspace.previewNodeId,
        );
        onNodeSelected?.(previewNode ?? null);
      }
      void userText;
    },
    [onConversationCreated, onNodeSelected],
  );

  const send = useCallback(
    async (text: string, action?: { nodeId: string; type: string }) => {
      const trimmed = text.trim();
      if ((!trimmed && !action) || sending) return;
      setSending(true);
      setInput('');
      const userText = trimmed;
      const sentAssetIds = assets.map((a) => a.id);
      setMessages((prev) => [
        ...prev,
        {
          id: `user-${nextLocalId()}`,
          role: 'user',
          text: userText,
          nodes: [],
        },
        {
          id: `assistant-${nextLocalId()}`,
          role: 'assistant',
          text: action ? `正在处理「${action.type}」…` : '正在思考…',
          nodes: [],
          pending: true,
        },
      ]);
      setAssets([]);
      try {
      const payload = {
        text: userText || null,
        assetIds: sentAssetIds,
        action: action
            ? { nodeId: action.nodeId, type: action.type }
            : null,
        };
        const result = action
          ? await workspaceGraphService.appendTurn(convIdRef.current!, payload)
          : convIdRef.current
            ? await workspaceGraphService.appendTurn(convIdRef.current, payload)
            : await workspaceGraphService.startTurn(payload);
        applyTurn(result, userText);
      } catch (err) {
        const message = err instanceof Error ? err.message : '请求失败';
        setError(message);
        setMessages((prev) => {
          const next = [...prev];
          const idx = next.length - 1;
          if (next[idx] && next[idx].pending) {
            next[idx] = { ...next[idx], pending: false, failed: true };
          }
          return next;
        });
      } finally {
        setSending(false);
      }
    },
    [applyTurn, sending],
  );

  const handleAction = useCallback(
    (node: WorkspaceNode, action: NodeAction) => {
      void send('', { nodeId: node.id, type: action.type });
    },
    [send],
  );

  const handleFilePick = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;
      const url = URL.createObjectURL(file);
      const pendingId = `pending-${Date.now()}`;
      setAssets((prev) => [...prev, { id: pendingId, url, name: file.name, uploading: true }]);
      event.target.value = '';
      try {
        const record = await assetService.upload(file, {
          kind: 'image',
          source: 'conversation_attachment',
        });
        setAssets((prev) =>
          prev.map((a) =>
            a.url === url ? { ...a, id: record.id, uploading: false } : a,
          ),
        );
      } catch {
        setAssets((prev) => prev.filter((a) => a.url !== url));
        setError('图片上传失败，请重试');
      }
    },
    [],
  );

  useEffect(() => {
    if (initialPrompt && !didAutoRunRef.current) {
      didAutoRunRef.current = true;
      void send(initialPrompt);
    }
  }, [initialPrompt, send]);

  useEffect(() => {
    const convId = convIdRef.current;
    if (!convId) return;

    const hasRunning = messages.some((m) =>
      m.nodes.some((n) => n.status === 'running' || n.status === 'queued'),
    );
    if (!hasRunning) return;

    const interval = setInterval(() => {
      workspaceGraphService
        .snapshot(convId)
        .then((snapshot) => {
          setMessages((prev) => {
            const knownIds = new Set<string>();
            prev.forEach((m) => m.nodes.forEach((n) => knownIds.add(n.id)));
            return prev.map((msg) => {
              const nodes = msg.nodes.map((node) => {
                const fresh = snapshot.nodes.find((n) => n.id === node.id);
                return fresh ? { ...node, ...fresh } : node;
              });
              if (msg.role !== 'assistant') return { ...msg, nodes };
              const attached = snapshot.nodes.filter((n) => {
                if (knownIds.has(n.id)) return false;
                return nodes.some((p) => p.id === n.parentId);
              });
              if (attached.length === 0) return { ...msg, nodes };
              attached.forEach((n) => knownIds.add(n.id));
              return { ...msg, nodes: [...nodes, ...attached] };
            });
          });
        })
        .catch(() => {
          /* ignore polling errors */
        });
    }, 3000);

    return () => clearInterval(interval);
  }, [messages]);

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
        {messages.length === 0 && !sending ? (
          <div className="flex h-full items-center justify-center">
            <div className="max-w-md text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-900 text-white">
                <Sparkles className="h-6 w-6" />
              </div>
              <h2 className="text-lg font-semibold text-slate-800">今天你想设计什么？</h2>
              <p className="mt-2 text-sm text-slate-500">
                输入一句话，AI 会为你建立项目、理解需求并生成设计方向。
              </p>
            </div>
          </div>
        ) : null}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div className={`max-w-[85%] ${message.role === 'user' ? '' : 'w-full'}`}>
              {message.text ? (
                <div
                  className={
                    message.role === 'user'
                      ? 'inline-block whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-slate-900 px-4 py-2.5 text-sm leading-relaxed text-white'
                      : 'mb-2 whitespace-pre-wrap px-1 text-sm leading-relaxed text-slate-700'
                  }
                >
                  {message.text}
                </div>
              ) : null}
              {message.nodes.length > 0 ? (
                <MessageNodes nodes={message.nodes} onAction={handleAction} />
              ) : null}
              {message.pending ? (
                <div className="flex items-center gap-2 px-1 text-sm text-slate-400">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在生成…
                </div>
              ) : null}
              {message.failed ? (
                <div className="px-1 text-sm text-rose-500">生成失败，请重试。</div>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      {error ? (
        <div className="mx-5 mb-2 flex items-center justify-between rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          <span className="truncate">{error}</span>
          <button type="button" onClick={() => setError(null)} className="ml-3 shrink-0 font-semibold">
            关闭
          </button>
        </div>
      ) : null}

      {assets.length > 0 ? (
        <div className="flex items-center gap-2 px-5 pb-2">
          {assets.map((asset) => (
            <div
              key={asset.url}
              className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600"
            >
              <img src={asset.url} alt={asset.name} className="h-6 w-6 rounded object-cover" />
              <span className="max-w-[140px] truncate">{asset.name}</span>
              {asset.uploading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />
              ) : (
                <button
                  type="button"
                  onClick={() => setAssets((prev) => prev.filter((a) => a.url !== asset.url))}
                  className="text-slate-400 hover:text-slate-700"
                >
                  ×
                </button>
              )}
            </div>
          ))}
        </div>
      ) : null}

      <div className="border-t border-slate-200 bg-white px-4 py-3">
        <div className="flex items-end gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => void handleFilePick(event)}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-500 transition hover:bg-slate-50"
            title="上传参考图"
          >
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
            placeholder="输入需求，或点击卡片按钮继续…"
            rows={1}
            className="max-h-32 min-h-[40px] flex-1 resize-none rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:bg-white"
          />
          <button
            type="button"
            onClick={() => void send(input)}
            disabled={sending || (!input.trim()) || assets.some((a) => a.uploading)}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-900 text-white transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
