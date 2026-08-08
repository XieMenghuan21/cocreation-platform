import React from 'react';
import { Box, FileText, Image as ImageIcon, Package, X } from 'lucide-react';
import type { WorkspaceNode } from '../../services/workspaceGraphService';

interface PreviewPanelProps {
  node: WorkspaceNode | null;
  onClose: () => void;
}

const previewUrls = (node: WorkspaceNode): string[] => {
  const urls: string[] = [];
  const outputs = node.outputData || {};
  for (const key of ['renderImageUrl', 'imageUrl', 'previewUrl', 'drawingUrl', 'glbUrl']) {
    if (typeof outputs[key] === 'string' && outputs[key]) urls.push(outputs[key] as string);
  }
  const uiAssets = (node.uiData?.assets as Array<{ url?: string }> | undefined) || [];
  for (const asset of uiAssets) {
    if (asset.url) urls.push(asset.url);
  }
  return urls;
};

const nodeIcon = (node: WorkspaceNode): React.ReactNode => {
  switch (node.type) {
    case 'model_3d':
      return <Box className="h-4 w-4" />;
    case 'cad':
      return <FileText className="h-4 w-4" />;
    case 'render':
      return <ImageIcon className="h-4 w-4" />;
    case 'engineering_package':
      return <Package className="h-4 w-4" />;
    default:
      return <ImageIcon className="h-4 w-4" />;
  }
};

export const PreviewPanel: React.FC<PreviewPanelProps> = ({ node, onClose }) => {
  if (!node) return null;
  const urls = previewUrls(node);

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-l border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-slate-500">{nodeIcon(node)}</span>
          <span className="truncate text-sm font-semibold text-slate-800">{node.title}</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          title="关闭预览"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {urls.length > 0 ? (
          <div className="space-y-3">
            {urls.map((url, index) => (
              <img
                key={`${url}-${index}`}
                src={url}
                alt={`${node.title} 预览 ${index + 1}`}
                className="w-full rounded-xl border border-slate-200 object-cover"
              />
            ))}
          </div>
        ) : node.status === 'completed' ? (
          <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center">
            <ImageIcon className="mx-auto mb-2 h-8 w-8 text-slate-300" />
            <p className="text-xs text-slate-400">该节点暂无预览资源</p>
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center">
            <p className="text-xs text-slate-400">任务执行中，完成后显示预览</p>
          </div>
        )}
      </div>
    </aside>
  );
};
