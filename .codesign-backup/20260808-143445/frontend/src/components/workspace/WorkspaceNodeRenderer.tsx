import React, { useCallback, useRef, useState } from 'react';
import {
  Box,
  Boxes,
  Calculator,
  Check,
  ChevronLeft,
  ChevronRight,
  FileText,
  Loader2,
  Package,
  Sparkles,
  X,
} from 'lucide-react';
import type { WorkspaceNode } from '../../services/workspaceGraphService';

export type NodeActionType =
  | 'confirm'
  | 'select'
  | 'request'
  | 'complete'
  | 'generate_3d'
  | 'generate_cad'
  | 'generate_quote'
  | 'generate_package';

export interface NodeAction {
  type: NodeActionType;
  label: string;
  value?: unknown;
}

interface WorkspaceNodeRendererProps {
  node: WorkspaceNode;
  onAction?: (node: WorkspaceNode, action: NodeAction) => void;
}

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  waiting_user: '等待确认',
  queued: '排队中',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  superseded: '已替换',
};

const TYPE_META: Record<string, { icon: React.ReactNode; label: string; accent: string }> = {
  project: { icon: <Boxes className="h-4 w-4" />, label: '项目', accent: 'border-slate-300 bg-slate-50' },
  requirement: { icon: <FileText className="h-4 w-4" />, label: '需求', accent: 'border-sky-200 bg-sky-50' },
  design_direction: { icon: <Sparkles className="h-4 w-4" />, label: '设计方向', accent: 'border-violet-200 bg-violet-50' },
  decision: { icon: <Check className="h-4 w-4" />, label: '决策', accent: 'border-emerald-200 bg-emerald-50' },
  render: { icon: <Sparkles className="h-4 w-4" />, label: '渲染', accent: 'border-amber-200 bg-amber-50' },
  model_3d: { icon: <Box className="h-4 w-4" />, label: '3D', accent: 'border-indigo-200 bg-indigo-50' },
  cad: { icon: <FileText className="h-4 w-4" />, label: 'CAD', accent: 'border-cyan-200 bg-cyan-50' },
  quote: { icon: <Calculator className="h-4 w-4" />, label: '报价', accent: 'border-teal-200 bg-teal-50' },
  engineering_package: { icon: <Package className="h-4 w-4" />, label: '工程包', accent: 'border-orange-200 bg-orange-50' },
  next_action: { icon: <Sparkles className="h-4 w-4" />, label: '下一步', accent: 'border-slate-200 bg-white' },
};

const nextActionButtons = (node: WorkspaceNode): NodeAction[] => {
  const recs = (node.outputData?.recommendations as
    | Array<{ type?: string; label?: string; description?: string }>
    | undefined) || [];
  return recs
    .map((rec) => ({
      type: (rec.type ?? 'request') as NodeActionType,
      label: rec.label ?? rec.type ?? '继续',
      value: rec,
    }))
    .filter((action) => action.label);
};

const getPreviewUrls = (node: WorkspaceNode): string[] => {
  const urls: string[] = [];
  const outputs = node.outputData || {};
  for (const key of ['renderImageUrl', 'imageUrl', 'previewUrl', 'glbUrl', 'drawingUrl']) {
    if (typeof outputs[key] === 'string' && outputs[key]) {
      urls.push(outputs[key] as string);
    }
  }
  const uiAssets = (node.uiData?.assets as Array<{ url?: string }> | undefined) || [];
  for (const asset of uiAssets) {
    if (asset.url) urls.push(asset.url);
  }
  return urls;
};

const defaultActionsFor = (node: WorkspaceNode): NodeAction[] => {
  if (node.status === 'superseded') return [];
  if (node.status === 'completed') {
    if (node.type === 'project') return [{ type: 'request', label: '继续设计' }];
    if (node.type === 'cad') return [{ type: 'generate_quote', label: '生成报价' }];
    if (node.type === 'model_3d') return [{ type: 'generate_cad', label: '生成 CAD' }];
    if (node.type === 'render') return [{ type: 'generate_3d', label: '建立 3D' }];
    return [];
  }
  if (node.status === 'waiting_user') {
    if (node.type === 'requirement') return [{ type: 'confirm', label: '确认需求' }];
    if (node.type === 'design_direction') return [{ type: 'confirm', label: '选择此方向' }];
    if (node.type === 'next_action') return nextActionButtons(node);
    return [{ type: 'confirm', label: '确认' }];
  }
  if (node.status === 'running' || node.status === 'queued') {
    if (node.type === 'render' || node.type === 'model_3d' || node.type === 'cad') {
      return [{ type: 'request', label: '刷新进度' }];
    }
    return [];
  }
  return [];
};

export const WorkspaceNodeRenderer: React.FC<WorkspaceNodeRendererProps> = ({
  node,
  onAction,
}) => {
  const meta = TYPE_META[node.type] || { icon: <FileText className="h-4 w-4" />, label: node.type, accent: 'border-slate-200 bg-white' };
  const actions = defaultActionsFor(node);
  const previewUrls = getPreviewUrls(node);
  const superseded = node.status === 'superseded';

  return (
    <div className={`overflow-hidden rounded-2xl border ${superseded ? 'border-slate-200 opacity-50' : meta.accent}`}>
      <div className="flex items-center justify-between gap-2 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-slate-600">{meta.icon}</span>
          <span className="truncate text-sm font-semibold text-slate-800">{node.title}</span>
        </div>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
            node.status === 'completed'
              ? 'bg-emerald-100 text-emerald-700'
              : node.status === 'running'
                ? 'bg-amber-100 text-amber-700'
                : node.status === 'failed'
                  ? 'bg-rose-100 text-rose-700'
                  : 'bg-slate-100 text-slate-600'
          }`}
        >
          {STATUS_LABEL[node.status] || node.status}
        </span>
      </div>

      <div className="border-t border-white/60 bg-white/60 px-4 py-3">
        <div className="flex items-center gap-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {meta.label}
          {node.agentKey ? <span className="normal-case text-slate-300">· {node.agentKey}</span> : null}
        </div>
        {node.summary ? (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-600">{node.summary}</p>
        ) : null}
        {node.type === 'project' && node.projectId ? (
          <p className="mt-1 text-xs text-slate-400">项目 ID：{node.projectId}</p>
        ) : null}

        {node.type === 'quote' && typeof node.outputData?.range === 'object'
          ? (
            <div className="mt-2 rounded-xl border border-teal-100 bg-teal-50 px-3 py-2 text-sm font-semibold text-teal-700">
              ≈ {String((node.outputData.range as Record<string, unknown>).min)} ~ {String((node.outputData.range as Record<string, unknown>).max)} CNY
            </div>
          ) : null}

        {(node.status === 'running' || node.status === 'queued') ? (
          <div className="mt-2 flex items-center gap-2 rounded-lg border border-amber-100 bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            <span>
              {typeof node.uiData?.progress === 'number'
                ? `正在执行 ${node.uiData.progress}%`
                : '排队执行中…'}
            </span>
          </div>
        ) : null}

        {previewUrls.length > 0 ? (
          <div className="mt-3 grid grid-cols-2 gap-2">
            {previewUrls.slice(0, 4).map((url, index) => (
              <img
                key={`${url}-${index}`}
                src={url}
                alt={`${node.title} 预览`}
                className="h-28 w-full rounded-xl border border-slate-200 object-cover"
              />
            ))}
          </div>
        ) : null}

        {actions.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {actions.map((action) => (
              <button
                key={action.label}
                type="button"
                onClick={() => onAction?.(node, action)}
                className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-3.5 py-1.5 text-xs font-semibold text-white transition hover:bg-slate-700"
              >
                {action.label}
                <Check className="h-3.5 w-3.5" />
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
};

export const NodeFallback: React.FC<{ node: WorkspaceNode }> = ({ node }) => (
  <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">
    <X className="h-4 w-4 text-slate-300" />
    <span className="truncate">{node.title}（{node.type}）</span>
  </div>
);

interface NodeCarouselProps {
  title: string;
  nodes: WorkspaceNode[];
  onAction?: (node: WorkspaceNode, action: NodeAction) => void;
  summary?: string;
}

/**
 * 轮播卡片：把多个同级节点（如设计方向 A/B/C）放进一张卡内横向滑动，
 * 替代竖排多张卡片。
 */
export const NodeCarousel: React.FC<NodeCarouselProps> = ({
  title,
  nodes,
  onAction,
  summary,
}) => {
  const [index, setIndex] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const scrollTo = useCallback((next: number) => {
    const clamped = Math.max(0, Math.min(next, nodes.length - 1));
    setIndex(clamped);
    const el = scrollRef.current;
    if (el) {
      const child = el.children[clamped] as HTMLElement | undefined;
      child?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    }
  }, [nodes.length]);

  const supersededCount = nodes.filter((n) => n.status === 'superseded').length;
  const liveCount = nodes.length - supersededCount;

  return (
    <div className="overflow-hidden rounded-2xl border border-violet-200 bg-violet-50/60">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-violet-600">
            <Sparkles className="h-4 w-4" />
          </span>
          <span className="truncate text-sm font-semibold text-slate-800">{title}</span>
        </div>
        <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
          {liveCount > 0 ? `${liveCount} 个可选项` : `${nodes.length} 个方向`}
        </span>
      </div>

      {summary ? (
        <p className="px-4 pb-2 text-xs leading-relaxed text-slate-500">{summary}</p>
      ) : null}

      <div
        ref={scrollRef}
        className="no-scrollbar flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-4"
      >
        {nodes.map((node) => {
          const ui = node.uiData ?? {};
          const keywords = Array.isArray(ui.styleKeywords)
            ? (ui.styleKeywords as string[]).join(' / ')
            : '';
          const cmf = typeof ui.cmf === 'string' ? ui.cmf : '';
          const isSuperseded = node.status === 'superseded';
          const isDone = node.status === 'completed';
          return (
            <div
              key={node.id}
              className={`flex w-56 shrink-0 snap-start flex-col rounded-xl border bg-white p-3.5 transition ${
                isSuperseded
                  ? 'border-slate-200 opacity-45'
                  : isDone
                    ? 'border-emerald-200 ring-1 ring-emerald-100'
                    : 'border-violet-200'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-slate-800">{node.title}</span>
                {isSuperseded ? (
                  <span className="shrink-0 rounded-full bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold text-slate-400">
                    未选
                  </span>
                ) : isDone ? (
                  <span className="flex shrink-0 items-center gap-0.5 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                    <Check className="h-2.5 w-2.5" /> 已选
                  </span>
                ) : null}
              </div>
              {node.summary ? (
                <p className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
                  {node.summary}
                </p>
              ) : null}
              {keywords ? (
                <p className="mt-2 text-[11px] text-slate-500">
                  <span className="font-semibold text-slate-400">风格：</span>
                  {keywords}
                </p>
              ) : null}
              {cmf ? (
                <p className="mt-0.5 text-[11px] text-slate-500">
                  <span className="font-semibold text-slate-400">材质：</span>
                  {cmf}
                </p>
              ) : null}
              {!isSuperseded && !isDone ? (
                <button
                  type="button"
                  onClick={() => onAction?.(node, { type: 'confirm', label: '选择此方向' })}
                  className="mt-3 inline-flex items-center justify-center gap-1.5 rounded-xl bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-slate-700"
                >
                  选择此方向
                  <Check className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </div>
          );
        })}
      </div>

      {nodes.length > 1 ? (
        <div className="flex items-center justify-between border-t border-violet-100 bg-white/70 px-3 py-2">
          <button
            type="button"
            onClick={() => scrollTo(index - 1)}
            disabled={index === 0}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 disabled:opacity-30"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-[11px] font-medium text-slate-500">
            {index + 1} / {nodes.length}
          </span>
          <button
            type="button"
            onClick={() => scrollTo(index + 1)}
            disabled={index >= nodes.length - 1}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 disabled:opacity-30"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      ) : null}
    </div>
  );
};
