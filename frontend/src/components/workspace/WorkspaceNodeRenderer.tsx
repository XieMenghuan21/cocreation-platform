import React from 'react';
import {
  Box,
  Boxes,
  Calculator,
  Check,
  FileText,
  Package,
  Sparkles,
  X,
} from 'lucide-react';
import type { WorkspaceNode } from '../../services/workspaceGraphService';

export interface NodeAction {
  type: 'confirm' | 'select' | 'request' | 'complete';
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
    if (node.type === 'design_direction') return [{ type: 'request', label: '以此方向生成' }];
    return [];
  }
  if (node.status === 'waiting_user') {
    if (node.type === 'requirement') return [{ type: 'confirm', label: '确认需求' }];
    return [{ type: 'confirm', label: '确认' }];
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
