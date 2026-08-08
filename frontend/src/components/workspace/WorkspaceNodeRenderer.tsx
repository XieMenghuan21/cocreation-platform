import React, { useCallback, useRef, useState } from 'react';
import {
  Box,
  Calculator,
  Check,
  ChevronLeft,
  ChevronRight,
  FileText,
  Image as ImageIcon,
  Loader2,
  Package,
  Sparkles,
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

const TYPE_META: Record<string, { label: string; icon: React.ReactNode; shell: string }> = {
  project: { label: '项目', icon: <Sparkles className="h-4 w-4" />, shell: 'border-slate-200 bg-white' },
  requirement: { label: '需求定义', icon: <FileText className="h-4 w-4" />, shell: 'border-sky-200 bg-sky-50/50' },
  decision: { label: '方向确认', icon: <Check className="h-4 w-4" />, shell: 'border-emerald-200 bg-emerald-50/50' },
  design_direction: { label: '设计方向', icon: <Sparkles className="h-4 w-4" />, shell: 'border-violet-200 bg-violet-50/50' },
  render: { label: '效果图', icon: <ImageIcon className="h-4 w-4" />, shell: 'border-amber-200 bg-amber-50/40' },
  model_3d: { label: '3D 模型', icon: <Box className="h-4 w-4" />, shell: 'border-indigo-200 bg-indigo-50/40' },
  cad: { label: 'CAD', icon: <FileText className="h-4 w-4" />, shell: 'border-cyan-200 bg-cyan-50/40' },
  quote: { label: '报价', icon: <Calculator className="h-4 w-4" />, shell: 'border-teal-200 bg-teal-50/40' },
  engineering_package: { label: '工程包', icon: <Package className="h-4 w-4" />, shell: 'border-orange-200 bg-orange-50/40' },
  next_action: { label: '建议下一步', icon: <Sparkles className="h-4 w-4" />, shell: 'border-slate-200 bg-white' },
};

const STATUS: Record<string, string> = {
  draft: '整理中',
  waiting_user: '等待你的确认',
  queued: '排队中',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  superseded: '未选',
};

const recActions = (node: WorkspaceNode): NodeAction[] => {
  const recs = Array.isArray(node.outputData?.recommendations)
    ? node.outputData?.recommendations as Array<{ type?: string; label?: string; description?: string }>
    : [];
  return recs.map((rec) => ({
    type: (rec.type || 'request') as NodeActionType,
    label: rec.label || rec.type || '继续',
    value: rec,
  }));
};

const actionsFor = (node: WorkspaceNode): NodeAction[] => {
  if (node.status === 'superseded' || node.status === 'failed') return [];
  if (node.type === 'next_action' && node.status === 'waiting_user') return recActions(node);
  if (node.status === 'waiting_user') {
    if (node.type === 'requirement') {
      const canProceed = node.uiData?.canProceed !== false;
      return [{ type: 'confirm', label: canProceed ? '确认需求，继续设计' : '按当前信息继续' }];
    }
    if (node.type === 'design_direction') return [{ type: 'confirm', label: '选择这个方向' }];
    return [{ type: 'confirm', label: '确认' }];
  }
  if (node.status === 'completed') {
    if (node.type === 'render') return [
      { type: 'generate_3d', label: '建立 3D' },
      { type: 'generate_cad', label: '生成 CAD' },
      { type: 'generate_quote', label: '生成报价' },
    ];
    if (node.type === 'model_3d') return [
      { type: 'generate_cad', label: '生成 CAD' },
      { type: 'generate_quote', label: '生成报价' },
      { type: 'generate_package', label: '生成工程包' },
    ];
    if (node.type === 'cad') return [
      { type: 'generate_quote', label: '生成报价' },
      { type: 'generate_package', label: '生成工程包' },
    ];
    if (node.type === 'quote') return [{ type: 'generate_package', label: '生成工程包' }];
  }
  return [];
};

const outputUrls = (node: WorkspaceNode): string[] => {
  const output = node.outputData ?? {};
  const nested = output.workflowOutputs;
  const source = nested && typeof nested === 'object' && !Array.isArray(nested)
    ? { ...(nested as Record<string, unknown>), ...output }
    : output;
  const urls: string[] = [];
  for (const key of ['renderImageUrl', 'renderPng', 'enhancedImage', 'imageUrl', 'previewUrl', 'drawingUrl', 'drawingSvg']) {
    const value = source[key];
    if (typeof value === 'string' && value && !urls.includes(value)) urls.push(value);
  }
  const generated = source.generatedImageUrls;
  if (Array.isArray(generated)) {
    generated.forEach((value) => {
      if (typeof value === 'string' && value && !urls.includes(value)) urls.push(value);
    });
  }
  return urls;
};

const RequirementDetails: React.FC<{ node: WorkspaceNode }> = ({ node }) => {
  const progress = typeof node.uiData?.completeness === 'number'
    ? Math.max(0, Math.min(100, node.uiData.completeness as number))
    : null;
  const question = typeof node.uiData?.question === 'string' ? node.uiData.question : '';
  return (
    <>
      {progress !== null ? (
        <div className="mt-3">
          <div className="mb-1 flex items-center justify-between text-[10px] text-slate-400">
            <span>需求清晰度</span><span>{progress}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-slate-900 transition-all" style={{ width: `${progress}%` }} /></div>
        </div>
      ) : null}
      {question ? (
        <div className="mt-3 rounded-xl border border-sky-100 bg-white px-3 py-2.5 text-xs leading-5 text-slate-600">
          <span className="font-semibold text-slate-900">还需要确认：</span>{question}
        </div>
      ) : null}
    </>
  );
};

const QuoteDetails: React.FC<{ node: WorkspaceNode }> = ({ node }) => {
  const range = node.outputData?.range;
  if (!range || typeof range !== 'object') return null;
  const record = range as Record<string, unknown>;
  return (
    <div className="mt-3 rounded-xl border border-teal-100 bg-white px-3 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-teal-500">估算范围</div>
      <div className="mt-1 text-lg font-semibold tracking-tight text-slate-950">¥ {String(record.min ?? '—')} — {String(record.max ?? '—')}</div>
      <div className="mt-0.5 text-[10px] text-slate-400">CNY · 以当前设计条件估算</div>
    </div>
  );
};

export const WorkspaceNodeRenderer: React.FC<WorkspaceNodeRendererProps> = ({ node, onAction }) => {
  const meta = TYPE_META[node.type] ?? { label: node.type, icon: <FileText className="h-4 w-4" />, shell: 'border-slate-200 bg-white' };
  const actions = actionsFor(node);
  const urls = outputUrls(node);
  const isInactive = node.status === 'superseded';
  return (
    <div className={`overflow-hidden rounded-2xl border shadow-[0_8px_26px_rgba(15,23,42,0.03)] ${meta.shell} ${isInactive ? 'opacity-45' : ''}`}>
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2 text-slate-700">
          {meta.icon}
          <span className="truncate text-sm font-semibold text-slate-900">{node.title}</span>
        </div>
        <span className="shrink-0 rounded-full bg-white/80 px-2 py-0.5 text-[10px] font-semibold text-slate-500">{STATUS[node.status] || node.status}</span>
      </div>
      <div className="border-t border-white/80 bg-white/72 px-4 py-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">{meta.label}</div>
        {node.summary ? <p className="mt-1.5 whitespace-pre-wrap text-sm leading-6 text-slate-600">{node.summary}</p> : null}
        {node.type === 'requirement' ? <RequirementDetails node={node} /> : null}
        {node.type === 'quote' ? <QuoteDetails node={node} /> : null}
        {(node.status === 'queued' || node.status === 'running') ? (
          <div className="mt-3 flex items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-700">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {typeof node.uiData?.progress === 'number' ? `正在执行 ${node.uiData.progress}%` : 'AI 正在后台推进…'}
          </div>
        ) : null}
        {urls.length > 0 ? (
          <div className="mt-3 grid grid-cols-2 gap-2">
            {urls.slice(0, 4).map((url) => <img key={url} src={url} alt={node.title} className="h-28 w-full rounded-xl border border-slate-200 object-cover" />)}
          </div>
        ) : null}
        {actions.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {actions.map((action, index) => (
              <button key={`${action.type}-${index}`} type="button" onClick={(event) => { event.stopPropagation(); onAction?.(node, action); }} className={index === 0 ? 'rounded-xl bg-slate-950 px-3.5 py-2 text-xs font-semibold text-white transition hover:bg-slate-800' : 'rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-600 transition hover:bg-slate-50'}>
                {action.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
};

interface NodeCarouselProps {
  title: string;
  nodes: WorkspaceNode[];
  onAction?: (node: WorkspaceNode, action: NodeAction) => void;
  summary?: string;
}

export const NodeCarousel: React.FC<NodeCarouselProps> = ({ title, nodes, onAction, summary }) => {
  const [index, setIndex] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollTo = useCallback((next: number) => {
    const value = Math.max(0, Math.min(next, nodes.length - 1));
    setIndex(value);
    const child = scrollRef.current?.children[value] as HTMLElement | undefined;
    child?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }, [nodes.length]);

  return (
    <div className="overflow-hidden rounded-2xl border border-violet-200 bg-violet-50/40">
      <div className="flex items-start justify-between gap-3 px-4 py-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Sparkles className="h-4 w-4 text-violet-500" />{title}</div>
          {summary ? <p className="mt-1 text-xs leading-5 text-slate-500">{summary}</p> : null}
        </div>
        <span className="shrink-0 rounded-full bg-white px-2 py-1 text-[10px] font-semibold text-violet-600">{nodes.filter((node) => node.status !== 'superseded').length} 个方向</span>
      </div>
      <div ref={scrollRef} className="no-scrollbar flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-4">
        {nodes.map((node) => {
          const styleKeywords = Array.isArray(node.uiData?.styleKeywords) ? (node.uiData?.styleKeywords as string[]).join(' / ') : String(node.uiData?.styleKeywords || '');
          const cmf = typeof node.uiData?.cmf === 'string' ? node.uiData.cmf : '';
          const selected = node.status === 'completed';
          const inactive = node.status === 'superseded';
          return (
            <div key={node.id} className={`flex w-64 shrink-0 snap-start flex-col rounded-xl border bg-white p-4 ${selected ? 'border-emerald-300 ring-1 ring-emerald-100' : inactive ? 'border-slate-200 opacity-40' : 'border-violet-200'}`}>
              <div className="flex items-center justify-between gap-2">
                <div className="truncate text-sm font-semibold text-slate-900">{node.title}</div>
                {selected ? <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] font-semibold text-emerald-700">已选</span> : inactive ? <span className="text-[9px] text-slate-400">未选</span> : null}
              </div>
              {node.summary ? <p className="mt-2 line-clamp-4 text-xs leading-5 text-slate-600">{node.summary}</p> : null}
              {styleKeywords ? <div className="mt-3 text-[11px] text-slate-500"><span className="font-semibold text-slate-400">造型：</span>{styleKeywords}</div> : null}
              {cmf ? <div className="mt-1 text-[11px] text-slate-500"><span className="font-semibold text-slate-400">CMF：</span>{cmf}</div> : null}
              {!selected && !inactive ? <button type="button" onClick={(event) => { event.stopPropagation(); onAction?.(node, { type: 'confirm', label: '选择这个方向' }); }} className="mt-auto pt-4"><span className="block rounded-xl bg-slate-950 px-3 py-2 text-center text-xs font-semibold text-white">选择这个方向</span></button> : null}
            </div>
          );
        })}
      </div>
      {nodes.length > 1 ? (
        <div className="flex items-center justify-between border-t border-violet-100 bg-white/70 px-3 py-2">
          <button type="button" disabled={index === 0} onClick={() => scrollTo(index - 1)} className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-30"><ChevronLeft className="h-4 w-4" /></button>
          <span className="text-[10px] text-slate-400">{index + 1} / {nodes.length}</span>
          <button type="button" disabled={index >= nodes.length - 1} onClick={() => scrollTo(index + 1)} className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-30"><ChevronRight className="h-4 w-4" /></button>
        </div>
      ) : null}
    </div>
  );
};

export const NodeFallback: React.FC<{ node: WorkspaceNode }> = ({ node }) => <WorkspaceNodeRenderer node={node} />;
