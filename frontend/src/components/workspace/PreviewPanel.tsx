import React from 'react';
import { Box, Download, FileText, Image as ImageIcon, Package, X } from 'lucide-react';
import { GeneratedStlPreview } from '../ThreeMeshPreview';
import PreviewImage from '../PreviewImage';
import type { WorkspaceNode } from '../../services/workspaceGraphService';

interface PreviewPanelProps {
  node: WorkspaceNode | null;
  onClose: () => void;
}

const firstString = (object: Record<string, unknown>, keys: string[]): string | null => {
  for (const key of keys) {
    const value = object[key];
    if (typeof value === 'string' && value) return value;
  }
  return null;
};

const mergedOutputs = (node: WorkspaceNode): Record<string, unknown> => {
  const output = node.outputData ?? {};
  const nested = output.workflowOutputs;
  return nested && typeof nested === 'object' && !Array.isArray(nested)
    ? { ...(nested as Record<string, unknown>), ...output }
    : output;
};

const iconFor = (node: WorkspaceNode): React.ReactNode => {
  if (node.type === 'model_3d') return <Box className="h-4 w-4" />;
  if (node.type === 'cad') return <FileText className="h-4 w-4" />;
  if (node.type === 'engineering_package') return <Package className="h-4 w-4" />;
  return <ImageIcon className="h-4 w-4" />;
};

export const PreviewPanel: React.FC<PreviewPanelProps> = ({ node, onClose }) => {
  if (!node) return null;
  const outputs = mergedOutputs(node);
  const imageUrl = firstString(outputs, ['renderImageUrl', 'renderPng', 'enhancedImage', 'imageUrl', 'previewUrl']);
  const drawingUrl = firstString(outputs, ['drawingUrl', 'drawingSvg', 'planLineSvg']);
  const modelUrl = firstString(outputs, ['modelUrl', 'modelStl', 'modelDownloadUrl', 'modelGlb']);
  const packageUrl = firstString(outputs, ['downloadUrl', 'packageDownloadUrl']);
  const filename = firstString(outputs, ['filename']) || '工程包';

  return (
    <aside className="flex h-full w-[420px] max-w-[45vw] shrink-0 flex-col border-l border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-slate-500">{iconFor(node)}</span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-900">{node.title}</div>
            <div className="text-[10px] text-slate-400">{node.status === 'completed' ? '已完成' : node.status === 'failed' ? '执行失败' : '处理中'}</div>
          </div>
        </div>
        <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"><X className="h-4 w-4" /></button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {node.type === 'model_3d' && modelUrl ? (
          <div className="h-full min-h-[380px] overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
            <GeneratedStlPreview downloadUrl={modelUrl} />
          </div>
        ) : node.type === 'cad' && drawingUrl ? (
          <div className="flex min-h-[320px] items-center justify-center overflow-hidden rounded-2xl border border-slate-200 bg-slate-50 p-3">
            <PreviewImage src={drawingUrl} alt={node.title} className="max-h-full max-w-full object-contain" />
          </div>
        ) : imageUrl ? (
          <div className="flex min-h-[320px] items-center justify-center overflow-hidden rounded-2xl border border-slate-200 bg-slate-50 p-3">
            <PreviewImage src={imageUrl} alt={node.title} className="max-h-full max-w-full object-contain" />
          </div>
        ) : packageUrl ? (
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5">
            <Package className="h-8 w-8 text-slate-400" />
            <div className="mt-3 text-sm font-semibold text-slate-900">{filename}</div>
            <p className="mt-1 text-xs leading-5 text-slate-500">设计结果、模型、图纸和工程资料已归档。</p>
            <a href={packageUrl} className="mt-4 inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2 text-xs font-semibold text-white"><Download className="h-4 w-4" />下载工程包</a>
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-slate-200 p-8 text-center">
            {iconFor(node)}
            <p className="mt-3 text-sm text-slate-500">{node.status === 'completed' ? '该节点已完成，但暂未发现可预览资源。' : '任务执行中，完成后会自动出现在这里。'}</p>
          </div>
        )}

        {node.summary ? (
          <div className="mt-4 rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">节点说明</div>
            <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-600">{node.summary}</p>
          </div>
        ) : null}
      </div>
    </aside>
  );
};
