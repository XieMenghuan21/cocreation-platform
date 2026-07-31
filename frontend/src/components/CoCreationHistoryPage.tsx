import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  ArrowLeft,
  Box,
  ChevronRight,
  Clock,
  Download,
  FileImage,
  FileText,
  FolderOpen,
  Image,
  Layers,
  Package,
  Plus,
  Sparkles,
  Trash2,
  Upload,
  Wand2,
  X,
} from 'lucide-react';
import PreviewImage from './PreviewImage';
import {
  groupSnapshotsByProject,
  normalizeVersionSnapshots,
} from './CoCreationAgentWorkspace.helpers';
import {
  cocreationHistoryService,
  runHistoryMutationAndRefresh,
} from '../services/cocreationHistoryService';
import { assetService } from '../services/assetService';
import { workspaceService } from '../services/workspaceService';
import { mapDatabaseAssets } from './databaseAssetLibraryMapper';
import type {
  AssetLibraryItem,
  AssetLibraryItemKind,
  ProjectLibraryItem,
  VersionSnapshot,
} from './CoCreationAgentWorkspace.types';

type AddAssetMode = 'select' | 'publish' | 'upload' | 'prompt';

interface CoCreationHistoryPageProps {
  onBack?: () => void;
  view?: 'projects' | 'assets';
}

function formatTime(value?: string): string {
  if (!value) return '未知时间';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getStatusLabel(status: string): string {
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'processing') return '处理中';
  return status || '未知';
}

function getStatusColor(status: string): string {
  if (status === '已完成' || status === 'completed') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (status === 'failed' || status === '失败') return 'border-rose-200 bg-rose-50 text-rose-700';
  if (status === 'processing' || status === '处理中') return 'border-amber-200 bg-amber-50 text-amber-700';
  return 'border-sky-200 bg-sky-50 text-sky-700';
}

function getAssetKindLabel(kind: AssetLibraryItemKind): string {
  const labels: Record<AssetLibraryItemKind, string> = {
    image: '图像',
    prompt: 'Prompt',
    document: '文档',
    model: '三维模型',
    cad: 'CAD',
    script: '脚本',
    archive: '压缩包',
    audio: '音频',
    other: '文件',
  };
  return labels[kind];
}

function getTypeIcon(changeType?: string): React.ReactNode {
  if (!changeType) return <Activity className="h-4 w-4" />;
  if (changeType.includes('3D') || changeType.includes('STEP') || changeType.includes('CAD')) return <Box className="h-4 w-4" />;
  if (changeType.includes('图') || changeType.includes('image') || changeType.includes('渲染')) return <Image className="h-4 w-4" />;
  if (changeType.includes('方案') || changeType.includes('生成')) return <Layers className="h-4 w-4" />;
  return <FileText className="h-4 w-4" />;
}

function getProjectLeadText(project: ProjectLibraryItem): string {
  const latest = project.versions[0];
  return latest?.executionSummary || latest?.resultText || latest?.note || latest?.prompt || '暂无项目摘要';
}

function getVersionLeadText(version: VersionSnapshot): string {
  return version.resultText || version.executionSummary || version.note || version.prompt || version.optimizedPrompt || '暂无版本说明';
}

function getVersionHeroImage(version: VersionSnapshot): string | null {
  if (version.previewImageUrl) return version.previewImageUrl;
  if (version.generatedImageUrls && version.generatedImageUrls.length > 0) return version.generatedImageUrls[0];
  return null;
}

function getProjectCoverImage(project: ProjectLibraryItem): string | null {
  for (const version of project.versions) {
    const image = getVersionHeroImage(version);
    if (image) {
      return image;
    }
  }
  return null;
}

function isSameVersionRecord(
  version: Pick<VersionSnapshot, 'id' | 'projectId'>,
  target: { versionId: string; projectId?: string | null },
): boolean {
  if (version.id !== target.versionId) {
    return false;
  }
  if (!target.projectId) {
    return true;
  }
  return version.projectId === target.projectId;
}

const EmptyState: React.FC<{
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
}> = ({ title, description, actionLabel, onAction }) => (
  <div className="flex flex-col items-center justify-center rounded-[30px] border border-white/80 bg-[linear-gradient(180deg,#ffffff_0%,#f8fbff_100%)] px-6 py-16 text-center shadow-[0_18px_60px_rgba(15,23,42,0.06)]">
    <div className="mb-4 rounded-3xl border border-slate-200 bg-slate-50 p-5 text-slate-500">
      <Sparkles className="h-10 w-10" />
    </div>
    <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
    <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">{description}</p>
    {actionLabel && onAction ? (
      <button
        type="button"
        onClick={onAction}
        className="mt-6 inline-flex items-center gap-2 rounded-2xl bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
      >
        {actionLabel}
      </button>
    ) : null}
  </div>
);

const StatCard: React.FC<{
  icon: React.ReactNode;
  label: string;
  value: string;
  hint: string;
}> = ({ icon, label, value, hint }) => (
  <div className="rounded-[26px] border border-white/80 bg-white/92 p-4 shadow-[0_16px_50px_rgba(15,23,42,0.05)] backdrop-blur-sm">
    <div className="flex items-center justify-between gap-3">
      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-2 text-slate-700">{icon}</div>
      <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">{label}</span>
    </div>
    <div className="mt-4 text-2xl font-semibold text-slate-900">{value}</div>
    <div className="mt-1 text-xs text-slate-500">{hint}</div>
  </div>
);

const AddAssetModal: React.FC<{
  mode: AddAssetMode;
  onClose: () => void;
  onModeChange: (mode: AddAssetMode) => void;
  groupedProjects: ProjectLibraryItem[];
  defaultVersionId: string | null;
  onPublishVersion: (versionId: string) => void;
  onUploadExternalAsset: (payload: { title: string; description: string; file: File }) => Promise<void>;
  onCreatePromptAsset: (payload: { title: string; description: string; prompt: string }) => Promise<void>;
}> = ({
  mode,
  onClose,
  onModeChange,
  groupedProjects,
  defaultVersionId,
  onPublishVersion,
  onUploadExternalAsset,
  onCreatePromptAsset,
}) => {
  const allVersions = useMemo(
    () => groupedProjects.flatMap((project) =>
      project.versions.map((version) => ({
        projectName: project.project.name,
        version,
      })),
    ),
    [groupedProjects],
  );

  const [selectedVersionId, setSelectedVersionId] = useState<string>(defaultVersionId || allVersions[0]?.version.id || '');
  const [uploadTitle, setUploadTitle] = useState('');
  const [uploadDescription, setUploadDescription] = useState('');
  const [uploadPreviewUrl, setUploadPreviewUrl] = useState<string | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [promptTitle, setPromptTitle] = useState('');
  const [promptDescription, setPromptDescription] = useState('');
  const [promptValue, setPromptValue] = useState('');

  useEffect(() => {
    if (!selectedVersionId && allVersions[0]?.version.id) {
      setSelectedVersionId(allVersions[0].version.id);
    }
  }, [allVersions, selectedVersionId]);

  const selectedVersion = allVersions.find((item) => item.version.id === selectedVersionId) || null;

  useEffect(
    () => () => {
      if (uploadPreviewUrl) URL.revokeObjectURL(uploadPreviewUrl);
    },
    [uploadPreviewUrl],
  );

  const handleUploadChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (!uploadTitle.trim()) {
      setUploadTitle(file.name.replace(/\.[^.]+$/, ''));
    }

    if (uploadPreviewUrl) URL.revokeObjectURL(uploadPreviewUrl);
    setUploadFile(file);
    setUploadPreviewUrl(URL.createObjectURL(file));
  };

  const renderModeSelector = () => (
    <div className="grid gap-3 md:grid-cols-3">
      <button
        type="button"
        onClick={() => onModeChange('publish')}
        className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 text-left transition hover:border-slate-300 hover:bg-white"
      >
        <FolderOpen className="h-5 w-5 text-slate-700" />
        <div className="mt-3 text-sm font-semibold text-slate-900">从项目版本发布</div>
        <div className="mt-1 text-xs leading-5 text-slate-500">将项目版本中的结果图与 Prompt 一键发布到资产库。</div>
      </button>
      <button
        type="button"
        onClick={() => onModeChange('upload')}
        className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 text-left transition hover:border-slate-300 hover:bg-white"
      >
        <Upload className="h-5 w-5 text-slate-700" />
        <div className="mt-3 text-sm font-semibold text-slate-900">上传外部资产</div>
        <div className="mt-1 text-xs leading-5 text-slate-500">通过本地上传补充图像资产，立即进入资产库管理。</div>
      </button>
      <button
        type="button"
        onClick={() => onModeChange('prompt')}
        className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 text-left transition hover:border-slate-300 hover:bg-white"
      >
        <Wand2 className="h-5 w-5 text-slate-700" />
        <div className="mt-3 text-sm font-semibold text-slate-900">新建 Prompt 资产</div>
        <div className="mt-1 text-xs leading-5 text-slate-500">整理可复用的提示词模板，用于后续项目复用。</div>
      </button>
    </div>
  );

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center bg-slate-950/45 p-4 md:items-center">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-[34px] border border-white/70 bg-white/95 shadow-[0_30px_120px_rgba(15,23,42,0.18)] backdrop-blur-xl">
        <div className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-slate-200 bg-white/95 px-6 py-5 backdrop-blur">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">资产添加</div>
            <h3 className="mt-1 text-xl font-semibold text-slate-900">添加到资产库</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-slate-200 p-2 text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-6 px-6 py-6">
          {mode === 'select' ? renderModeSelector() : null}

          {mode === 'publish' ? (
            <div className="space-y-5">
              <button type="button" onClick={() => onModeChange('select')} className="text-xs font-semibold text-slate-500 transition hover:text-slate-900">
                返回入口选择
              </button>
              <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
                <label className="text-xs font-semibold text-slate-500">选择项目版本</label>
                <select
                  value={selectedVersionId}
                  onChange={(event) => setSelectedVersionId(event.target.value)}
                  className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                >
                  {allVersions.map(({ projectName, version }) => (
                    <option key={version.id} value={version.id}>
                      {projectName} / {version.label} / {formatTime(version.createdAt)}
                    </option>
                  ))}
                </select>
              </div>
              {selectedVersion ? (
                <div className="grid gap-4 rounded-[24px] border border-slate-200 bg-white p-4 md:grid-cols-[220px_1fr]">
                  <div className="overflow-hidden rounded-[20px] border border-slate-100 bg-slate-50">
                    {getVersionHeroImage(selectedVersion.version) ? (
                      <PreviewImage
                        src={getVersionHeroImage(selectedVersion.version) || ''}
                        alt={selectedVersion.version.label}
                        className="h-56 w-full object-cover"
                      />
                    ) : (
                      <div className="flex h-56 items-center justify-center text-sm text-slate-400">无可预览图片</div>
                    )}
                  </div>
                  <div className="space-y-3">
                    <div>
                      <div className="text-sm font-semibold text-slate-900">{selectedVersion.projectName}</div>
                      <div className="mt-1 text-xs text-slate-500">{selectedVersion.version.label}</div>
                    </div>
                    <div className="inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold text-slate-700">
                      {getStatusLabel(selectedVersion.version.status)}
                    </div>
                    <div className="rounded-2xl bg-slate-50 px-4 py-3 text-xs leading-6 text-slate-700 whitespace-pre-line">
                      {selectedVersion.version.prompt || selectedVersion.version.optimizedPrompt || getVersionLeadText(selectedVersion.version)}
                    </div>
                  </div>
                </div>
              ) : null}
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => selectedVersionId && onPublishVersion(selectedVersionId)}
                  disabled={!selectedVersionId}
                  className="rounded-2xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  发布所选版本
                </button>
              </div>
            </div>
          ) : null}

          {mode === 'upload' ? (
            <div className="space-y-5">
              <button type="button" onClick={() => onModeChange('select')} className="text-xs font-semibold text-slate-500 transition hover:text-slate-900">
                返回入口选择
              </button>
              <div className="grid gap-4 md:grid-cols-2">
                <label className="rounded-[24px] border border-dashed border-slate-300 bg-slate-50 p-5 text-center">
                  <input type="file" accept="image/*" className="hidden" onChange={handleUploadChange} />
                  <div className="flex flex-col items-center gap-3">
                    <div className="rounded-2xl bg-white p-3 shadow-sm">
                      <FileImage className="h-6 w-6 text-slate-700" />
                    </div>
                    <div className="text-sm font-semibold text-slate-900">选择本地图像</div>
                    <div className="text-xs leading-5 text-slate-500">支持本地选择图片，确认后上传并保存到数据库资产库。</div>
                  </div>
                </label>
                <div className="overflow-hidden rounded-[24px] border border-slate-200 bg-slate-50">
                  {uploadPreviewUrl ? (
                    <PreviewImage src={uploadPreviewUrl} alt="上传预览" className="h-56 w-full object-cover" />
                  ) : (
                    <div className="flex h-56 items-center justify-center text-sm text-slate-400">等待上传预览</div>
                  )}
                </div>
              </div>
              <div className="grid gap-4">
                <input
                  value={uploadTitle}
                  onChange={(event) => setUploadTitle(event.target.value)}
                  placeholder="资产标题"
                  className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                />
                <textarea
                  value={uploadDescription}
                  onChange={(event) => setUploadDescription(event.target.value)}
                  placeholder="资产说明"
                  rows={4}
                  className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                />
              </div>
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => {
                    if (!uploadFile) return;
                    void onUploadExternalAsset({
                      title: uploadTitle.trim() || '外部图像资产',
                      description: uploadDescription.trim() || '本地上传的外部图像资产',
                      file: uploadFile,
                    });
                  }}
                  disabled={!uploadFile}
                  className="rounded-2xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  添加图像资产
                </button>
              </div>
            </div>
          ) : null}

          {mode === 'prompt' ? (
            <div className="space-y-5">
              <button type="button" onClick={() => onModeChange('select')} className="text-xs font-semibold text-slate-500 transition hover:text-slate-900">
                返回入口选择
              </button>
              <div className="grid gap-4">
                <input
                  value={promptTitle}
                  onChange={(event) => setPromptTitle(event.target.value)}
                  placeholder="Prompt 资产标题"
                  className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                />
                <textarea
                  value={promptDescription}
                  onChange={(event) => setPromptDescription(event.target.value)}
                  placeholder="使用场景说明"
                  rows={3}
                  className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                />
                <textarea
                  value={promptValue}
                  onChange={(event) => setPromptValue(event.target.value)}
                  placeholder="输入可复用的 Prompt 内容"
                  rows={8}
                  className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                />
              </div>
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => {
                    void onCreatePromptAsset({
                      title: promptTitle.trim() || '新建 Prompt 资产',
                      description: promptDescription.trim() || '新建的 Prompt 资产',
                      prompt: promptValue.trim(),
                    });
                  }}
                  disabled={!promptValue.trim()}
                  className="rounded-2xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  创建 Prompt 资产
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};

export const CoCreationHistoryPage: React.FC<CoCreationHistoryPageProps> = ({ onBack, view = 'projects' }) => {
  const [snapshots, setSnapshots] = useState<VersionSnapshot[]>([]);
  const [referenceAssetId, setReferenceAssetId] = useState<string | null>(null);
  const [assetLibrary, setAssetLibrary] = useState<AssetLibraryItem[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [isAddAssetOpen, setIsAddAssetOpen] = useState(false);
  const [addAssetMode, setAddAssetMode] = useState<AddAssetMode>('select');
  const [historyLoadError, setHistoryLoadError] = useState<string | null>(null);
  const historyLoadGenerationRef = useRef(0);

  const refreshHistoryData = async () => {
    const generation = historyLoadGenerationRef.current + 1;
    historyLoadGenerationRef.current = generation;
    try {
      const [historyResponse, libraryResponse, workspaceResponse] = await Promise.all([
        cocreationHistoryService.listAllHistory(),
        assetService.listAll(),
        workspaceService.get(),
      ]);
      if (historyLoadGenerationRef.current !== generation) return;
      const remoteSnapshots = normalizeVersionSnapshots(
        historyResponse.data.snapshots || [],
      );
      setSnapshots(remoteSnapshots);
      setAssetLibrary(
        mapDatabaseAssets(libraryResponse.items || [], remoteSnapshots),
      );
      setReferenceAssetId(
        workspaceResponse.selectedReferenceVersionId || null,
      );
      setHistoryLoadError(null);
    } catch (error) {
      if (historyLoadGenerationRef.current !== generation) return;
      const message = error instanceof Error ? error.message : '历史记录读取失败，请重新登录后重试。';
      setSnapshots([]);
      setAssetLibrary([]);
      setReferenceAssetId(null);
      setHistoryLoadError(message);
      throw error;
    }
  };

  useEffect(() => {
    void refreshHistoryData().catch(() => undefined);
    return () => {
      historyLoadGenerationRef.current += 1;
    };
  }, []);

  const groupedProjects = useMemo(() => groupSnapshotsByProject(snapshots), [snapshots]);

  useEffect(() => {
    if (groupedProjects.length === 0) {
      setSelectedProjectId(null);
      setSelectedVersionId(null);
      return;
    }

    if (!selectedProjectId) {
      return;
    }

    const selectedProjectExists = groupedProjects.some((project) => project.project.id === selectedProjectId);
    if (!selectedProjectExists) {
      setSelectedProjectId(null);
      setSelectedVersionId(null);
      return;
    }

    const currentProject = groupedProjects.find((project) => project.project.id === selectedProjectId) || null;
    const selectedVersionExists = currentProject?.versions.some((version) => version.id === selectedVersionId) || false;

    if (!selectedVersionExists) {
      setSelectedVersionId(currentProject?.versions[0]?.id || null);
    }
  }, [groupedProjects, selectedProjectId, selectedVersionId]);

  const selectedProject = useMemo(
    () => groupedProjects.find((project) => project.project.id === selectedProjectId) || null,
    [groupedProjects, selectedProjectId],
  );

  const selectedVersion = useMemo(
    () => selectedProject?.versions.find((version) => version.id === selectedVersionId) || selectedProject?.versions[0] || null,
    [selectedProject, selectedVersionId],
  );
  const isProjectOverview = !selectedProject;

  const publishedVersionIds = useMemo(
    () => new Set(assetLibrary.filter((asset) => asset.sourceVersionId).map((asset) => asset.sourceVersionId)),
    [assetLibrary],
  );

  const finalizedAssets = useMemo(
    () => assetLibrary.filter((asset) => asset.isFinalized !== false),
    [assetLibrary],
  );
  const imageAssets = useMemo(
    () => finalizedAssets.filter((asset) => asset.kind === 'image'),
    [finalizedAssets],
  );
  const promptAssets = useMemo(
    () => finalizedAssets.filter((asset) => asset.kind === 'prompt'),
    [finalizedAssets],
  );
  const fileAssets = useMemo(
    () => finalizedAssets.filter(
      (asset) => asset.kind !== 'image' && asset.kind !== 'prompt',
    ),
    [finalizedAssets],
  );

  const handleDeleteVersion = async (versionId: string): Promise<void> => {
    const projectId = selectedProject?.project.id;
    if (!projectId) return;
    try {
      await runHistoryMutationAndRefresh(
        () => cocreationHistoryService.deleteVersion(projectId, versionId),
        refreshHistoryData,
      );
    } catch (error) {
      setHistoryLoadError(
        error instanceof Error ? error.message : '删除版本失败，请稍后重试。',
      );
    }
  };

  const handleUseAsReference = async (
    version: VersionSnapshot,
  ): Promise<void> => {
    const projectId = version.projectId || selectedProject?.project.id;
    if (!projectId) return;
    try {
      await runHistoryMutationAndRefresh(
        () => workspaceService.setReference(projectId, version.id),
        refreshHistoryData,
      );
    } catch (error) {
      setHistoryLoadError(
        error instanceof Error ? error.message : '参考版本保存失败，请稍后重试。',
      );
    }
  };

  const handlePublishToLibrary = async (
    version: VersionSnapshot,
  ): Promise<void> => {
    const projectId = version.projectId || selectedProject?.project.id;
    if (!projectId) return;
    try {
      await runHistoryMutationAndRefresh(
        () => cocreationHistoryService.publishVersion(projectId, version.id),
        refreshHistoryData,
      );
    } catch (error) {
      setHistoryLoadError(
        error instanceof Error ? error.message : '版本发布失败，请稍后重试。',
      );
      throw error;
    }
  };

  const handlePublishByVersionId = async (versionId: string): Promise<void> => {
    const version = snapshots.find((item) => isSameVersionRecord(item, { versionId, projectId: selectedProject?.project.id || null }));
    if (!version) return;
    try {
      await handlePublishToLibrary(version);
      setIsAddAssetOpen(false);
      setAddAssetMode('select');
    } catch {
      // 错误已展示，保持弹窗打开以便用户重试。
    }
  };

  const handleCreateExternalAsset = async (payload: {
    title: string;
    description: string;
    file: File;
  }): Promise<void> => {
    try {
      await assetService.upload(payload.file, {
        kind: 'image',
        source: 'external-upload',
        metadata: {
          title: payload.title,
          description: payload.description,
        },
      });
      await refreshHistoryData();
      setIsAddAssetOpen(false);
      setAddAssetMode('select');
    } catch (error) {
      setHistoryLoadError(
        error instanceof Error ? error.message : '外部资产上传失败，请稍后重试。',
      );
    }
  };

  const handleCreatePromptAsset = async (payload: {
    title: string;
    description: string;
    prompt: string;
  }): Promise<void> => {
    try {
      const safeFilename = `${payload.title.replace(/[^\p{L}\p{N}._-]+/gu, '-') || 'prompt'}.txt`;
      const file = new File([payload.prompt], safeFilename, { type: 'text/plain' });
      await assetService.upload(file, {
        kind: 'prompt',
        source: 'manual-prompt',
        metadata: payload,
      });
      await refreshHistoryData();
      setIsAddAssetOpen(false);
      setAddAssetMode('select');
    } catch (error) {
      setHistoryLoadError(
        error instanceof Error ? error.message : 'Prompt 资产保存失败，请稍后重试。',
      );
    }
  };

  if (view === 'assets') {
    return (
      <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(148,163,184,0.16),_transparent_32%),linear-gradient(180deg,#f8fafc_0%,#eef2f7_100%)]">
        <div className="w-full px-4 py-6 sm:px-5 lg:px-6">
          {historyLoadError ? (
            <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {historyLoadError}
            </div>
          ) : null}
          <section className="overflow-hidden rounded-[34px] border border-white/75 bg-white/92 shadow-[0_20px_80px_rgba(15,23,42,0.07)] backdrop-blur-xl">
            <div className="border-b border-slate-200 bg-[linear-gradient(135deg,#ffffff_0%,#f5f7fb_55%,#eef2ff_100%)] px-6 py-6">
              <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                <div className="flex items-start gap-4">
                  {onBack ? (
                    <button
                      type="button"
                      onClick={onBack}
                      className="mt-1 rounded-full border border-slate-200 p-2 text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
                    >
                      <ArrowLeft className="h-4 w-4" />
                    </button>
                  ) : null}
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Asset Center</div>
                    <h1 className="mt-2 text-2xl font-semibold text-slate-950">资产库</h1>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
                      管理来自项目版本发布的图像与 Prompt 资产，也支持通过本地上传和手动创建进行补充。
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setAddAssetMode('select');
                    setIsAddAssetOpen(true);
                  }}
                  className="inline-flex items-center justify-center gap-2 rounded-2xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800"
                >
                  <Plus className="h-4 w-4" />
                  添加资产
                </button>
              </div>

              <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard icon={<Package className="h-5 w-5" />} label="总量" value={`${finalizedAssets.length}`} hint="当前资产库总数" />
                <StatCard icon={<Image className="h-5 w-5" />} label="图像资产" value={`${imageAssets.length}`} hint="可直接作为设计参考图" />
                <StatCard icon={<FileText className="h-5 w-5" />} label="Prompt 资产" value={`${promptAssets.length}`} hint="沉淀的可复用提示词" />
                <StatCard icon={<Download className="h-5 w-5" />} label="文件资产" value={`${fileAssets.length}`} hint="CAD、模型、文档与脚本" />
              </div>
            </div>

            <div className="grid gap-6 px-6 py-6 xl:grid-cols-2">
              <section className="rounded-[28px] border border-slate-200 bg-slate-50/70 p-5">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900">图像资产</h2>
                    <p className="mt-1 text-xs text-slate-500">项目定稿图与上传图像统一沉淀。</p>
                  </div>
                  <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-600">
                    {imageAssets.length} 项
                  </span>
                </div>
                <div className="mt-4 space-y-4">
                  {imageAssets.length === 0 ? (
                    <EmptyState
                      title="暂无图像资产"
                      description="可以从项目版本发布结果图，或通过本地上传补充外部图像资产。"
                      actionLabel="添加图像资产"
                      onAction={() => {
                        setAddAssetMode('select');
                        setIsAddAssetOpen(true);
                      }}
                    />
                  ) : (
                    imageAssets.map((asset) => (
                      <article key={asset.id} className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <h3 className="text-sm font-semibold text-slate-900">{asset.title}</h3>
                            <p className="mt-1 text-xs text-slate-500">
                              {asset.sourceProjectName} · {formatTime(asset.createdAt)}
                            </p>
                          </div>
                          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700">
                            图像
                          </span>
                        </div>
                        {asset.imageUrl ? (
                          <PreviewImage
                            src={asset.imageUrl}
                            alt={asset.title}
                            className="mt-4 h-56 w-full rounded-[20px] border border-slate-100 object-cover"
                          />
                        ) : (
                          <div className="mt-4 flex h-56 items-center justify-center rounded-[20px] border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-400">
                            该资产没有图像预览
                          </div>
                        )}
                        <p className="mt-4 text-xs leading-6 text-slate-600">{asset.description}</p>
                        {asset.downloadUrl ? (
                          <a
                            href={asset.downloadUrl}
                            download={asset.title}
                            className="mt-4 inline-flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                          >
                            <Download className="h-4 w-4" />
                            下载资产
                          </a>
                        ) : null}
                      </article>
                    ))
                  )}
                </div>
              </section>

              <section className="rounded-[28px] border border-slate-200 bg-slate-50/70 p-5">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900">Prompt 资产</h2>
                    <p className="mt-1 text-xs text-slate-500">归档可复用的生成提示词与场景说明。</p>
                  </div>
                  <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-600">
                    {promptAssets.length} 项
                  </span>
                </div>
                <div className="mt-4 space-y-4">
                  {promptAssets.length === 0 ? (
                    <EmptyState
                      title="暂无 Prompt 资产"
                      description="可以从项目版本发布 Prompt，也可以手动创建新的提示词模板。"
                      actionLabel="新建 Prompt 资产"
                      onAction={() => {
                        setAddAssetMode('prompt');
                        setIsAddAssetOpen(true);
                      }}
                    />
                  ) : (
                    promptAssets.map((asset) => (
                      <article key={asset.id} className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <h3 className="text-sm font-semibold text-slate-900">{asset.title}</h3>
                            <p className="mt-1 text-xs text-slate-500">
                              {asset.sourceProjectName} · {formatTime(asset.createdAt)}
                            </p>
                          </div>
                          <span className="rounded-full border border-sky-200 bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700">
                            Prompt
                          </span>
                        </div>
                        <p className="mt-3 text-xs leading-6 text-slate-600">{asset.description}</p>
                        <div className="mt-4 rounded-[20px] border border-slate-200 bg-slate-50 px-4 py-4 text-xs leading-6 text-slate-700 whitespace-pre-line">
                          {asset.prompt || '暂无 Prompt 内容'}
                        </div>
                        {asset.downloadUrl ? (
                          <a
                            href={asset.downloadUrl}
                            download={asset.title}
                            className="mt-4 inline-flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                          >
                            <Download className="h-4 w-4" />
                            下载资产
                          </a>
                        ) : null}
                      </article>
                    ))
                  )}
                </div>
              </section>

              <section className="rounded-[28px] border border-slate-200 bg-slate-50/70 p-5 xl:col-span-2">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900">文件资产</h2>
                    <p className="mt-1 text-xs text-slate-500">CAD、三维模型、脚本、文档、压缩包与音频文件。</p>
                  </div>
                  <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-600">
                    {fileAssets.length} 项
                  </span>
                </div>
                {fileAssets.length === 0 ? (
                  <div className="mt-4 rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-400">
                    暂无文件资产
                  </div>
                ) : (
                  <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {fileAssets.map((asset) => (
                      <article key={asset.id} className="rounded-[22px] border border-slate-200 bg-white p-4 shadow-sm">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <h3 className="truncate text-sm font-semibold text-slate-900">{asset.title}</h3>
                            <p className="mt-1 text-xs text-slate-500">
                              {asset.sourceProjectName} · {formatTime(asset.createdAt)}
                            </p>
                          </div>
                          <span className="shrink-0 rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-[11px] font-semibold text-violet-700">
                            {getAssetKindLabel(asset.kind)}
                          </span>
                        </div>
                        <p className="mt-3 line-clamp-3 text-xs leading-6 text-slate-600">{asset.description}</p>
                        {asset.downloadUrl ? (
                          <a
                            href={asset.downloadUrl}
                            download={asset.title}
                            className="mt-4 inline-flex items-center gap-2 rounded-xl bg-slate-900 px-3 py-2 text-xs font-semibold text-white transition hover:bg-slate-800"
                          >
                            <Download className="h-4 w-4" />
                            下载文件
                          </a>
                        ) : null}
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </div>
          </section>
        </div>

        {isAddAssetOpen ? (
          <AddAssetModal
            mode={addAssetMode}
            onClose={() => {
              setIsAddAssetOpen(false);
              setAddAssetMode('select');
            }}
            onModeChange={setAddAssetMode}
            groupedProjects={groupedProjects}
            defaultVersionId={selectedVersion?.id || null}
            onPublishVersion={handlePublishByVersionId}
            onUploadExternalAsset={handleCreateExternalAsset}
            onCreatePromptAsset={handleCreatePromptAsset}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(148,163,184,0.16),_transparent_32%),linear-gradient(180deg,#f8fafc_0%,#eef2f7_100%)]">
      <div className="w-full px-4 py-6 sm:px-5 lg:px-6">
        {historyLoadError ? (
          <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {historyLoadError}
          </div>
        ) : null}
        <section className="overflow-hidden rounded-[34px] border border-white/75 bg-white/92 shadow-[0_20px_80px_rgba(15,23,42,0.07)] backdrop-blur-xl">
          <div className="border-b border-slate-200 bg-[linear-gradient(135deg,#ffffff_0%,#f7f8fc_50%,#eef2ff_100%)] px-6 py-6">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
              <div className="flex items-start gap-4">
                {onBack ? (
                  <button
                    type="button"
                    onClick={onBack}
                    className="mt-1 rounded-full border border-slate-200 p-2 text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
                  >
                    <ArrowLeft className="h-4 w-4" />
                  </button>
                ) : null}
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Project Library</div>
                  <h1 className="mt-2 text-2xl font-semibold text-slate-950">项目库</h1>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
                    先查看项目卡片，再进入单独项目页查看历史版本、Prompt 和生成结果。
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                {referenceAssetId ? (
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700">
                    已设置参考资产
                  </span>
                ) : null}
              </div>
            </div>

            <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard icon={<FolderOpen className="h-5 w-5" />} label="项目" value={`${groupedProjects.length}`} hint="当前归档项目数" />
              <StatCard icon={<Layers className="h-5 w-5" />} label="版本" value={`${snapshots.length}`} hint="项目累计版本数" />
              <StatCard icon={<Package className="h-5 w-5" />} label="已发布" value={`${publishedVersionIds.size}`} hint="已进入资产库的版本" />
              <StatCard icon={<Clock className="h-5 w-5" />} label="最近更新" value={formatTime(groupedProjects[0]?.versions[0]?.createdAt)} hint="最新版本发布时间" />
            </div>
          </div>

          <div className="px-6 py-6">
            {groupedProjects.length === 0 ? (
              <EmptyState
                title="暂无项目"
                description="在工作台完成共创生成后，这里会自动显示项目卡片、版本列表和详情。"
                actionLabel={onBack ? '返回工作台' : undefined}
                onAction={onBack}
              />
            ) : isProjectOverview ? (
              <div className="space-y-6">
                <div className="flex items-end justify-between gap-4">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Projects</div>
                    <h2 className="mt-2 text-xl font-semibold text-slate-900">我的项目</h2>
                  </div>
                  <div className="text-sm text-slate-500">{groupedProjects.length} 个项目</div>
                </div>

                <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
                  {groupedProjects.map((project) => {
                    const latest = project.versions[0];
                    const coverImage = getProjectCoverImage(project);
                    return (
                      <button
                        key={project.project.id}
                        type="button"
                        onClick={() => {
                          setSelectedProjectId(project.project.id);
                          setSelectedVersionId(project.versions[0]?.id || null);
                        }}
                        className="overflow-hidden rounded-[28px] border border-slate-200 bg-white text-left shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-lg"
                      >
                        <div className="h-44 bg-slate-100">
                          {coverImage ? (
                            <PreviewImage
                              src={coverImage}
                              alt={project.project.name}
                              className="h-full w-full object-cover"
                            />
                          ) : (
                            <div className="flex h-full items-center justify-center text-slate-400">
                              <Sparkles className="h-8 w-8" />
                            </div>
                          )}
                        </div>
                        <div className="space-y-4 p-5">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">项目卡片</div>
                              <h3 className="mt-2 truncate text-lg font-semibold text-slate-900">{project.project.name}</h3>
                            </div>
                            <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${getStatusColor(latest?.status || '')}`}>
                              {getStatusLabel(latest?.status || '')}
                            </span>
                          </div>
                          <p className="line-clamp-3 text-sm leading-6 text-slate-500">{getProjectLeadText(project)}</p>
                          <div className="flex items-center justify-between text-xs text-slate-400">
                            <span>{project.versions.length} 个历史版本</span>
                            <span>{formatTime(latest?.createdAt)}</span>
                          </div>
                          <div className="inline-flex items-center gap-2 text-sm font-semibold text-slate-900">
                            查看项目详情
                            <ChevronRight className="h-4 w-4" />
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
                <section className="rounded-[28px] border border-slate-200 bg-slate-50/70 p-5">
                  <div className="flex flex-col gap-4 border-b border-slate-200 pb-4 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedProjectId(null);
                          setSelectedVersionId(null);
                        }}
                        className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-100 hover:text-slate-900"
                      >
                        <ArrowLeft className="h-3.5 w-3.5" />
                        返回项目库
                      </button>
                      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Versions</div>
                      <h2 className="mt-2 text-xl font-semibold text-slate-900">{selectedProject?.project.name || '请选择项目'}</h2>
                    </div>
                    <div className="text-sm text-slate-500">{selectedProject?.versions.length || 0} 个版本</div>
                  </div>

                  <div className="mt-5 space-y-4">
                    {selectedProject?.versions.map((version, index) => {
                      const active = version.id === selectedVersion?.id;
                      const published = publishedVersionIds.has(version.id);

                      return (
                        <button
                          key={version.id}
                          type="button"
                          onClick={() => setSelectedVersionId(version.id)}
                          className={`w-full rounded-[24px] border p-4 text-left transition ${
                            active
                              ? 'border-slate-900 bg-white shadow-md'
                              : 'border-slate-200 bg-white/75 hover:border-slate-300 hover:bg-white'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-slate-600">
                                  {getTypeIcon(version.changeType)}
                                </span>
                                <span className="text-sm font-semibold text-slate-900">{version.label}</span>
                                <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
                                  V{selectedProject.versions.length - index}
                                </span>
                                {referenceAssetId === version.id ? (
                                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                                    参考资产
                                  </span>
                                ) : null}
                                {published ? (
                                  <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-semibold text-sky-700">
                                    已发布
                                  </span>
                                ) : null}
                              </div>
                              <p className="mt-3 line-clamp-3 text-xs leading-6 text-slate-500">{getVersionLeadText(version)}</p>
                            </div>
                            <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${getStatusColor(version.status)}`}>
                              {getStatusLabel(version.status)}
                            </span>
                          </div>
                          <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                            <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1">
                              <Clock className="h-3.5 w-3.5" />
                              发布时间 {formatTime(version.createdAt)}
                            </span>
                            {version.changeType ? (
                              <span className="rounded-full bg-slate-100 px-2.5 py-1">{version.changeType}</span>
                            ) : null}
                            {version.generatedAssets?.length ? (
                              <span className="rounded-full bg-slate-100 px-2.5 py-1">{version.generatedAssets.length} 个导出资产</span>
                            ) : null}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </section>

                <aside className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
                  {selectedVersion ? (
                    <div className="space-y-5">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Version Detail</div>
                          <h2 className="mt-2 text-xl font-semibold text-slate-900">{selectedVersion.label}</h2>
                          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                            <span>{selectedProject?.project.name}</span>
                            <ChevronRight className="h-3.5 w-3.5" />
                            <span>发布时间 {formatTime(selectedVersion.createdAt)}</span>
                          </div>
                        </div>
                        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${getStatusColor(selectedVersion.status)}`}>
                          {getStatusLabel(selectedVersion.status)}
                        </span>
                      </div>

                      {getVersionHeroImage(selectedVersion) ? (
                        <PreviewImage
                          src={getVersionHeroImage(selectedVersion) || ''}
                          alt={selectedVersion.label}
                          className="h-64 w-full rounded-[24px] border border-slate-100 object-cover"
                        />
                      ) : (
                        <div className="flex h-64 items-center justify-center rounded-[24px] border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-400">
                          当前版本没有可直接展示的结果图
                        </div>
                      )}

                      <div className="grid gap-3 sm:grid-cols-2">
                        <div className="rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-4">
                          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">状态</div>
                          <div className="mt-2 text-sm font-semibold text-slate-900">{getStatusLabel(selectedVersion.status)}</div>
                        </div>
                        <div className="rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-4">
                          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">发布时间</div>
                          <div className="mt-2 text-sm font-semibold text-slate-900">{formatTime(selectedVersion.createdAt)}</div>
                        </div>
                      </div>

                      <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Prompt</div>
                        <div className="mt-3 rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-4 text-xs leading-6 text-slate-700 whitespace-pre-line">
                          {selectedVersion.prompt || selectedVersion.optimizedPrompt || '该版本没有记录 Prompt。'}
                        </div>
                      </div>

                      <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">生成结果</div>
                        <div className="mt-3 rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-4 text-xs leading-6 text-slate-700 whitespace-pre-line">
                          {selectedVersion.resultText || selectedVersion.executionSummary || selectedVersion.note || '暂无结果描述。'}
                        </div>
                      </div>

                      {selectedVersion.generatedImageUrls && selectedVersion.generatedImageUrls.length > 0 ? (
                        <div>
                          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">结果图集</div>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            {selectedVersion.generatedImageUrls.map((url) => (
                              <PreviewImage
                                key={url}
                                src={url}
                                alt={selectedVersion.label}
                                className="h-36 w-full rounded-[20px] border border-slate-100 object-cover"
                              />
                            ))}
                          </div>
                        </div>
                      ) : null}

                      {selectedVersion.generatedAssets && selectedVersion.generatedAssets.length > 0 ? (
                        <div>
                          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">导出资产</div>
                          <div className="mt-3 space-y-2">
                            {selectedVersion.generatedAssets.map((asset) => (
                              <div key={`${asset.name}-${asset.assetType}-${asset.path || 'asset'}`} className="flex items-center justify-between gap-3 rounded-[18px] border border-slate-200 bg-slate-50 px-3 py-3">
                                <div className="min-w-0">
                                  <div className="truncate text-xs font-semibold text-slate-900">{asset.name}</div>
                                  <div className="mt-1 text-[11px] text-slate-500">{asset.assetType}{asset.status ? ` · ${asset.status}` : ''}</div>
                                </div>
                                {asset.downloadUrl ? (
                                  <a
                                    href={asset.downloadUrl}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="rounded-full border border-slate-200 p-2 text-slate-600 transition hover:bg-white hover:text-slate-900"
                                  >
                                    <Download className="h-4 w-4" />
                                  </a>
                                ) : null}
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      {selectedVersion.diagnostics && selectedVersion.diagnostics.length > 0 ? (
                        <div>
                          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">诊断信息</div>
                          <div className="mt-3 space-y-2">
                            {selectedVersion.diagnostics.map((item, index) => (
                              <div
                                key={`${item.title}-${index}`}
                                className={`rounded-[18px] px-4 py-3 text-xs ${
                                  item.level === 'error' ? 'bg-rose-50 text-rose-700' : 'bg-sky-50 text-sky-700'
                                }`}
                              >
                                <div className="font-semibold">{item.title}</div>
                                {item.detail ? <div className="mt-1 leading-6 opacity-90">{item.detail}</div> : null}
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      <div className="flex flex-wrap gap-3 pt-2">
                        <button
                          type="button"
                          onClick={() => handleUseAsReference(selectedVersion)}
                          className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100"
                        >
                          设为参考资产
                        </button>
                        <button
                          type="button"
                          onClick={() => handlePublishToLibrary(selectedVersion)}
                          className="rounded-2xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white transition hover:bg-slate-800"
                        >
                          发布到资产库
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteVersion(selectedVersion.id)}
                          className="inline-flex items-center gap-2 rounded-2xl border border-rose-200 bg-white px-4 py-3 text-sm font-semibold text-rose-600 transition hover:bg-rose-50"
                        >
                          <Trash2 className="h-4 w-4" />
                          删除版本
                        </button>
                      </div>
                    </div>
                  ) : (
                    <EmptyState
                      title="请选择版本"
                      description="从中间版本列表选择一个版本后，这里会展示 Prompt、结果图、状态、发布时间和操作入口。"
                    />
                  )}
                </aside>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
};

export default CoCreationHistoryPage;
