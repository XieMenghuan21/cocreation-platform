import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  Box,
  ChevronRight,
  Download,
  FileArchive,
  FileCode2,
  FolderKanban,
  Layers3,
  Loader2,
  MessageSquare,
  PackageCheck,
  ReceiptText,
  Search,
  Sparkles,
  Waypoints,
} from 'lucide-react';
import type { ProjectRecord, VersionSnapshot } from '../CoCreationAgentWorkspace.types';
import { assetDownloadUrl, assetService, type AssetRecord } from '../../services/assetService';
import { cocreationHistoryService } from '../../services/cocreationHistoryService';
import { conversationService, type Conversation } from '../../services/conversationService';
import { workspaceResourceService } from '../../services/workspaceResourceService';
import type { WorkspaceNode } from '../../services/workspaceGraphService';
import type { WorkspacePrimaryView } from './workspaceResourceTypes';
import { GeneratedStlPreview } from '../ThreeMeshPreview';

interface WorkspaceResourceViewProps {
  view: Exclude<WorkspacePrimaryView, 'chat'>;
  selectedProjectId?: string | null;
  onBackToConversation: () => void;
  onOpenProject: (project: ProjectRecord) => void;
  onClearProject: () => void;
  onOpenConversation: (conversationId: string, title: string) => void;
}

interface QuoteRecord {
  id: string;
  conversationId: string;
  projectId: string | null;
  projectName: string;
  title: string;
  range: string;
  breakdown: string[];
  createdAt: string;
}

const VIEW_META: Record<Exclude<WorkspacePrimaryView, 'chat'>, { title: string; description: string }> = {
  projects: { title: '项目档案馆', description: '不是文件夹，而是每个设计项目从对话到交付的完整生命线。' },
  files: { title: '文件', description: '集中查看上传资料、CAD、PDF、脚本、工程包与其他交付文件。' },
  assets: { title: 'AI 资产中心', description: '把生成图、模型、参考资料和可复用知识沉淀成长期设计资产。' },
  versions: { title: '版本', description: '查看设计快照、迭代记录与后续分支的基础版本关系。' },
  quotes: { title: '报价', description: '所有报价结果都来自设计过程，并自动归档到对应项目。' },
};

const isImageAsset = (asset: AssetRecord): boolean =>
  asset.contentType.startsWith('image/') || asset.kind.toLowerCase().includes('image');

const isStlAsset = (asset: AssetRecord): boolean => `${asset.extension ?? ''} ${asset.kind} ${asset.filename}`.toLowerCase().includes('stl');

const isPdfAsset = (asset: AssetRecord): boolean => asset.contentType === 'application/pdf' || `${asset.extension ?? ''} ${asset.filename}`.toLowerCase().includes('.pdf');

const isDeliverable = (asset: AssetRecord): boolean => {
  const value = `${asset.kind} ${asset.filename} ${asset.extension ?? ''}`.toLowerCase();
  return ['step', 'stp', 'stl', 'glb', 'zip', 'pdf', 'bom', 'drawing', 'cad', 'package'].some((token) => value.includes(token));
};

const formatSize = (bytes: number): string => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
};

const formatDate = (value?: string | null): string => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const collectQuotes = (conversations: Conversation[], quoteNodes: WorkspaceNode[] = []): QuoteRecord[] => {
  const result: QuoteRecord[] = [];
  conversations.forEach((conversation) => {
    conversation.messages?.forEach((message) => {
      const cards = Array.isArray(message.cardData?.cards)
        ? (message.cardData.cards as Array<Record<string, unknown>>)
        : [];
      cards.forEach((card, index) => {
        if (card.type !== 'quote') return;
        const data = (card.data && typeof card.data === 'object')
          ? (card.data as Record<string, unknown>)
          : {};
        const breakdownValue = data.breakdown;
        const breakdown = Array.isArray(breakdownValue)
          ? breakdownValue.map((item) => String(item))
          : [
              Number.isFinite(Number(data.materialCost)) ? `材料 ¥${Number(data.materialCost).toLocaleString('zh-CN')}` : '',
              Number.isFinite(Number(data.productionCost)) ? `生产 ¥${Number(data.productionCost).toLocaleString('zh-CN')}` : '',
              Number.isFinite(Number(data.totalInternal)) ? `内部成本 ¥${Number(data.totalInternal).toLocaleString('zh-CN')}` : '',
            ].filter(Boolean);
        result.push({
          id: `${conversation.id}-${message.id}-${index}`,
          conversationId: conversation.id,
          projectId: conversation.projectId,
          projectName: String(data.projectName || conversation.title || '未命名项目'),
          title: String(data.schemeName || data.title || '方案报价'),
          range: Number.isFinite(Number(data.totalCustomer))
            ? `¥${Number(data.totalCustomer).toLocaleString('zh-CN')}`
            : String(data.range || data.total || data.price || '待估算'),
          breakdown,
          createdAt: message.createdAt,
        });
      });
    });
  });
  quoteNodes.forEach((node) => {
    const output = node.outputData ?? {};
    const mirroredCards = Array.isArray(output.cards) ? output.cards as Array<Record<string, unknown>> : [];
    const mirroredQuoteCard = mirroredCards.find((card) => card.type === 'quote');
    const mirroredQuoteData = mirroredQuoteCard?.data && typeof mirroredQuoteCard.data === 'object'
      ? mirroredQuoteCard.data as Record<string, unknown>
      : {};
    const effective = Object.keys(mirroredQuoteData).length > 0 ? { ...output, ...mirroredQuoteData } : output;
    const breakdownValue = effective.breakdown;
    const breakdown = Array.isArray(breakdownValue)
      ? breakdownValue.map((item) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object') {
            const row = item as Record<string, unknown>;
            const label = String(row.label || row.name || '明细');
            const amount = Number(row.amount);
            return Number.isFinite(amount) ? `${label} ¥${amount.toLocaleString('zh-CN')}` : label;
          }
          return String(item);
        })
      : [];
    const conversation = conversations.find((item) => item.id === node.conversationId);
    const rawRange = effective.range ?? effective.totalCustomer ?? effective.total ?? effective.price;
    let range = '待估算';
    if (typeof rawRange === 'number') {
      range = `¥${rawRange.toLocaleString('zh-CN')}`;
    } else if (rawRange && typeof rawRange === 'object' && !Array.isArray(rawRange)) {
      const rangeRecord = rawRange as Record<string, unknown>;
      const min = Number(rangeRecord.min);
      const max = Number(rangeRecord.max);
      if (Number.isFinite(min) && Number.isFinite(max)) {
        range = `¥${min.toLocaleString('zh-CN')} – ¥${max.toLocaleString('zh-CN')}`;
      }
    } else if (typeof rawRange === 'string' && rawRange) {
      range = rawRange;
    }
    result.push({
      id: `node-${node.id}`,
      conversationId: node.conversationId,
      projectId: node.projectId ?? conversation?.projectId ?? null,
      projectName: String(effective.projectName || conversation?.title || node.title || '未命名项目'),
      title: node.title || '方案报价',
      range,
      breakdown,
      createdAt: node.createdAt,
    });
  });

  // 同一 Graph Node / Legacy Card 在迁移期间可能同时存在，按 id 去重后排序。
  return Array.from(new Map(result.map((item) => [item.id, item])).values())
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
};

const EmptyState: React.FC<{ title: string; description: string }> = ({ title, description }) => (
  <div className="flex min-h-[360px] flex-col items-center justify-center px-6 text-center">
    <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100 text-slate-400">
      <Sparkles className="h-5 w-5" />
    </div>
    <div className="mt-4 text-sm font-semibold text-slate-800">{title}</div>
    <div className="mt-1 max-w-md text-xs leading-5 text-slate-400">{description}</div>
  </div>
);

export const WorkspaceResourceView: React.FC<WorkspaceResourceViewProps> = ({
  view,
  selectedProjectId,
  onBackToConversation,
  onOpenProject,
  onClearProject,
  onOpenConversation,
}) => {
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [versions, setVersions] = useState<VersionSnapshot[]>([]);
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [quoteNodes, setQuoteNodes] = useState<WorkspaceNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [previewAsset, setPreviewAsset] = useState<AssetRecord | null>(null);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError(null);
    void Promise.all([
      cocreationHistoryService.listAllHistory(),
      assetService.listAll(),
      conversationService.list(),
      workspaceResourceService.listNodes({ type: 'quote' }).catch(() => []),
    ])
      .then(([historyResponse, assetResponse, conversationResponse, graphQuoteNodes]) => {
        if (disposed) return;
        setProjects(historyResponse.data.projects ?? []);
        setVersions(historyResponse.data.snapshots ?? []);
        setAssets(assetResponse.items ?? []);
        setConversations(conversationResponse ?? []);
        setQuoteNodes(graphQuoteNodes ?? []);
      })
      .catch((reason) => {
        if (disposed) return;
        setError(reason instanceof Error ? reason.message : '资源读取失败');
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [view]);

  const normalizedQuery = query.trim().toLowerCase();
  const quotes = useMemo(() => collectQuotes(conversations, quoteNodes), [conversations, quoteNodes]);
  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );

  const filteredProjects = useMemo(() => {
    if (!normalizedQuery) return projects;
    return projects.filter((project) =>
      `${project.name} ${project.description} ${project.industry}`.toLowerCase().includes(normalizedQuery),
    );
  }, [normalizedQuery, projects]);

  const filteredAssets = useMemo(() => {
    if (!normalizedQuery) return assets;
    return assets.filter((asset) =>
      `${asset.filename} ${asset.kind} ${asset.extension ?? ''} ${asset.projectId ?? ''}`.toLowerCase().includes(normalizedQuery),
    );
  }, [assets, normalizedQuery]);

  const filteredVersions = useMemo(() => {
    if (!normalizedQuery) return versions;
    return versions.filter((version) =>
      `${version.label} ${version.note} ${version.projectName ?? ''} ${version.status}`.toLowerCase().includes(normalizedQuery),
    );
  }, [normalizedQuery, versions]);

  const filteredQuotes = useMemo(() => {
    if (!normalizedQuery) return quotes;
    return quotes.filter((quote) =>
      `${quote.projectName} ${quote.title} ${quote.range}`.toLowerCase().includes(normalizedQuery),
    );
  }, [normalizedQuery, quotes]);

  const projectVersions = selectedProject
    ? versions.filter((version) => version.projectId === selectedProject.id || version.sourceProjectId === selectedProject.id)
    : [];
  const projectAssets = selectedProject
    ? assets.filter((asset) => asset.projectId === selectedProject.id)
    : [];
  const projectConversations = selectedProject
    ? conversations.filter((conversation) => conversation.projectId === selectedProject.id)
    : [];
  const projectQuotes = selectedProject
    ? quotes.filter((quote) => quote.projectId === selectedProject.id)
    : [];

  const meta = VIEW_META[view];

  return (
    <section className="relative flex h-full min-w-0 flex-1 overflow-hidden bg-[#f7f7f5]">
      <div className={`min-w-0 flex-1 overflow-y-auto ${previewAsset ? 'pr-0' : ''}`}>
        <div className="mx-auto w-full max-w-[1440px] px-6 pb-16 pt-5 lg:px-10">
          <div className="sticky top-0 z-10 -mx-2 mb-6 flex flex-wrap items-center gap-3 bg-[#f7f7f5]/95 px-2 py-3 backdrop-blur-xl">
            <button
              type="button"
              onClick={onBackToConversation}
              className="flex h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-600 shadow-sm transition hover:text-slate-950"
            >
              <ArrowLeft className="h-4 w-4" />
              返回对话
            </button>
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-xl font-semibold tracking-tight text-slate-950">{meta.title}</h1>
              <p className="mt-0.5 truncate text-xs text-slate-400">{meta.description}</p>
            </div>
            <label className="flex h-10 min-w-[240px] items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 shadow-sm focus-within:border-slate-300">
              <Search className="h-4 w-4 text-slate-400" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索当前资源"
                className="min-w-0 flex-1 border-0 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-300"
              />
            </label>
          </div>

          {loading ? (
            <div className="flex min-h-[420px] items-center justify-center text-sm text-slate-400">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              正在整理 Workspace 资源…
            </div>
          ) : error ? (
            <EmptyState title="资源读取失败" description={error} />
          ) : view === 'projects' ? (
            selectedProject ? (
              <ProjectArchive
                project={selectedProject}
                versions={projectVersions}
                assets={projectAssets}
                conversations={projectConversations}
                quotes={projectQuotes}
                onBack={onClearProject}
                onOpenConversation={onOpenConversation}
                onPreviewAsset={setPreviewAsset}
              />
            ) : filteredProjects.length === 0 ? (
              <EmptyState title="还没有项目" description="用户的第一句话会自动建立项目。项目完成的对话、版本、资产、报价和交付物都会归档到这里。" />
            ) : (
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {filteredProjects.map((project) => {
                  const versionCount = versions.filter((version) => version.projectId === project.id || version.sourceProjectId === project.id).length;
                  const assetCount = assets.filter((asset) => asset.projectId === project.id).length;
                  const conversationCount = conversations.filter((conversation) => conversation.projectId === project.id).length;
                  return (
                    <button
                      key={project.id}
                      type="button"
                      onClick={() => onOpenProject(project)}
                      className="group overflow-hidden rounded-[22px] border border-slate-200/80 bg-white text-left shadow-[0_8px_30px_rgba(15,23,42,0.04)] transition hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-[0_16px_42px_rgba(15,23,42,0.08)]"
                    >
                      <div className="relative aspect-[16/8] overflow-hidden bg-gradient-to-br from-slate-100 via-stone-100 to-slate-200">
                        {project.lastImageUrl ? (
                          <img src={project.lastImageUrl} alt={project.name} className="h-full w-full object-cover transition duration-500 group-hover:scale-[1.025]" />
                        ) : (
                          <div className="flex h-full items-center justify-center text-slate-300">
                            <FolderKanban className="h-10 w-10" />
                          </div>
                        )}
                        <div className="absolute left-3 top-3 rounded-full border border-white/60 bg-white/85 px-2.5 py-1 text-[10px] font-semibold text-slate-600 backdrop-blur">
                          {project.industry || '工业设计'}
                        </div>
                      </div>
                      <div className="p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-slate-950">{project.name}</div>
                            <div className="mt-1 line-clamp-2 min-h-9 text-xs leading-[18px] text-slate-400">{project.description || '暂无项目描述'}</div>
                          </div>
                          <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-500" />
                        </div>
                        <div className="mt-4 flex items-center gap-4 border-t border-slate-100 pt-3 text-[11px] text-slate-400">
                          <span>{conversationCount} 对话</span>
                          <span>{versionCount} 版本</span>
                          <span>{assetCount} 资产</span>
                          <span className="ml-auto">{formatDate(project.updatedAt)}</span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )
          ) : view === 'assets' ? (
            filteredAssets.length === 0 ? (
              <EmptyState title="资产中心还是空的" description="效果图、3D、CAD、参考资料和后续知识资产都会自动沉淀到这里。" />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {filteredAssets.map((asset) => (
                  <AssetCard key={asset.id} asset={asset} onPreview={() => setPreviewAsset(asset)} />
                ))}
              </div>
            )
          ) : view === 'files' ? (
            filteredAssets.length === 0 ? (
              <EmptyState title="还没有文件" description="上传的 PDF、CAD、模型、脚本和工程包会统一出现在这里。" />
            ) : (
              <div className="overflow-hidden rounded-[20px] border border-slate-200 bg-white">
                <div className="grid grid-cols-[minmax(0,1fr)_120px_110px_160px_44px] gap-3 border-b border-slate-100 bg-slate-50/70 px-4 py-3 text-[11px] font-semibold text-slate-400">
                  <span>文件</span><span>类型</span><span>大小</span><span>更新时间</span><span />
                </div>
                {filteredAssets.map((asset) => (
                  <div key={asset.id} className="grid grid-cols-[minmax(0,1fr)_120px_110px_160px_44px] items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-0 hover:bg-slate-50/60">
                    <button type="button" onClick={() => setPreviewAsset(asset)} className="min-w-0 text-left">
                      <div className="truncate text-sm font-medium text-slate-800">{asset.filename}</div>
                      <div className="mt-0.5 truncate text-[10px] text-slate-400">{asset.projectId || '未关联项目'}</div>
                    </button>
                    <span className="truncate text-xs text-slate-500">{asset.kind || asset.extension || 'file'}</span>
                    <span className="text-xs text-slate-500">{formatSize(asset.sizeBytes)}</span>
                    <span className="text-xs text-slate-400">{formatDate(asset.updatedAt)}</span>
                    <a href={assetDownloadUrl(asset.id)} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700" title="下载">
                      <Download className="h-4 w-4" />
                    </a>
                  </div>
                ))}
              </div>
            )
          ) : view === 'versions' ? (
            filteredVersions.length === 0 ? (
              <EmptyState title="还没有版本" description="每次完成的重要设计节点都会形成版本快照，后续可以在此基础上继续分支和回退。" />
            ) : (
              <div className="space-y-3">
                {filteredVersions.map((version, index) => (
                  <div key={version.id} className="relative flex gap-4 rounded-[20px] border border-slate-200 bg-white p-4">
                    <div className="flex flex-col items-center">
                      <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-950 text-xs font-semibold text-white">V{version.versionNumber ?? Math.max(1, filteredVersions.length - index)}</div>
                      <div className="mt-2 h-full w-px bg-slate-100" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="text-sm font-semibold text-slate-900">{version.projectName || '项目'} · {version.label}</div>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">{version.status || 'snapshot'}</span>
                        {version.isFinalized ? <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">已定稿</span> : null}
                      </div>
                      <p className="mt-2 text-xs leading-5 text-slate-500">{version.note || version.resultText || version.executionSummary || '设计版本快照'}</p>
                      <div className="mt-3 text-[10px] text-slate-400">{formatDate(version.createdAt)}</div>
                    </div>
                    {version.previewImageUrl ? <img src={version.previewImageUrl} alt={version.label} className="h-20 w-28 rounded-xl object-cover" /> : null}
                  </div>
                ))}
              </div>
            )
          ) : filteredQuotes.length === 0 ? (
            <EmptyState title="还没有报价" description="当设计方案达到可估算条件时，AI 会主动建议生成报价，并自动归档到这里。" />
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {filteredQuotes.map((quote) => (
                <button
                  key={quote.id}
                  type="button"
                  onClick={() => onOpenConversation(quote.conversationId, quote.projectName)}
                  className="rounded-[20px] border border-slate-200 bg-white p-5 text-left transition hover:-translate-y-0.5 hover:shadow-lg"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="rounded-xl bg-amber-50 p-2 text-amber-700"><ReceiptText className="h-4 w-4" /></span>
                    <span className="text-[10px] text-slate-400">{formatDate(quote.createdAt)}</span>
                  </div>
                  <div className="mt-4 text-xs font-medium text-slate-400">{quote.projectName}</div>
                  <div className="mt-1 text-sm font-semibold text-slate-900">{quote.title}</div>
                  <div className="mt-3 text-xl font-semibold tracking-tight text-slate-950">{quote.range}</div>
                  {quote.breakdown.length > 0 ? (
                    <div className="mt-4 space-y-1 border-t border-slate-100 pt-3">
                      {quote.breakdown.slice(0, 3).map((item, index) => <div key={index} className="truncate text-[11px] text-slate-500">{item}</div>)}
                    </div>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {previewAsset ? (
        <AssetPreview asset={previewAsset} onClose={() => setPreviewAsset(null)} />
      ) : null}
    </section>
  );
};

const AssetCard: React.FC<{ asset: AssetRecord; onPreview: () => void }> = ({ asset, onPreview }) => {
  const image = isImageAsset(asset);
  return (
    <button type="button" onClick={onPreview} className="group overflow-hidden rounded-[20px] border border-slate-200 bg-white text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg">
      <div className="aspect-[4/3] overflow-hidden bg-slate-100">
        {image ? (
          <img src={assetDownloadUrl(asset.id)} alt={asset.filename} className="h-full w-full object-cover transition duration-500 group-hover:scale-[1.025]" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-slate-300">
            {isDeliverable(asset) ? <PackageCheck className="h-10 w-10" /> : <FileArchive className="h-10 w-10" />}
            <span className="mt-2 text-[10px] font-semibold uppercase tracking-wider">{asset.extension || asset.kind}</span>
          </div>
        )}
      </div>
      <div className="p-3.5">
        <div className="truncate text-xs font-semibold text-slate-800">{asset.filename}</div>
        <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-slate-400">
          <span className="truncate">{asset.kind}</span>
          <span>{formatSize(asset.sizeBytes)}</span>
        </div>
      </div>
    </button>
  );
};

const AssetPreview: React.FC<{ asset: AssetRecord; onClose: () => void }> = ({ asset, onClose }) => (
  <aside className="flex h-full w-[38%] min-w-[360px] max-w-[620px] shrink-0 flex-col border-l border-slate-200 bg-white shadow-[-18px_0_48px_rgba(15,23,42,0.05)]">
    <div className="flex h-16 items-center justify-between gap-3 border-b border-slate-100 px-4">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-slate-900">{asset.filename}</div>
        <div className="mt-0.5 text-[10px] text-slate-400">{asset.kind} · {formatSize(asset.sizeBytes)}</div>
      </div>
      <button type="button" onClick={onClose} className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-50">关闭</button>
    </div>
    <div className="min-h-0 flex-1 overflow-auto bg-[#f6f6f4] p-5">
      {isImageAsset(asset) ? (
        <img src={assetDownloadUrl(asset.id)} alt={asset.filename} className="mx-auto max-h-full max-w-full rounded-2xl object-contain shadow-lg" />
      ) : isStlAsset(asset) ? (
        <div className="h-full min-h-[420px] overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <GeneratedStlPreview downloadUrl={assetDownloadUrl(asset.id)} />
        </div>
      ) : isPdfAsset(asset) ? (
        <iframe title={asset.filename} src={assetDownloadUrl(asset.id)} className="h-full min-h-[520px] w-full rounded-2xl border border-slate-200 bg-white" />
      ) : (
        <div className="flex h-full min-h-[320px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white text-center">
          <FileCode2 className="h-12 w-12 text-slate-300" />
          <div className="mt-4 text-sm font-semibold text-slate-700">{asset.filename}</div>
          <div className="mt-1 max-w-sm text-xs leading-5 text-slate-400">STEP / GLB / ZIP / 工程包暂以下载交付；STL、PDF 和图片已经可以在右侧直接预览。</div>
          <a href={assetDownloadUrl(asset.id)} className="mt-5 flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2 text-xs font-semibold text-white">
            <Download className="h-4 w-4" /> 下载文件
          </a>
        </div>
      )}
    </div>
  </aside>
);

const ProjectArchive: React.FC<{
  project: ProjectRecord;
  versions: VersionSnapshot[];
  assets: AssetRecord[];
  conversations: Conversation[];
  quotes: QuoteRecord[];
  onBack: () => void;
  onOpenConversation: (conversationId: string, title: string) => void;
  onPreviewAsset: (asset: AssetRecord) => void;
}> = ({ project, versions, assets, conversations, quotes, onBack, onOpenConversation, onPreviewAsset }) => {
  const deliverables = assets.filter(isDeliverable);
  return (
    <div className="space-y-5">
      <div className="overflow-hidden rounded-[24px] border border-slate-200 bg-white">
        <div className="relative min-h-[210px] bg-gradient-to-br from-slate-900 via-slate-800 to-stone-700 px-6 py-6 text-white">
          {project.lastImageUrl ? <img src={project.lastImageUrl} alt={project.name} className="absolute inset-0 h-full w-full object-cover opacity-35" /> : null}
          <div className="absolute inset-0 bg-gradient-to-r from-slate-950/85 via-slate-900/60 to-transparent" />
          <div className="relative z-10 max-w-2xl">
            <button type="button" onClick={onBack} className="mb-7 flex items-center gap-2 text-xs font-semibold text-white/70 hover:text-white"><ArrowLeft className="h-4 w-4" />返回项目</button>
            <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-white/55">PROJECT ARCHIVE</div>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">{project.name}</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-white/70">{project.description || '这是由对话持续生成的项目档案。'}</p>
            <div className="mt-5 flex flex-wrap gap-2 text-[11px] text-white/70">
              <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1">{project.industry || '工业设计'}</span>
              <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1">{conversations.length} 对话</span>
              <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1">{versions.length} 版本</span>
              <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1">{assets.length} 资产</span>
            </div>
          </div>
        </div>
      </div>

      <ArchiveSection title="对话" icon={<MessageSquare className="h-4 w-4" />} count={conversations.length}>
        {conversations.length === 0 ? <ArchiveEmpty text="暂时没有关联对话" /> : conversations.map((conversation) => (
          <button key={conversation.id} type="button" onClick={() => onOpenConversation(conversation.id, conversation.title)} className="flex w-full items-center gap-3 rounded-xl border border-slate-100 px-3 py-3 text-left hover:bg-slate-50">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-slate-500"><MessageSquare className="h-4 w-4" /></span>
            <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-slate-800">{conversation.title}</span><span className="mt-0.5 block text-[10px] text-slate-400">{formatDate(conversation.updatedAt)}</span></span>
            <ChevronRight className="h-4 w-4 text-slate-300" />
          </button>
        ))}
      </ArchiveSection>

      <div className="grid gap-5 xl:grid-cols-2">
        <ArchiveSection title="版本" icon={<Waypoints className="h-4 w-4" />} count={versions.length}>
          {versions.length === 0 ? <ArchiveEmpty text="暂无版本快照" /> : versions.slice(0, 6).map((version) => (
            <div key={version.id} className="flex items-center gap-3 rounded-xl border border-slate-100 px-3 py-3">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-950 text-[10px] font-bold text-white">V{version.versionNumber ?? 1}</span>
              <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-slate-800">{version.label}</span><span className="mt-0.5 block truncate text-[10px] text-slate-400">{version.note || version.status}</span></span>
            </div>
          ))}
        </ArchiveSection>

        <ArchiveSection title="报价" icon={<ReceiptText className="h-4 w-4" />} count={quotes.length}>
          {quotes.length === 0 ? <ArchiveEmpty text="暂无报价记录" /> : quotes.slice(0, 6).map((quote) => (
            <button key={quote.id} type="button" onClick={() => onOpenConversation(quote.conversationId, quote.projectName)} className="flex w-full items-center gap-3 rounded-xl border border-slate-100 px-3 py-3 text-left hover:bg-slate-50">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-50 text-amber-700"><ReceiptText className="h-4 w-4" /></span>
              <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-slate-800">{quote.title}</span><span className="mt-0.5 block text-[10px] text-slate-400">{quote.range}</span></span>
            </button>
          ))}
        </ArchiveSection>
      </div>

      <ArchiveSection title="设计资产" icon={<Layers3 className="h-4 w-4" />} count={assets.length}>
        {assets.length === 0 ? <ArchiveEmpty text="暂无设计资产" /> : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {assets.slice(0, 8).map((asset) => (
              <button key={asset.id} type="button" onClick={() => onPreviewAsset(asset)} className="overflow-hidden rounded-xl border border-slate-100 text-left hover:border-slate-200 hover:shadow-sm">
                <div className="aspect-[4/2.6] bg-slate-100">{isImageAsset(asset) ? <img src={assetDownloadUrl(asset.id)} alt={asset.filename} className="h-full w-full object-cover" /> : <div className="flex h-full items-center justify-center text-slate-300"><Box className="h-8 w-8" /></div>}</div>
                <div className="truncate px-2.5 py-2 text-[11px] font-medium text-slate-700">{asset.filename}</div>
              </button>
            ))}
          </div>
        )}
      </ArchiveSection>

      <ArchiveSection title="交付物" icon={<PackageCheck className="h-4 w-4" />} count={deliverables.length}>
        {deliverables.length === 0 ? <ArchiveEmpty text="工程包、CAD、BOM、PDF 等最终交付物会出现在这里" /> : deliverables.map((asset) => (
          <button key={asset.id} type="button" onClick={() => onPreviewAsset(asset)} className="flex w-full items-center gap-3 rounded-xl border border-slate-100 px-3 py-3 text-left hover:bg-slate-50">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700"><PackageCheck className="h-4 w-4" /></span>
            <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-slate-800">{asset.filename}</span><span className="mt-0.5 block text-[10px] text-slate-400">{asset.kind} · {formatSize(asset.sizeBytes)}</span></span>
          </button>
        ))}
      </ArchiveSection>
    </div>
  );
};

const ArchiveSection: React.FC<{ title: string; icon: React.ReactNode; count: number; children: React.ReactNode }> = ({ title, icon, count, children }) => (
  <section className="rounded-[20px] border border-slate-200 bg-white p-4">
    <div className="mb-3 flex items-center gap-2 text-slate-700">
      <span className="text-slate-400">{icon}</span>
      <h3 className="text-sm font-semibold">{title}</h3>
      <span className="ml-auto rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">{count}</span>
    </div>
    <div className="space-y-2">{children}</div>
  </section>
);

const ArchiveEmpty: React.FC<{ text: string }> = ({ text }) => (
  <div className="rounded-xl border border-dashed border-slate-200 px-3 py-5 text-center text-xs text-slate-400">{text}</div>
);
