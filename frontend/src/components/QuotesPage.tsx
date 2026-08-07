import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Calculator, Loader2, FileText, Check } from 'lucide-react';
import { cocreationHistoryService } from '../services/cocreationHistoryService';
import { normalizeVersionSnapshots, groupSnapshotsByProject } from './CoCreationAgentWorkspace.helpers';
import type { ProjectLibraryItem } from './CoCreationAgentWorkspace.types';

interface QuoteRowProps {
  project: ProjectLibraryItem;
}

const estimateValue = (project: ProjectLibraryItem): number | null => {
  const versionCount = project.versions.length;
  if (versionCount === 0) return null;
  const basePrice = 1200 + versionCount * 600;
  const hasModel = project.versions.some((v) => v.changeType?.includes('3D') || v.changeType?.includes('STEP'));
  const hasRender = project.versions.some((v) => v.generatedImageUrls && v.generatedImageUrls.length > 0);
  return basePrice + (hasRender ? 300 : 0) + (hasModel ? 900 : 0);
};

const QuoteRow: React.FC<QuoteRowProps> = ({ project }) => {
  const value = estimateValue(project);
  const latest = project.versions[0];
  const lead = latest?.resultText || latest?.executionSummary || latest?.note || '暂无项目摘要';

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:shadow-md">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-slate-900">{project.project.name}</h3>
            <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
              {project.versions.length} 个版本
            </span>
          </div>
          <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{lead}</p>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-lg font-bold text-slate-900">
            {value != null ? `¥${value.toLocaleString()}` : '—'}
          </div>
          <div className="mt-1 flex items-center justify-end gap-1 text-[11px] font-medium text-emerald-600">
            <Check className="size-3" />
            可交付
          </div>
        </div>
      </div>
    </div>
  );
};

export const QuotesPage: React.FC = () => {
  const [snapshots, setSnapshots] = useState<ProjectLibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await cocreationHistoryService.listAllHistory();
      setSnapshots(
        groupSnapshotsByProject(normalizeVersionSnapshots(response.data.snapshots || [])),
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '报价读取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const sorted = useMemo(() => {
    return [...snapshots].sort((a, b) => {
      return (estimateValue(b) ?? 0) - (estimateValue(a) ?? 0);
    });
  }, [snapshots]);

  const totalEstimate = useMemo(
    () => sorted.reduce((sum, project) => sum + (estimateValue(project) ?? 0), 0),
    [sorted],
  );

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col overflow-y-auto p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-900">报价</h1>
          <p className="mt-0.5 text-xs text-slate-500">基于各项目版本与资产自动估算的设计报价</p>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-right shadow-sm">
          <div className="text-[11px] font-medium text-slate-400">预估总额</div>
          <div className="text-base font-bold text-slate-900">¥{totalEstimate.toLocaleString()}</div>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="size-5 animate-spin text-slate-400" />
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-600">
          {error}
        </div>
      ) : sorted.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-[28px] border border-white/80 bg-white/80 px-6 py-16 text-center shadow-[0_12px_40px_rgba(15,23,42,0.06)]">
          <div className="mb-4 rounded-3xl bg-slate-100 p-4 text-slate-400">
            <Calculator className="size-8" />
          </div>
          <h3 className="text-sm font-semibold text-slate-900">暂无报价</h3>
          <p className="mt-2 max-w-sm text-sm leading-6 text-slate-500">
            在 AI 设计助手中生成方案后，系统会自动估算各项目的设计报价。
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {sorted.map((project) => (
            <QuoteRow key={project.project.id} project={project} />
          ))}
          <p className="px-1 pt-2 text-center text-[11px] leading-5 text-slate-400">
            报价为基于版本数量、渲染与 3D 资产的系统估算，仅供参考，具体以实际交付为准。
          </p>
        </div>
      )}
    </div>
  );
};