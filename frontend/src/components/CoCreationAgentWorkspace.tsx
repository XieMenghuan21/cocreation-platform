/**
 * 共创智能体工业设计工作台
 * 聚焦全行业 CAD 设计、3D 预览与爆炸图协同场景。
 */
import React, { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import {
  Activity,
  Bot,
  Boxes,
  Clock,
  DraftingCompass,
  Download,
  ExternalLink,
  FileText,
  FileStack,
  Gauge,
  Layers3,
  Move3D,
  Plus,
  ScanSearch,
  Sliders,
  Sparkles,
  Square,
  UploadCloud,
  Workflow,
  Wrench,
  X,
} from 'lucide-react';
import {
  createIndustrialDesignWorkflow,
  getIndustrialDesignWorkflowTask,
  generateForgeCadModel,
  generateWithDrawing,
  uploadForgeCadImportAsset,
  type CadAiTaskStatus,
  type ForgeCadExplosionStep,
  type ForgeCadGeneratedAsset,
  type ForgeCadGenerateResult,
  type ForgeCadImportAsset,
  type ForgeCadVersionSnapshot,
  type IndustrialDesignInputType,
} from '../services/forgecadService';
import { aggregationWorkbenchService } from '../services/aggregationWorkbenchService';
import { cocreationHistoryService } from '../services/cocreationHistoryService';
import {
  createDebouncedWorkspaceWriter,
  validateWorkspaceAssetReferences,
  WorkspaceMutationQueue,
  workspaceService,
  type DebouncedWorkspaceWriter,
  type WorkspaceState,
  type WorkspaceUpdate,
} from '../services/workspaceService';
import {
  assetDownloadUrl,
  assetService,
  type AssetRecord,
} from '../services/assetService';

import type {
  ViewMode,
  ProjectInputMode,
  UploadDesignIntent,
  ProjectRecord,
  SceneMode,
  CoCreationScenario,
  WorkflowStage,
  IndustryFilter,
  IndustryTemplate,
  IndustryLeaf,
  IndustrySegment,
  IndustryGroup,
  IndustryRoot,
  PartNode,
  BomRow,
  ProjectDraft,
  VersionSnapshot,
  RefineActionState,
  BuildVersionSnapshotArgs,
  SubmitFeedbackState,
  ResolvedProjectDraft,
  CadAiWorkflowState,
  RefineType,
  VersionSnapshot as VersionSnapshotType,
} from './CoCreationAgentWorkspace.types';
import {
  acceptedCadImportExtensions,
  maxCadImportSizeBytes,
  industryCategories,
  industryCatalog,
  templates,
  partTree,
  bomRows,
  inputModes,
  uploadDesignIntents,
  emptyStepPreviewAsset,
  workspacePreviewHeightClass,
  workspacePreviewImageClass,
  workspacePreviewImageFrameClass,
  scenarioConfigs,
  scenarioTabs,
} from './CoCreationAgentWorkspace.constants';
import {
  normalizeVersionSnapshots,
  formatSnapshotTime,
  formatFileSize,
  getCadAiOutputValue,
  buildCadAiGeneratedAssets,
  buildVersionSnapshot,
  buildCadAiVersionSnapshot,
  buildAssetsFromVersion,
  getProjectVersionCount,
  getVersionsForProject,
  resolveIndustrialDesignSubmitError,
  getIndustryLeafPathLabel,
  getAllIndustryLeaves,
  buildScenarioFlow,
  getScenarioStepLabel,
  buildProjectId,
  ensureUniqueProjectName,
  extractAssetIdFromWorkflowReference,
} from './CoCreationAgentWorkspace.helpers';
import PreviewImage from './PreviewImage';
import { DxfPreview, StepProxyPreview, ExplodedPreview } from './CadPreviewComponents';
import { normalizePreviewImageSource } from '../utils/previewImage';

const JscadAgentPreview = lazy(() => import('./JscadAgentPreview'));
const StlPreview = lazy(() => import('./ThreeMeshPreview').then((module) => ({ default: module.StlPreview })));
const GeneratedStlPreview = lazy(() => import('./ThreeMeshPreview').then((module) => ({ default: module.GeneratedStlPreview })));

const isExternalImageReferenceUrl = (value: string): boolean => /^https?:\/\//i.test(value.trim());

const databaseAssetToForgeImport = (
  asset: AssetRecord,
  previewAsset: AssetRecord | null,
): ForgeCadImportAsset => {
  const extension = asset.extension || asset.filename.split('.').pop()?.toLowerCase() || '';
  const parseStatus = typeof asset.metadata.parseStatus === 'string'
    ? asset.metadata.parseStatus
    : 'restored';
  const parseMessage = typeof asset.metadata.parseMessage === 'string'
    ? asset.metadata.parseMessage
    : '已从数据库恢复上传资产。';
  return {
    assetId: asset.id,
    filename: asset.filename,
    extension,
    contentType: asset.contentType,
    sizeBytes: asset.sizeBytes,
    storagePath: '',
    createdAt: asset.createdAt,
    parseStatus,
    parseMessage,
    parseFeatures: [],
    previewKind: previewAsset ? 'stl' : extension === 'stl' ? 'stl' : extension === 'dxf' ? 'dxf' : 'none',
    previewAssetId: previewAsset?.id ?? null,
    previewAssetPath: null,
    previewAssetFormat: previewAsset?.extension ?? null,
    previewAssetUrl: previewAsset ? assetDownloadUrl(previewAsset.id) : null,
    conversionStatus: previewAsset ? 'converted' : null,
    conversionMessage: previewAsset ? '已从数据库恢复预览资产。' : null,
    previewEntities: [],
    bomItems: [],
    explosionSteps: [],
    downloadUrl: assetDownloadUrl(asset.id),
  };
};


const CadImportPreview: React.FC<{ asset: ForgeCadImportAsset | null; mode: ViewMode }> = ({ asset, mode }) => {
  if (!asset) {
    return (
      <div className="rounded-2xl border border-dashed border-white/15 bg-slate-950/45 px-8 py-10 text-center text-sm leading-7 text-slate-300">
        暂无 CAD 文件。上传 STL/DXF/STEP 后，这里会显示可用的在线预览或转换状态。
      </div>
    );
  }

  if (mode === 'exploded') {
    return <ExplodedPreview steps={asset.explosionSteps} />;
  }

  if (asset.previewKind === 'stl') {
    return (
      <Suspense fallback={<PreviewLoader message="正在加载 STL 预览..." />}>
        <StlPreview asset={asset} />
      </Suspense>
    );
  }

  if (asset.previewKind === 'dxf') {
    return <DxfPreview asset={asset} />;
  }

  if (asset.previewKind === 'step_pending_conversion') {
    return <StepProxyPreview asset={asset} />;
  }

  return (
    <div className="w-full max-w-3xl rounded-2xl border border-white/10 bg-slate-950/70 p-5 text-slate-100 shadow-[0_18px_60px_rgba(0,0,0,0.28)]">
      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">CAD Preview</div>
      <div className="mt-2 text-lg font-bold">{asset.filename}</div>
      <div className="mt-3 text-sm leading-7 text-slate-300">{asset.parseMessage}</div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {asset.parseFeatures.map((feature) => (
          <div key={`${feature.label}-${feature.value}`} className="rounded-xl bg-white/5 p-3">
            <div className="text-xs text-slate-400">{feature.label}</div>
            <div className="mt-1 break-all text-sm font-semibold">{feature.value}</div>
          </div>
        ))}
      </div>
      <div className="mt-4 rounded-xl border border-amber-300/20 bg-amber-300/10 p-3 text-xs leading-6 text-amber-100">
        {asset.previewKind === 'step_pending_conversion'
          ? 'STEP 已完成基础信息读取；在线三维预览需要后续接入 STEP -> STL/glTF 转换器。'
          : '该格式已保存，预览需要接入专用解析器后展示。'}
      </div>
    </div>
  );
};

const PreviewLoader: React.FC<{ message: string }> = ({ message }) => (
  <div className={`flex ${workspacePreviewHeightClass} items-center justify-center rounded-2xl border border-white/10 bg-slate-950/80 text-center`}>
    <div className="space-y-3 px-6">
      <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-cyan-200/30 border-t-cyan-300" />
      <div className="text-sm font-medium text-slate-200">{message}</div>
    </div>
  </div>
);

const CoCreationAgentWorkspace: React.FC<{ variant?: 'standalone' | 'embedded' }> = ({ variant }) => {
  // 自动检测是否为独立页面模式
  const location = useLocation();
  const isStandalone = variant === 'standalone' || location.pathname.startsWith('/cocreation');
  const [viewMode, setViewMode] = useState<ViewMode>('preview3d');
  const [selectedIndustry, setSelectedIndustry] = useState<IndustryFilter>('全部行业');
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isDraftingNewProject, setIsDraftingNewProject] = useState(false);
  const [currentProjectName, setCurrentProjectName] = useState('工业设计总控台');
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [projectList, setProjectList] = useState<ProjectRecord[]>([]);
  const [selectedPreviewVersion, setSelectedPreviewVersion] = useState<VersionSnapshotType | null>(null);
  const [selectedReferenceAsset, setSelectedReferenceAsset] = useState<VersionSnapshotType | null>(null);
  const [isVersionPickerOpen, setIsVersionPickerOpen] = useState(false);
  const [pendingReferenceVersionId, setPendingReferenceVersionId] = useState<string>('');
  const [taskStatus, setTaskStatus] = useState('尚未提交 ForgeCAD 建模任务');
  const [workflowNotice, setWorkflowNotice] = useState('当前工作区暂无真实生成结果，请先新建设计项目或提交修改任务。');
  const [rotationDeg, setRotationDeg] = useState(-18);
  const [explodeDistance, setExplodeDistance] = useState(26);
  const [sceneMode, setSceneMode] = useState<SceneMode>('poster');
  const [activeScenario, setActiveScenario] = useState<CoCreationScenario>('design');
  const [activeWorkflowStage, setActiveWorkflowStage] = useState<WorkflowStage>('design');
  const [activeStepIndex, setActiveStepIndex] = useState(0);
  const [isSubmittingForgeCad, setIsSubmittingForgeCad] = useState(false);
  const [versionSnapshots, setVersionSnapshots] = useState<VersionSnapshot[]>([]);
  const [, setWorkspaceState] = useState<WorkspaceState | null>(null);
  const [projectDraft, setProjectDraft] = useState<ProjectDraft>({
    name: '',
    industry: '装备制造',
    inputMode: 'prompt',
    uploadIntent: 'drawing',
    description: '',
  });
  const [isDescriptionManuallyEdited, setIsDescriptionManuallyEdited] = useState(false);
  const [generationPrompt, setGenerationPrompt] = useState('');
  const [isPromptManuallyEdited, setIsPromptManuallyEdited] = useState(false);
  const [isOptimizingDescription, setIsOptimizingDescription] = useState(false);
  const [imageModelOptions, setImageModelOptions] = useState<Array<{ id: string; label: string; provider: string; connected: boolean; description: string | null }>>([]);
  const [selectedImageModelId, setSelectedImageModelId] = useState<string>('auto');
  const imageModelPreferenceAppliedRef = useRef(false);
  const [refineAction, setRefineAction] = useState<RefineActionState | null>(null);
  const [refineNote, setRefineNote] = useState('');
  const [submitFeedback, setSubmitFeedback] = useState<SubmitFeedbackState | null>(null);
  const [workspaceLoadError, setWorkspaceLoadError] = useState<string | null>(null);
  const [workspaceLoadAttempt, setWorkspaceLoadAttempt] = useState(0);
  const [isUploadingCadImport, setIsUploadingCadImport] = useState(false);
  const [importedCadAsset, setImportedCadAsset] = useState<ForgeCadImportAsset | null>(null);
  const [cadAiWorkflow, setCadAiWorkflow] = useState<CadAiWorkflowState | null>(null);
  const [isSubmittingCadAiWorkflow, setIsSubmittingCadAiWorkflow] = useState(false);
  const [industrySearch, setIndustrySearch] = useState('');
  const [activeIndustryRoot, setActiveIndustryRoot] = useState(industryCatalog[0]?.id || '');
  const [activeIndustryGroup, setActiveIndustryGroup] = useState(industryCatalog[0]?.groups[0]?.id || '');
  const [activeIndustrySegment, setActiveIndustrySegment] = useState(industryCatalog[0]?.groups[0]?.segments[0]?.id || '');
  const [selectedIndustryLeafId, setSelectedIndustryLeafId] = useState(industryCatalog[0]?.groups[0]?.segments[0]?.leaves[0]?.id || '');
  const workspaceStateRef = useRef<WorkspaceState | null>(null);
  const workspaceQueueRef = useRef<WorkspaceMutationQueue | null>(null);
  const promptWriterRef = useRef<DebouncedWorkspaceWriter | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const applyWorkspaceState = (state: WorkspaceState): void => {
    if (!isMountedRef.current) return;
    workspaceStateRef.current = state;
    setWorkspaceState(state);
    setSelectedProjectId(state.selectedProjectId ?? '');
    if (state.activeScenario === 'design' || state.activeScenario === 'propaganda' || state.activeScenario === 'production') {
      setActiveScenario(state.activeScenario);
    }
    if (state.activeWorkflowStage === 'design' || state.activeWorkflowStage === 'propaganda' || state.activeWorkflowStage === 'production') {
      setActiveWorkflowStage(state.activeWorkflowStage);
    }
    setActiveStepIndex(state.activeStepIndex);
    if (state.viewMode === 'cad' || state.viewMode === 'preview3d' || state.viewMode === 'exploded') {
      setViewMode(state.viewMode);
    }
    if (state.sceneMode === 'poster' || state.sceneMode === 'mid' || state.sceneMode === 'detail') {
      setSceneMode(state.sceneMode);
    }
    setSelectedIndustry(state.selectedIndustry || '全部行业');
    setGenerationPrompt(state.generationPrompt);
  };

  const persistWorkspace = async (
    patch: Partial<WorkspaceUpdate>,
  ): Promise<WorkspaceState> => {
    const queue = workspaceQueueRef.current;
    if (!queue) {
      throw new Error('工作区尚未完成数据库加载');
    }
    const persisted = await queue.enqueue(patch);
    workspaceStateRef.current = persisted;
    if (!isMountedRef.current) {
      return persisted;
    }
    setWorkspaceState(persisted);
    return persisted;
  };

  const savePromptDraft = (prompt: string): void => {
    setGenerationPrompt(prompt);
    setIsPromptManuallyEdited(true);
    const writer = promptWriterRef.current;
    if (!writer) {
      setWorkflowNotice('工作区尚未完成数据库加载');
      return;
    }
    void writer.write(prompt)
      .then((persisted) => {
        if (!persisted || !isMountedRef.current) return;
        workspaceStateRef.current = persisted;
        setWorkspaceState(persisted);
      })
      .catch((error: unknown) => {
        if (!isMountedRef.current) return;
        const message = error instanceof Error ? error.message : 'Prompt 保存失败';
        setWorkflowNotice(message);
        setSubmitFeedback({ title: '未保存', detail: message });
      });
  };

  const persistWorkspaceAndApply = async (
    patch: Partial<WorkspaceUpdate>,
    apply: () => void,
  ): Promise<void> => {
    try {
      await persistWorkspace(patch);
      if (!isMountedRef.current) return;
      apply();
    } catch (error) {
      if (!isMountedRef.current) return;
      const message = error instanceof Error ? error.message : '工作区保存失败';
      setWorkflowNotice(message);
      setSubmitFeedback({ title: '未保存', detail: message });
    }
  };

  useEffect(() => {
    let cancelled = false;
    setWorkspaceLoadError(null);
    void Promise.all([
      cocreationHistoryService.listAllHistory(),
      workspaceService.get(),
    ])
      .then(async ([historyResponse, persistedWorkspace]) => {
        if (cancelled) return;
        let effectiveWorkspace = persistedWorkspace;
        const validation = await validateWorkspaceAssetReferences(
          persistedWorkspace,
          (assetId) => assetService.get(assetId),
        );
        if (validation.cleanup) {
          effectiveWorkspace = await workspaceService.update(
            validation.cleanup,
          );
        }
        if (cancelled) return;
        const projects = historyResponse.data.projects ?? [];
        const snapshots = normalizeVersionSnapshots(historyResponse.data.snapshots ?? []);
        setProjectList(projects);
        setVersionSnapshots(snapshots);
        const queue = new WorkspaceMutationQueue(
          effectiveWorkspace,
          (payload) => workspaceService.update(payload),
          () => workspaceService.get(),
        );
        workspaceQueueRef.current = queue;
        promptWriterRef.current?.cancel();
        promptWriterRef.current = createDebouncedWorkspaceWriter(queue, 300);
        applyWorkspaceState(effectiveWorkspace);
        const reference = snapshots.find(
          (snapshot) => snapshot.id === effectiveWorkspace.selectedReferenceVersionId,
        ) ?? null;
        setSelectedReferenceAsset(reference);
        if (validation.importedAsset) {
          setImportedCadAsset(databaseAssetToForgeImport(
            validation.importedAsset,
            validation.previewAsset,
          ));
        } else {
          setImportedCadAsset(null);
        }
        setWorkspaceLoadError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : '工作区数据库加载失败';
        setWorkspaceLoadError(message);
        setWorkflowNotice(message);
        setSubmitFeedback({ title: '工作区加载失败', detail: message });
      });
    return () => {
      cancelled = true;
      promptWriterRef.current?.cancel();
      promptWriterRef.current = null;
      workspaceQueueRef.current = null;
    };
  }, [workspaceLoadAttempt]);

  const currentProject = useMemo(
    () => projectList.find((project) => project.id === selectedProjectId) || null,
    [projectList, selectedProjectId],
  );

  const projectNameCounts = useMemo(() => {
    const counts = new Map<string, number>();
    projectList.forEach((project) => {
      counts.set(project.name, (counts.get(project.name) || 0) + 1);
    });
    return counts;
  }, [projectList]);

  const projectOptionLabel = (project: ProjectRecord): string => {
    const updatedAt = project.updatedAt
      ? new Date(project.updatedAt).toLocaleString('zh-CN', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        })
      : '未知时间';
    const duplicateSuffix = (projectNameCounts.get(project.name) || 0) > 1
      ? ` · ${project.id.slice(-8)}`
      : '';
    const statusSuffix = project.lastStatus ? ` · ${project.lastStatus}` : '';
    return `${project.name}${duplicateSuffix} · ${project.industry}${statusSuffix} · ${updatedAt}`;
  };

  useEffect(() => {
    if (selectedProjectId || projectList.length === 0 || isDraftingNewProject) {
      return;
    }
    const firstProject = projectList[0];
    void persistWorkspaceAndApply(
      {
        selectedProjectId: firstProject.id,
        selectedReferenceVersionId: null,
        selectedReferenceAssetId: null,
        selectedIndustry: firstProject.industry,
        stateData: {
          previewVersionId: null,
          selectedAssetId: null,
        },
      },
      () => {
        setSelectedProjectId(firstProject.id);
        setSelectedReferenceAsset(null);
        setCurrentProjectName(firstProject.name);
        setPendingReferenceVersionId('');
        setProjectDraft((draft) => ({
          ...draft,
          name: firstProject.name,
          industry: firstProject.industry,
          description: firstProject.description,
        }));
      },
    );
  }, [isDraftingNewProject, projectList, selectedProjectId]);

  const upsertProjectRecord = (nextRecord: ProjectRecord): void => {
    setProjectList((prev) => {
      const existingIndex = prev.findIndex((item) => item.id === nextRecord.id);
      if (existingIndex >= 0) {
        const next = [...prev];
        next[existingIndex] = nextRecord;
        return next;
      }
      return [nextRecord, ...prev];
    });
  };

  const getCurrentProjectId = (): string => selectedProjectId || currentProject?.id || buildProjectId(currentProjectName);

  const currentProjectVersions = useMemo(
    () => getVersionsForProject(versionSnapshots, selectedProjectId || currentProject?.id, currentProject?.name || currentProjectName),
    [currentProject?.id, currentProject?.name, currentProjectName, selectedProjectId, versionSnapshots],
  );
  const pendingReferenceVersion = useMemo(
    () => currentProjectVersions.find((version) => version.id === pendingReferenceVersionId) || null,
    [currentProjectVersions, pendingReferenceVersionId],
  );

  const viewModeTabs = useMemo(() => [
    { id: 'preview3d' as const, label: '效果预览' },
    { id: 'cad' as const, label: '方案查看' },
    { id: 'exploded' as const, label: '结构拆解' },
  ], []);

  const industryOptions = useMemo<IndustryFilter[]>(
    () => ['全部行业', ...industryCategories],
    [],
  );
  const allIndustryLeaves = useMemo(() => getAllIndustryLeaves(), []);
  const selectedIndustryLeaf = useMemo(
    () => allIndustryLeaves.find((item) => item.leaf.id === selectedIndustryLeafId) || allIndustryLeaves[0],
    [allIndustryLeaves, selectedIndustryLeafId],
  );
  const buildGenericDescription = (nextName: string): string =>
    `设计一款${nextName}，请围绕主体造型、功能分区、尺寸关系、材质搭配和使用场景输出完整设计方案。`;

  const resolveProjectDescription = (nextIndustry: IndustryFilter, nextName: string): string => {
    const manualDescription = projectDraft.description.trim();
    if (manualDescription) {
      const selectedPrefillDescription = selectedIndustryLeaf?.leaf.prefill.description?.trim() || '';
      const selectedPrefillName = selectedIndustryLeaf?.leaf.prefill.projectName?.trim() || '';
      const manualProjectName = projectDraft.name.trim();
      const isUsingCustomProjectName = Boolean(manualProjectName) && manualProjectName !== selectedPrefillName;

      if (!isDescriptionManuallyEdited && isUsingCustomProjectName && manualDescription === selectedPrefillDescription) {
        return buildGenericDescription(nextName);
      }
      return manualDescription;
    }

    const selectedPrefillName = selectedIndustryLeaf?.leaf.prefill.projectName?.trim() || '';
    const manualProjectName = projectDraft.name.trim();
    const isUsingCustomProjectName = Boolean(manualProjectName) && manualProjectName !== selectedPrefillName;

    if (isUsingCustomProjectName) {
      return buildGenericDescription(nextName);
    }

    return selectedIndustryLeaf?.leaf.prefill.description || `基于${nextName}生成工程图、效果图和场景融合图。`;
  };
  const optimizedGenerationPrompt = useMemo(() => {
    const scenarioInstruction: Record<CoCreationScenario, string> = {
      design: [
        '输出 2D 平面图和设计图，明确尺寸范围、结构约束、材料建议和装配关系。',
        '风格：工业设计渲染，工程图表达，正交视图，结构分区清晰。',
        '质量：主体清晰，边缘锐利，材质真实，光线干净，无水印无乱码。',
      ].join('\n'),
      propaganda: [
        '基于已确认设计图输出精修图和产品融合场景图，突出结构层次、材质和工业应用环境。',
        '风格：商业级产品渲染，棚拍光影，真实材质纹理，适合方案汇报和营销展示。',
        '质量：高分辨率，色彩准确，阴影自然，构图稳定，无多余装饰元素。',
      ].join('\n'),
      production: [
        '基于设计图和解析结果输出可用于 HiCAD/JSCAD 3D 打样与 STEP 交付的结构化建模描述。',
        '风格：参数化建模描述，构件命名规范，装配关系明确，尺寸可调整。',
        '质量：几何准确，拓扑干净，无冗余面，适合后续工程出图和制造交付。',
      ].join('\n'),
    };
    const nextIndustry = projectDraft.industry === '全部行业' ? selectedIndustryLeaf?.pathLabel || selectedIndustry : projectDraft.industry;
    const nextName = projectDraft.name.trim() || selectedIndustryLeaf?.leaf.prefill.projectName || currentProjectName;
    const description = resolveProjectDescription(nextIndustry, nextName);
    const referenceSummary = selectedReferenceAsset
      ? [
          `参考资产：来自版本 ${selectedReferenceAsset.label}（${selectedReferenceAsset.id}）`,
          selectedReferenceAsset.prompt ? `版本 Prompt：${selectedReferenceAsset.prompt}` : '',
          selectedReferenceAsset.resultText ? `版本结果：${selectedReferenceAsset.resultText}` : '',
        ].filter(Boolean).join('\n')
      : '';

    return [
      `项目名称：${nextName}`,
      `所属行业：${nextIndustry}`,
      `当前场景：${scenarioConfigs[activeScenario].label}`,
      importedCadAsset ? `参考资产：${importedCadAsset.filename}，解析摘要：${importedCadAsset.parseMessage}` : '',
      referenceSummary,
      `设计描述：${description}`,
      scenarioInstruction[activeScenario],
      '负面约束：避免模糊、畸变、多余水印、乱码文字、错误标识、杂乱背景、过度艺术化。',
    ].filter(Boolean).join('\n');
  }, [
    activeScenario,
    currentProjectName,
    importedCadAsset,
    projectDraft.description,
    projectDraft.industry,
    projectDraft.name,
    resolveProjectDescription,
    selectedIndustry,
    selectedIndustryLeaf,
    selectedReferenceAsset,
    workflowNotice,
  ]);

  useEffect(() => {
    if (isPromptManuallyEdited || !workspaceStateRef.current) return;
    void persistWorkspace({ generationPrompt: optimizedGenerationPrompt })
      .then(() => setGenerationPrompt(optimizedGenerationPrompt))
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : 'Prompt 保存失败';
        setSubmitFeedback({ title: 'Prompt 未保存', detail: message });
      });
  }, [isPromptManuallyEdited, optimizedGenerationPrompt]);

  const handleOptimizeDescription = async () => {
    const { nextIndustry, nextName, description } = resolveProjectDraft();
    const basePrompt = [
      `项目名称：${nextName}`,
      `所属行业：${nextIndustry}`,
      `设计描述：${description}`,
      `当前场景：${scenarioConfigs[activeScenario].label}`,
      importedCadAsset ? `参考资产：${importedCadAsset.filename}，解析摘要：${importedCadAsset.parseMessage}` : '',
    ].filter(Boolean).join('\n');

    setIsOptimizingDescription(true);
    setSubmitFeedback({
      title: 'AI 正在优化设计描述',
      detail: '系统会基于项目名称、行业、场景和当前描述，生成更适合后续设计方案的文案。',
    });
    try {
      const response = await aggregationWorkbenchService.optimizePrompt({
        prompt: basePrompt,
        model: selectedImageModelId === 'auto' ? null : selectedImageModelId,
      });
      const result = response.data;
      const nextPrompt = (
        result.finalPrompt
        || result.optimizedPrompt
        || optimizedGenerationPrompt
        || description
      ).trim();
      await persistWorkspace({ generationPrompt: nextPrompt });
      setProjectDraft((draft) => ({
        ...draft,
        name: nextName,
        industry: nextIndustry,
        description,
      }));
      setIsPromptManuallyEdited(false);
      setGenerationPrompt(nextPrompt);
      setWorkflowNotice(result.aiOptimized
        ? 'AI 已优化 Prompt，可继续微调后生成方案。'
        : '已使用规则优化 Prompt，可继续微调后生成方案。');
      setSubmitFeedback({
        title: 'Prompt 已优化',
        detail: result.aiOptimized
          ? '已完成 AI 优化，你可以直接执行，也可以继续编辑 Prompt。'
          : '当前 AI 优化模型不可用，已使用规则拼接的 Prompt。',
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '设计描述优化失败，请稍后重试。';
      setSubmitFeedback({
        title: '优化失败',
        detail: message,
      });
      setWorkflowNotice(message);
    } finally {
      setIsOptimizingDescription(false);
    }
  };

  const persistHistoryVersion = async (
    project: ProjectRecord,
    version: VersionSnapshotType,
  ): Promise<void> => {
    await cocreationHistoryService.upsertProjectWithVersion({ project, version });
  };

  // 获取可用生图模型列表
  useEffect(() => {
    let cancelled = false;
    aggregationWorkbenchService.getCatalog().then((res) => {
      if (cancelled) return;
      const catalog = res.data;
      const imageModels = (catalog?.models || []).filter((m) => m.expectedType === 'image');
      const normalizedOptions = imageModels.map((m) => ({
        id: m.id,
        label: m.label,
        provider: m.provider || '',
        connected: m.connected,
        description: m.description,
      }));
      setImageModelOptions(normalizedOptions);
      if (!imageModelPreferenceAppliedRef.current) {
        const preferredNodapi = normalizedOptions.find((m) => m.connected && m.provider === 'nodapi');
        if (preferredNodapi) {
          setSelectedImageModelId(preferredNodapi.id);
        }
        imageModelPreferenceAppliedRef.current = true;
      }
    }).catch(() => {
      if (cancelled) return;
      setImageModelOptions([]);
    });
    return () => { cancelled = true; };
  }, []);
  const currentIndustryRoot = useMemo(
    () => industryCatalog.find((item) => item.id === activeIndustryRoot) || industryCatalog[0],
    [activeIndustryRoot],
  );
  const currentIndustryGroup = useMemo(
    () => currentIndustryRoot.groups.find((item) => item.id === activeIndustryGroup) || currentIndustryRoot.groups[0],
    [activeIndustryGroup, currentIndustryRoot],
  );
  const currentIndustrySegment = useMemo(
    () => currentIndustryGroup.segments.find((item) => item.id === activeIndustrySegment) || currentIndustryGroup.segments[0],
    [activeIndustrySegment, currentIndustryGroup],
  );
  const normalizedIndustrySearch = industrySearch.trim().toLowerCase();
  const searchedIndustryLeaves = useMemo(() => {
    if (!normalizedIndustrySearch) {
      return [];
    }
    return allIndustryLeaves.filter((item) => {
      const haystack = [
        item.pathLabel,
        item.leaf.prefill.projectName,
        item.leaf.prefill.description,
        ...item.leaf.keywords,
      ].join(' ').toLowerCase();
      return haystack.includes(normalizedIndustrySearch);
    }).slice(0, 8);
  }, [allIndustryLeaves, normalizedIndustrySearch]);

  const filteredTemplates = useMemo(
    () =>
      selectedIndustry === '全部行业'
        ? templates
        : templates.filter((item) => item.category === selectedIndustry),
    [selectedIndustry],
  );
  const currentSubmitActionLabel = useMemo(() => {
    if (activeScenario === 'propaganda') {
      return '生成精修图';
    }
    if (activeScenario === 'production') {
      return '生成 3D 打样';
    }
    return '生成方案';
  }, [activeScenario]);

  const latestGeneratedVersion = versionSnapshots.find((version) => Boolean(version.taskId)) || null;
  const latestModelObjects = latestGeneratedVersion?.modelObjects || [];
  const activeExplosionSteps: ForgeCadExplosionStep[] =
    importedCadAsset?.explosionSteps.length
      ? importedCadAsset.explosionSteps
      : latestModelObjects.map((objectItem, index) => ({
          step: index + 1,
          name: objectItem.name,
          offset: [index * explodeDistance, index * Math.max(6, Math.round(explodeDistance / 3)), index * 4],
          description: `${objectItem.name}：体积 ${objectItem.volume || '未返回'}，包围盒 ${objectItem.bbox || '未返回'}。`,
        }));

  useEffect(() => {
    if (!cadAiWorkflow?.taskId || ['completed', 'failed'].includes(cadAiWorkflow.status)) {
      return undefined;
    }

    let disposed = false;
    const timer = window.setInterval(() => {
      void getIndustrialDesignWorkflowTask(cadAiWorkflow.taskId)
        .then(async (task) => {
          if (disposed) {
            return;
          }
          const outputs = (task.outputs || {}) as Record<string, unknown>;
          const progress = typeof task.progress === 'number' ? task.progress : cadAiWorkflow.progress;
          const currentStep = task.currentStep || cadAiWorkflow.currentStep || '工业品设计任务处理中';
          setCadAiWorkflow({
            taskId: task.taskId,
            status: task.status,
            progress,
            currentStep,
            outputs,
            error: task.error,
          });
          setTaskStatus(`工业品设计 ${task.taskId}：${task.status} ${progress}%`);
          setWorkflowNotice(task.error || currentStep);

          if (task.status === 'completed' || task.status === 'failed') {
            if (task.status === 'failed') {
              setSubmitFeedback({
                title: '生成失败',
                detail: task.error || currentStep || '工业品设计任务未能生成有效结果。',
              });
            } else {
              setSubmitFeedback(null);
            }
            const projectId = getCurrentProjectId();
            const snapshot = buildCadAiVersionSnapshot(task, versionSnapshots, projectId, currentProjectName, generationPrompt);
            const nextProjectRecord: ProjectRecord = {
              id: projectId,
              name: currentProjectName,
              industry: selectedIndustry,
              description: projectDraft.description,
              inputMode: projectDraft.inputMode,
              createdAt: currentProject?.createdAt || new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              lastTaskId: task.taskId,
              lastStatus: task.status,
              lastResultText: snapshot.resultText || snapshot.executionSummary || snapshot.note,
              lastImageUrl: snapshot.previewImageUrl || null,
              versionCount: getProjectVersionCount(versionSnapshots, projectId, currentProjectName) + 1,
            };
            await persistHistoryVersion(nextProjectRecord, snapshot);
            if (disposed) return;
            setVersionSnapshots((prev) => [snapshot, ...prev]);
            upsertProjectRecord(nextProjectRecord);
            await persistWorkspaceAndApply(
              {
                selectedProjectId: projectId,
                activeScenario: activeWorkflowStage,
                activeStepIndex: task.status === 'completed'
                  ? activeWorkflowStage === 'design' ? 2 : 1
                  : 0,
              },
              () => {
                setSelectedProjectId(projectId);
                setActiveScenario(activeWorkflowStage);
                setActiveStepIndex(task.status === 'completed' ? activeWorkflowStage === 'design' ? 2 : 1 : 0);
              },
            );
          }
        })
        .catch((error) => {
          if (disposed) {
            return;
          }
          const message = error instanceof Error ? error.message : '工业品设计任务状态查询失败';
          setCadAiWorkflow((current) => current ? { ...current, status: 'failed', error: message } : current);
          setWorkflowNotice(message);
        });
    }, 3000);

    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [cadAiWorkflow?.taskId, cadAiWorkflow?.status, cadAiWorkflow?.progress, cadAiWorkflow?.currentStep, currentProjectName]);

  useEffect(() => {
    return undefined;
  }, []);

  const renderVersionSnapshotCard = (version: VersionSnapshot, variant: 'overview' | 'compact' = 'overview') => {
    const isGenerated = Boolean(version.taskId);

    return (
      <div key={version.id} className={`rounded-2xl border border-slate-200 ${variant === 'compact' ? 'bg-slate-50 p-4' : 'bg-white px-4 py-3'}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <div className="text-sm font-bold text-slate-900">{version.id} · {version.label}</div>
              {version.changeType ? (
                <span className="rounded-full bg-cyan-50 px-2.5 py-1 text-[11px] font-semibold text-cyan-700">
                  {version.changeType}
                </span>
              ) : null}
            </div>
            <div className="mt-1 text-xs leading-6 text-slate-500">{version.executionSummary || version.note}</div>
            {version.resultText ? (
              <div className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs leading-6 text-slate-600">
                {version.resultText}
              </div>
            ) : null}
          </div>
          <div className="shrink-0 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
            {version.status}
          </div>
        </div>

        {isGenerated ? (
          <div className="mt-3 grid gap-2 rounded-xl border border-slate-100 bg-slate-50 p-3 text-xs text-slate-600 sm:grid-cols-2">
            <div className="min-w-0">
              <span className="text-slate-400">来源对象：</span>
              <span className="font-semibold text-slate-700">{version.sourceObject || '当前设计项目'}</span>
            </div>
            <div className="min-w-0">
              <span className="text-slate-400">taskId：</span>
              <span className="font-semibold text-slate-700">{version.taskId}</span>
            </div>
            <div className="min-w-0 sm:col-span-2">
              <span className="text-slate-400">脚本路径：</span>
              <span className="break-all font-semibold text-slate-700">{version.scriptPath}</span>
            </div>
            <div>
              <span className="text-slate-400">执行方式：</span>
              <span className="font-semibold text-slate-700">{version.cliExecuted ? 'Bridge / CLI 已执行' : '仅生成脚本'}</span>
            </div>
            <div>
              <span className="text-slate-400">创建时间：</span>
              <span className="font-semibold text-slate-700">{formatSnapshotTime(version.createdAt)}</span>
            </div>
            <div>
              <span className="text-slate-400">模型对象：</span>
              <span className="font-semibold text-slate-700">{version.modelObjects?.length || 0}</span>
            </div>
            <div>
              <span className="text-slate-400">参数：</span>
              <span className="font-semibold text-slate-700">{version.parameters?.length || 0}</span>
            </div>
            <div>
              <span className="text-slate-400">资产：</span>
              <span className="font-semibold text-slate-700">{version.generatedAssets?.length || 0}</span>
            </div>
            <div>
              <span className="text-slate-400">诊断：</span>
              <span className="font-semibold text-slate-700">{version.diagnostics?.length || 0}</span>
            </div>
          </div>
        ) : (
          <div className="mt-1 text-xs text-slate-500">{version.note}</div>
        )}

        {version.previewImageUrl ? (
          <div className="mt-3 overflow-hidden rounded-xl border border-slate-100 bg-white">
            <PreviewImage src={version.previewImageUrl} alt={version.label} className="h-36 w-full object-contain" />
          </div>
        ) : null}

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => handleOpenRefine(`${version.id} 版本`, 'concept')}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            派生新版本
          </button>
          <button
            type="button"
            onClick={() => handleEditProject(version)}
            className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
          >
            编辑 / 复用
          </button>
          <button
            type="button"
            onClick={() => {
              void persistWorkspaceAndApply(
                {
                  selectedProjectId: version.projectId ?? null,
                  selectedReferenceVersionId: version.id,
                  selectedReferenceAssetId: null,
                },
                () => setSelectedReferenceAsset(version),
              );
            }}
            className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-100"
          >
            设为参考资产
          </button>
          {version.scriptPath ? (
            <button
              type="button"
              onClick={() => {
                setTaskStatus(`正在查看 ${version.id} 的脚本路径与执行摘要`);
                setWorkflowNotice(`脚本路径：${version.scriptPath}；执行摘要：${version.executionSummary || version.note}`);
              }}
              className="rounded-lg bg-slate-100 px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-200"
            >
              查看脚本摘要
            </button>
          ) : null}
          {version.downloadUrl ? (
            <a
              href={version.downloadUrl}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg bg-slate-900 px-3 py-2 text-xs font-semibold text-white transition hover:bg-slate-800"
            >
              下载生成文件
            </a>
          ) : null}
        </div>
      </div>
    );
  };

  const scenePreset = useMemo(() => {
    if (sceneMode === 'mid') {
      return {
        label: '中景结构视角',
        camera: '用于零部件关系审查',
        transform: 'scale(1.02) translateY(2px)',
        glow: 'opacity-90',
      };
    }

    if (sceneMode === 'detail') {
      return {
        label: '细节近景视角',
        camera: '用于孔位与接口细查',
        transform: 'scale(1.08) translateY(-8px)',
        glow: 'opacity-75',
      };
    }

    return {
      label: '海报远景视角',
      camera: '用于客户方案展示',
      transform: 'scale(0.94) translateY(8px)',
      glow: 'opacity-100',
    };
  }, [sceneMode]);

  const resolveIndustrialDesignInput = (): { inputType: IndustrialDesignInputType; workflowAsset: ForgeCadImportAsset | null } => {
    if (projectDraft.inputMode === 'upload' && importedCadAsset) {
      const normalizedExtension = importedCadAsset.extension.toLowerCase();
      const inputType: IndustrialDesignInputType = ['png', 'jpg', 'jpeg', 'webp', 'pdf'].includes(normalizedExtension)
        ? normalizedExtension === 'pdf' ? 'pdf' : 'drawing'
        : 'cad';
      return { inputType, workflowAsset: importedCadAsset };
    }
    return { inputType: 'text', workflowAsset: null };
  };

  const resolveProjectDraft = (): ResolvedProjectDraft => {
    const nextIndustry = projectDraft.industry === '全部行业' ? selectedIndustryLeaf?.pathLabel || '装备制造' : projectDraft.industry;
    const rawNextName = projectDraft.name.trim() || selectedIndustryLeaf?.leaf.prefill.projectName || currentProjectName || `${nextIndustry}智能设计项目`;
    const activeProjectId = selectedProjectId || currentProject?.id || undefined;
    const nextName = ensureUniqueProjectName(rawNextName, projectList, activeProjectId);
    const description = resolveProjectDescription(nextIndustry, nextName);

    return { nextIndustry, nextName, description };
  };

  const handleStartProject = async (): Promise<void> => {
    const { nextIndustry, nextName, description } = resolveProjectDraft();
    const { inputType, workflowAsset } = resolveIndustrialDesignInput();
    const existingProjectId = selectedProjectId || currentProject?.id || null;

    try {
      await persistWorkspace({
        selectedProjectId: existingProjectId,
        activeScenario: 'design',
        activeWorkflowStage: 'design',
        activeStepIndex: 0,
        viewMode: inputType === 'drawing' || inputType === 'cad' || inputType === 'pdf' ? 'cad' : 'preview3d',
        selectedIndustry: nextIndustry,
        generationPrompt,
        stateData: {
          ...workspaceStateRef.current?.stateData,
          projectDraft: { name: nextName, industry: nextIndustry, description },
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '项目工作区保存失败';
      setSubmitFeedback({ title: '项目未创建', detail: message });
      return;
    }

    setIsDraftingNewProject(false);
    setSelectedProjectId(existingProjectId ?? '');
    setCurrentProjectName(nextName);
    setSelectedIndustry(nextIndustry);
    setTaskStatus('项目已创建，待生成设计方案');
    setWorkflowNotice(workflowAsset ? `${workflowAsset.filename} 已作为参考输入。${workflowAsset.parseMessage}` : description);
    setSubmitFeedback(null);
    setIsCreateOpen(false);
    setActiveScenario('design');
    setActiveStepIndex(0);
    setViewMode(inputType === 'drawing' || inputType === 'cad' || inputType === 'pdf' ? 'cad' : 'preview3d');
    setProjectDraft((draft) => ({
      ...draft,
      name: nextName,
      industry: nextIndustry,
      description,
    }));
  };

  const submitIndustrialDesignWorkflow = async (
    source: 'create' | 'toolbar' = 'create',
    stage: WorkflowStage = 'design',
  ) => {
    const { nextIndustry, nextName, description } = resolveProjectDraft();
    const { inputType, workflowAsset } = resolveIndustrialDesignInput();
    const propagandaReferenceValues = stage === 'propaganda'
      ? [
          selectedReferenceAsset?.outputAssetId || '',
          ...(selectedReferenceAsset?.generatedAssets?.map((asset) => asset.assetId || asset.downloadUrl || asset.path || '') || []),
          ...(selectedReferenceAsset?.generatedImageUrls || []),
          selectedReferenceAsset?.previewImageUrl || '',
          selectedReferenceAsset?.downloadUrl || '',
          getCadAiOutputValue(cadAiWorkflow?.outputs || null, ['renderPngAssetId']) || '',
          getCadAiOutputValue(cadAiWorkflow?.outputs || null, ['enhancedImageAssetId']) || '',
          normalizePreviewImageSource(getCadAiOutputValue(cadAiWorkflow?.outputs || null, ['renderPng'])) || '',
          normalizePreviewImageSource(getCadAiOutputValue(cadAiWorkflow?.outputs || null, ['enhancedImage'])) || '',
          latestGeneratedVersion?.outputAssetId || '',
          ...(latestGeneratedVersion?.generatedAssets?.map((asset) => asset.assetId || asset.downloadUrl || asset.path || '') || []),
          ...(latestGeneratedVersion?.generatedImageUrls || []),
          latestGeneratedVersion?.previewImageUrl || '',
          latestGeneratedVersion?.downloadUrl || '',
        ].filter((value): value is string => Boolean(value))
      : [];
    const propagandaReferenceAssetIds = propagandaReferenceValues
      .map((value) => extractAssetIdFromWorkflowReference(value))
      .filter((value): value is string => Boolean(value));
    const propagandaReferenceUrls = propagandaReferenceValues.filter(
      (value) => !extractAssetIdFromWorkflowReference(value) && isExternalImageReferenceUrl(value),
    );
    const assetIds = Array.from(new Set([
      ...(workflowAsset ? [workflowAsset.assetId] : []),
      ...propagandaReferenceAssetIds,
    ]));
    const assetUrls = Array.from(new Set(propagandaReferenceUrls));
    const resolvedGenerationPrompt = generationPrompt.trim() || optimizedGenerationPrompt || description;
    const nextProjectId = selectedProjectId || currentProject?.id || buildProjectId(nextName);
    const persistedProjectId = selectedProjectId || currentProject?.id || null;

    setIsSubmittingCadAiWorkflow(true);
    setIsSubmittingForgeCad(true);
    try {
      await persistWorkspace({
        selectedProjectId: persistedProjectId,
        activeWorkflowStage: stage,
        activeScenario: stage,
        activeStepIndex: 0,
        viewMode: inputType === 'drawing' || inputType === 'cad' || inputType === 'pdf' ? 'cad' : 'preview3d',
        selectedIndustry: nextIndustry,
        generationPrompt: resolvedGenerationPrompt,
        stateData: {
          ...workspaceStateRef.current?.stateData,
          projectDraft: { name: nextName, industry: nextIndustry, description },
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '工作区保存失败';
      setSubmitFeedback({ title: '生成未提交', detail: message });
      setIsSubmittingCadAiWorkflow(false);
      setIsSubmittingForgeCad(false);
      return;
    }
    setActiveWorkflowStage(stage);
    setCurrentProjectName(nextName);
    setSelectedIndustry(nextIndustry);
    setTaskStatus('正在提交工业品设计工作流');
    setWorkflowNotice(
      stage === 'design'
        ? '正在根据文字或参考图片生成 2D 设计图。'
        : stage === 'propaganda'
          ? '正在基于已确认的设计图生成精修图。'
          : '正在基于已确认的设计图生成 3D 打样和 STEP/CAD。',
    );
    setIsCreateOpen(false);
    setActiveScenario(stage);
    setActiveStepIndex(0);
    setViewMode(inputType === 'drawing' || inputType === 'cad' || inputType === 'pdf' ? 'cad' : 'preview3d');
    setSubmitFeedback({
      title:
        source === 'create'
          ? stage === 'design'
            ? '正在生成设计方案'
            : stage === 'propaganda'
              ? '正在生成精修图'
              : '正在生成 3D 打样'
          : stage === 'design'
            ? '正在提交方案生成'
            : stage === 'propaganda'
              ? '正在提交精修图生成'
              : '正在提交 3D 打样生成',
      detail: '系统正在整理设计需求、解析输入资产，并提交统一工业品设计任务。',
    });

    try {
      const task = await createIndustrialDesignWorkflow({
        inputType,
        text: [
          `项目名称：${nextName}`,
          `所属行业：${nextIndustry}`,
          workflowAsset ? `输入资产：${workflowAsset.filename}，解析摘要：${workflowAsset.parseMessage}` : '',
          projectDraft.inputMode === 'upload'
            ? `上传意图：${projectDraft.uploadIntent === 'objectToDrawing' ? '实物转图纸' : '图纸识别'}`
            : '',
          `设计描述：${description}`,
          `生成 Prompt：${resolvedGenerationPrompt}`,
        ].filter(Boolean).join('\n'),
        assetIds,
        assetUrls,
        assetMetas: workflowAsset ? [{
          assetId: workflowAsset.assetId,
          filename: workflowAsset.filename,
          extension: workflowAsset.extension,
          contentType: workflowAsset.contentType,
          sizeBytes: workflowAsset.sizeBytes,
          parseStatus: workflowAsset.parseStatus,
          parseMessage: workflowAsset.parseMessage,
          previewAssetUrl: workflowAsset.previewAssetUrl,
        }] : [],
        projectName: nextName,
        industry: nextIndustry,
        mode: workflowAsset || assetIds.length > 0 || assetUrls.length > 0 ? 'redesign' : 'create',
        context: {
          entryMode: projectDraft.inputMode,
          uploadIntent: projectDraft.inputMode === 'upload' ? projectDraft.uploadIntent : undefined,
        },
        options: {
          generateCad: stage === 'production',
          generateDrawing: stage === 'design',
          generateThreePreview: stage === 'production',
          generateRender: stage === 'propaganda',
          generateExplosion: false,
          enhanceImage: stage === 'propaganda',
          optimizePrompt: true,
          generateTrellisAsset: false,
          imageModel: selectedImageModelId === 'auto' ? null : selectedImageModelId,
          imageProvider: selectedImageModelId === 'auto'
            ? null
            : imageModelOptions.find((m) => m.id === selectedImageModelId)?.provider || null,
        },
      });
      const outputs = (task.outputs || {}) as Record<string, unknown>;
      const progress = typeof task.progress === 'number' ? task.progress : 0;
      const currentStep = task.currentStep || '工业品设计工作流已提交';
      setCadAiWorkflow({
        taskId: task.taskId,
        status: task.status,
        progress,
        currentStep,
        outputs,
        error: task.error,
      });
      setTaskStatus(`工业品设计任务 ${task.taskId} 已提交`);
      setWorkflowNotice(currentStep);
      setSubmitFeedback(
        task.status === 'failed'
          ? {
              title: '生成失败',
              detail: task.error || currentStep || '工业品设计任务未能生成有效结果。',
            }
          : null,
      );
      const terminalStepIndex = task.status === 'completed'
        ? stage === 'design' ? 2 : 1
        : 0;
      await persistWorkspace({
        activeScenario: stage,
        activeWorkflowStage: stage,
        activeStepIndex: terminalStepIndex,
      });
      setActiveScenario(stage);
      setActiveStepIndex(terminalStepIndex);
      if (task.status === 'completed' || task.status === 'failed') {
        const projectId = nextProjectId;
        const snapshot = buildCadAiVersionSnapshot(task, versionSnapshots, projectId, nextName, generationPrompt);
        const nextProjectRecord: ProjectRecord = {
          id: projectId,
          name: nextName,
          industry: nextIndustry,
          description,
          inputMode: projectDraft.inputMode,
          createdAt: currentProject?.createdAt || new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          lastTaskId: task.taskId,
          lastStatus: task.status,
          lastResultText: snapshot.resultText || snapshot.executionSummary || snapshot.note,
          lastImageUrl: snapshot.previewImageUrl || null,
          versionCount: getProjectVersionCount(versionSnapshots, projectId, nextName) + 1,
        };
        await persistHistoryVersion(nextProjectRecord, snapshot);
        await persistWorkspace({
          selectedProjectId: projectId,
        });
        setVersionSnapshots((prev) => [snapshot, ...prev]);
        upsertProjectRecord(nextProjectRecord);
        setSelectedProjectId(projectId);
      }
    } catch (error) {
      const message = resolveIndustrialDesignSubmitError(error);
      setCadAiWorkflow(null);
      setTaskStatus('工业品设计工作流提交失败');
      setWorkflowNotice(message);
      setSubmitFeedback({
        title: '生成失败',
        detail: message,
      });
    } finally {
      setIsSubmittingCadAiWorkflow(false);
      setIsSubmittingForgeCad(false);
    }
  };

  const handleCreateProject = async () => {
    await submitIndustrialDesignWorkflow('create', 'design');
  };

  const handleSelectIndustryRoot = (root: IndustryRoot) => {
    const nextGroup = root.groups[0];
    const nextSegment = nextGroup?.segments[0];
    setActiveIndustryRoot(root.id);
    setActiveIndustryGroup(nextGroup?.id || '');
    setActiveIndustrySegment(nextSegment?.id || '');
  };

  const handleSelectIndustryGroup = (group: IndustryGroup) => {
    const nextSegment = group.segments[0];
    setActiveIndustryGroup(group.id);
    setActiveIndustrySegment(nextSegment?.id || '');
  };

  const handleSelectIndustryLeaf = (root: IndustryRoot, group: IndustryGroup, segment: IndustrySegment, leaf: IndustryLeaf) => {
    const pathLabel = getIndustryLeafPathLabel(root, group, segment, leaf);
    setActiveIndustryRoot(root.id);
    setActiveIndustryGroup(group.id);
    setActiveIndustrySegment(segment.id);
    setSelectedIndustryLeafId(leaf.id);
    setIsDescriptionManuallyEdited(false);
    setProjectDraft((draft) => ({
      ...draft,
      industry: pathLabel,
      name: draft.name.trim() ? draft.name : leaf.prefill.projectName,
      description: draft.description.trim() ? draft.description : leaf.prefill.description,
    }));
  };

  const handleOpenCreateProject = async (): Promise<void> => {
    const defaultIndustry = selectedIndustryLeaf?.pathLabel || selectedIndustry || '装备制造';
    try {
      await persistWorkspace({
        selectedProjectId: null,
        selectedReferenceVersionId: null,
        selectedReferenceAssetId: null,
        selectedIndustry: defaultIndustry,
        generationPrompt: '',
        stateData: {
          importedCadAssetId: null,
          importedCadPreviewAssetId: null,
          selectedAssetId: null,
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '新项目工作区初始化失败';
      setSubmitFeedback({ title: '未打开新项目', detail: message });
      return;
    }
    setIsDraftingNewProject(true);
    setSelectedProjectId('');
    setCurrentProjectName('工业设计总控台');
    setImportedCadAsset(null);
    setSelectedReferenceAsset(null);
    setSubmitFeedback(null);
    setIsPromptManuallyEdited(false);
    setIsDescriptionManuallyEdited(false);
    setProjectDraft((draft) => ({
      ...draft,
      name: '',
      industry: defaultIndustry,
      inputMode: 'prompt',
      description: '',
    }));
    setGenerationPrompt('');
    setIsCreateOpen(true);
  };

  const openCreateWithInputMode = async (inputMode: ProjectInputMode): Promise<void> => {
    try {
      await persistWorkspace({
        selectedProjectId: null,
        stateData: {
          ...workspaceStateRef.current?.stateData,
          inputMode,
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '新项目工作区初始化失败';
      setSubmitFeedback({ title: '未打开新项目', detail: message });
      return;
    }
    setIsDraftingNewProject(true);
    setSelectedProjectId('');
    setIsDescriptionManuallyEdited(false);
    setProjectDraft((draft) => ({ ...draft, inputMode }));
    setSubmitFeedback(null);
    setIsCreateOpen(true);
  };

  const handleOpenRefine = (source: string, type: RefineType) => {
    setRefineAction({ source, type });
    setRefineNote('');
  };

  const handleEditProject = async (version: VersionSnapshot): Promise<void> => {
    const nextName = version.label || currentProjectName;
    const nextDescription = version.resultText || version.note || projectDraft.description;
    const projectId = version.projectId || selectedProjectId || buildProjectId(version.sourceObject || nextName);
    try {
      await persistWorkspace({
        selectedProjectId: projectId,
        selectedIndustry,
        stateData: {
          ...workspaceStateRef.current?.stateData,
          editingVersionId: version.id,
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '项目载入失败';
      setSubmitFeedback({ title: '项目未载入', detail: message });
      return;
    }
    setIsDraftingNewProject(false);
    setSelectedProjectId(projectId);
    setCurrentProjectName(nextName);
    setIsDescriptionManuallyEdited(true);
    setProjectDraft((draft) => ({
      ...draft,
      name: nextName,
      industry: selectedIndustry,
      description: nextDescription,
    }));
    setTaskStatus(`已载入 ${version.label}，可继续修改并重新生成`);
    setWorkflowNotice(version.resultText || version.executionSummary || version.note || '已载入版本信息');
    setIsCreateOpen(true);
  };

  const handleCadImportChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] || null;
    event.target.value = '';
    if (!file) {
      return;
    }

    const extension = file.name.split('.').pop()?.toLowerCase() || '';
    if (!acceptedCadImportExtensions.includes(extension)) {
      setSubmitFeedback({
        title: '导入失败',
        detail: `暂不支持 .${extension || '未知'} 文件，请上传 STEP、STL、DXF、DWG、PDF 或图片图纸。`,
      });
      return;
    }

    if (file.size > maxCadImportSizeBytes) {
      setSubmitFeedback({
        title: '导入失败',
        detail: '文件超过 50MB，请压缩或拆分后再上传。',
      });
      return;
    }

    setIsUploadingCadImport(true);
    setProjectDraft((draft) => ({ ...draft, inputMode: 'upload' }));
    setSubmitFeedback({
      title: '正在导入 CAD 图纸',
      detail: `正在上传 ${file.name}，上传完成后会作为${projectDraft.uploadIntent === 'objectToDrawing' ? '实物转图纸' : 'AI 生成方案'}的参考输入。`,
    });

    try {
      const asset = await uploadForgeCadImportAsset(file);
      await persistWorkspace({
        selectedReferenceAssetId: asset.assetId,
        stateData: {
          ...workspaceStateRef.current?.stateData,
          selectedAssetId: asset.assetId,
          importedCadAssetId: asset.assetId,
          importedCadPreviewAssetId: asset.previewAssetId ?? null,
        },
      });
      setImportedCadAsset(asset);
      setSubmitFeedback({
        title: projectDraft.uploadIntent === 'objectToDrawing' ? '实物照片导入成功' : 'CAD 图纸导入成功',
        detail: `${asset.filename} 已保存，大小 ${formatFileSize(asset.sizeBytes)}。${asset.parseMessage}`,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'CAD 图纸导入失败，请稍后重试。';
      setImportedCadAsset(null);
      setSubmitFeedback({
        title: '导入失败',
        detail: message,
      });
    } finally {
      setIsUploadingCadImport(false);
    }
  };

  const handleSubmitRefine = async () => {
    if (!refineAction) {
      return;
    }

    const typeLabel =
      refineAction.type === 'appearance'
        ? '外观调整'
        : refineAction.type === 'structure'
          ? '结构调整'
          : '方案重做';

    const prompt = [
      `当前项目：${currentProjectName}`,
      `修改对象：${refineAction.source}`,
      `修改类型：${typeLabel}`,
      `修改说明：${refineNote.trim() || '请基于当前版本做优化调整。'}`,
      '请基于现有设计生成新的 ForgeCAD 候选脚本，保留可对比的版本差异。',
    ].join('\n');

    setIsSubmittingForgeCad(true);
    setSubmitFeedback({
      title: `正在提交${typeLabel}`,
      detail: '系统会保留当前版本，并生成新的候选脚本与版本快照，请等待执行完成。',
    });
    setTaskStatus(`正在处理${typeLabel}，等待 ForgeCAD 返回结果`);
    setWorkflowNotice(`已提交${typeLabel}请求，系统正在生成候选脚本并回写版本快照。`);
    let succeeded = false;
    try {
      const result = await generateForgeCadModel({
        prompt,
        exportFormat: 'stl',
        runCli: true,
        maxTokens: 2400,
        action: refineAction.type === 'concept' ? 'derive' : refineAction.type,
        sourceObject: refineAction.source,
      });
      setTaskStatus(`${typeLabel}任务已完成，已生成候选版本 ${result.taskId}`);
      setWorkflowNotice(`${typeLabel}已提交并完成执行：${result.logs}`);
      const persistedSnapshot = buildVersionSnapshot({
        previousSnapshots: versionSnapshots,
        projectId: getCurrentProjectId(),
        projectName: currentProjectName,
        label: `${typeLabel}候选版`,
        status: '待确认',
        notePrefix: `基于${refineAction.source}发起${typeLabel}；任务 ${result.taskId}`,
        result,
        fallbackChangeType: typeLabel,
        fallbackSourceObject: refineAction.source,
        sourceProjectId: getCurrentProjectId(),
        prompt,
      });
      const nextProjectRecord: ProjectRecord = {
        id: getCurrentProjectId(),
        name: currentProjectName,
        industry: selectedIndustry,
        description: projectDraft.description,
        inputMode: projectDraft.inputMode,
        createdAt: currentProject?.createdAt || new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        lastTaskId: result.taskId,
        lastStatus: result.status,
        lastResultText: result.logs || result.snapshot?.executionSummary || typeLabel,
        lastImageUrl: result.downloadUrl || result.snapshot?.downloadUrl || null,
        versionCount: getProjectVersionCount(versionSnapshots, getCurrentProjectId(), currentProjectName) + 1,
      };
      await persistHistoryVersion(nextProjectRecord, persistedSnapshot);
      setVersionSnapshots((prev) => [
        persistedSnapshot,
        ...prev,
      ]);
      upsertProjectRecord(nextProjectRecord);
      await persistWorkspace({
        activeScenario: 'design',
        activeWorkflowStage: 'design',
        activeStepIndex: 2,
      });
      succeeded = true;
      setRefineAction(null);
      setRefineNote('');
      setActiveScenario('design');
      setActiveStepIndex(2);
    } catch (error) {
      const message = error instanceof Error ? error.message : `${typeLabel}提交失败，请稍后重试。`;
      setTaskStatus(`${typeLabel}任务提交失败`);
      setWorkflowNotice(message);
      setSubmitFeedback({
        title: `${typeLabel}失败`,
        detail: message,
      });
    } finally {
      setIsSubmittingForgeCad(false);
      if (succeeded) {
        setSubmitFeedback(null);
      }
    }
  };

  const handleAutoGenerateWorkflow = async () => {
    await submitIndustrialDesignWorkflow('toolbar');
  };

  const currentScenarioConfig = scenarioConfigs[activeScenario];
  const currentStepTitle = getScenarioStepLabel(activeScenario, activeStepIndex);
  const previewVersionImageUrl = selectedPreviewVersion?.previewImageUrl || selectedPreviewVersion?.generatedImageUrls?.[0] || selectedPreviewVersion?.downloadUrl || '';
  const imagePromptMeta = (() => {
    const raw = cadAiWorkflow?.outputs?.imagePromptMeta;
    if (!raw || typeof raw !== 'object') {
      return null;
    }
    return raw as {
      originalPrompt?: string;
      optimizedPrompt?: string;
      finalPrompt?: string;
      aiOptimized?: boolean;
      references?: Array<{
        source?: string;
        category?: string;
        prompt?: string;
        score?: number;
      }>;
    };
  })();

  const renderImagePromptMeta = () => {
    if (!imagePromptMeta) {
      return null;
    }

    const references = Array.isArray(imagePromptMeta.references) ? imagePromptMeta.references : [];

    return (
      <div className="mt-4 rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-4 py-3">
          <div className="text-xs font-bold uppercase tracking-[0.14em] text-slate-400">Prompt Trace</div>
          <div className="mt-1 flex items-center gap-2">
            <div className="text-sm font-semibold text-slate-900">本次生图提示词</div>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
              imagePromptMeta.aiOptimized ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'
            }`}>
              {imagePromptMeta.aiOptimized ? 'Qwen3 优化' : '规则拼接'}
            </span>
          </div>
        </div>
        <div className="space-y-4 px-4 py-4">
          <div>
            <div className="mb-1 text-xs font-semibold text-slate-500">原始 Prompt</div>
            <pre className="overflow-auto rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
              {imagePromptMeta.originalPrompt || '无'}
            </pre>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold text-slate-500">最终 Prompt</div>
            <pre className="overflow-auto rounded-lg bg-blue-50 p-3 text-xs leading-6 text-slate-800 whitespace-pre-wrap">
              {imagePromptMeta.finalPrompt || imagePromptMeta.optimizedPrompt || '无'}
            </pre>
          </div>
          <div>
            <div className="mb-2 text-xs font-semibold text-slate-500">命中的参考提示词</div>
            {references.length > 0 ? (
              <div className="space-y-2">
                {references.map((reference, index) => (
                  <div key={`${reference.source || 'ref'}-${index}`} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-xs font-semibold text-slate-900">
                        {[reference.category, reference.source].filter(Boolean).join(' · ') || `参考 ${index + 1}`}
                      </div>
                      {typeof reference.score === 'number' ? (
                        <span className="text-[10px] font-semibold text-slate-400">score {reference.score}</span>
                      ) : null}
                    </div>
                    <div className="mt-2 text-xs leading-6 text-slate-600">{reference.prompt || '无'}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">未返回参考提示词。</div>
            )}
          </div>
        </div>
      </div>
    );
  };

  const activeCadAiProgress = cadAiWorkflow
    && !['completed', 'failed'].includes(cadAiWorkflow.status)
      ? Math.max(5, Math.min(95, cadAiWorkflow.progress || 0))
      : null;
  const activeTaskStatusLabel = cadAiWorkflow
    ? cadAiWorkflow.status === 'completed'
      ? '已完成'
      : cadAiWorkflow.status === 'failed'
        ? '失败'
        : cadAiWorkflow.status === 'running'
          ? '生成中'
          : '排队中'
    : null;

  const renderWorkspacePreview = () => {
    if (viewMode === 'exploded') {
      return <ExplodedPreview steps={activeExplosionSteps} />;
    }
    if (previewVersionImageUrl && activeScenario !== 'production') {
      return (
        <div className={`relative ${workspacePreviewHeightClass} overflow-auto rounded-lg bg-white p-4`}>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-bold text-blue-600">历史版本预览</span>
            <span className="text-[10px] text-slate-400">
              {selectedPreviewVersion?.label || '已选版本'}
            </span>
          </div>
          <div className={workspacePreviewImageFrameClass}>
            <PreviewImage
              src={previewVersionImageUrl}
              alt={selectedPreviewVersion?.label || '历史版本预览'}
              className={workspacePreviewImageClass}
            />
          </div>
          {selectedPreviewVersion?.prompt ? (
            <div className="mt-3 rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-600">
              {selectedPreviewVersion.prompt}
            </div>
          ) : null}
        </div>
      );
    }
    if (cadAiWorkflow?.status === 'failed') {
      return (
        <div className={`relative flex ${workspacePreviewHeightClass} items-center justify-center overflow-hidden rounded-lg bg-white`}>
          <div className="absolute inset-0 bg-[linear-gradient(#fee2e2_1px,transparent_1px),linear-gradient(90deg,#fee2e2_1px,transparent_1px)] bg-[size:36px_36px]" />
          <div className="relative w-full max-w-xl rounded-2xl border border-red-200 bg-white/95 p-8 text-center shadow-sm">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-red-50 text-red-600">
              <X className="h-7 w-7" />
            </div>
            <div className="mt-5 text-xl font-extrabold text-slate-900">生成失败</div>
            <div className="mt-3 text-sm leading-7 text-slate-600">
              {cadAiWorkflow.error || cadAiWorkflow.currentStep || workflowNotice || '工业品设计任务未能生成有效结果。'}
            </div>
            <div className="mt-5 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-left text-xs leading-6 text-red-700">
              请调整描述、切换模型或检查后端 AI 服务状态后重试。
            </div>
          </div>
        </div>
      );
    }
    if (activeScenario === 'production' && activeStepIndex === 0) {
      return (
        <div className="rounded-lg border border-indigo-100 bg-indigo-50/30 p-4">
          <Suspense
            fallback={(
              <div className="flex h-[380px] items-center justify-center rounded-2xl border border-slate-200 bg-white/80 text-sm font-medium text-slate-500">
                正在加载 3D 打样预览...
              </div>
            )}
          >
            <JscadAgentPreview
              description={[
                projectDraft.description,
                importedCadAsset ? `参考输入：${importedCadAsset.filename}。${importedCadAsset.parseMessage}` : '',
                '请根据当前设计图或实体图片识别结果复现可编辑的工业结构。',
              ].filter(Boolean).join('\n')}
              industry={projectDraft.industry === '全部行业' ? (selectedIndustryLeaf?.pathLabel || '装备制造') : projectDraft.industry}
              projectName={currentProjectName}
            />
          </Suspense>
        </div>
      );
    }
    // 设计/宣发场景优先展示 2D 图片，再回退 CAD/STL 预览
    const renderUrl = normalizePreviewImageSource(
      getCadAiOutputValue(cadAiWorkflow?.outputs || null, ['renderPng', 'enhancedImage']),
    );
    if (activeScenario !== 'production' && renderUrl) {
      const provider = (cadAiWorkflow.outputs.imageProvider as string) || 'AI';
      return (
        <div className={`relative ${workspacePreviewHeightClass} overflow-auto rounded-lg bg-white p-4`}>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-bold text-blue-600">AI 生成设计图</span>
            <span className="text-[10px] text-slate-400">{provider}</span>
          </div>
          {activeCadAiProgress !== null ? (
            <div className="mb-3 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[11px] leading-5 text-blue-700">
              正在继续生成中（{activeCadAiProgress}%）：{cadAiWorkflow.currentStep || 'AI 设计图处理中'}
            </div>
          ) : null}
          <div className={workspacePreviewImageFrameClass}>
            <PreviewImage
              src={renderUrl}
              alt="AI 生成设计方案"
              className={workspacePreviewImageClass}
            />
          </div>
          {cadAiWorkflow.outputs.drawingSummary ? (
            <div className="mt-3 rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-600">
              {cadAiWorkflow.outputs.drawingSummary as string}
            </div>
          ) : null}
          {renderImagePromptMeta()}
        </div>
      );
    }
   if (cadAiWorkflow?.outputs?.drawingSvg) {
      // 移除 SVG 内联 width/height 属性，让 SVG 在容器内自适应缩放
      const rawSvg = cadAiWorkflow.outputs.drawingSvg as string;
      const responsiveSvg = rawSvg.replace(
        /<svg([^>]*)>/,
        (_match: string, attrs: string) => {
          let cleaned = attrs.replace(/\s*width\s*=\s*"[^"]*"/, '');
          cleaned = cleaned.replace(/\s*height\s*=\s*"[^"]*"/, '');
          cleaned += ' style="max-width:100%;height:auto;display:block;margin:0 auto"';
          return `<svg${cleaned}>`;
        }
      );
      return (
        <div className={`relative ${workspacePreviewHeightClass} overflow-auto rounded-lg bg-white p-4`}>
          <div className="mb-2 text-xs font-bold text-blue-600">设计图 · SVG 工程图</div>
          {activeCadAiProgress !== null ? (
            <div className="mb-3 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[11px] leading-5 text-blue-700">
              已先展示本地工程图预览，AI 设计图仍在生成（{activeCadAiProgress}%）：{cadAiWorkflow.currentStep || '任务处理中'}
            </div>
          ) : null}
          <div className="flex items-start justify-center w-full overflow-hidden" dangerouslySetInnerHTML={{ __html: responsiveSvg }} />
          {cadAiWorkflow.outputs.drawingSummary ? (
            <div className="mt-3 rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-600">
              {cadAiWorkflow.outputs.drawingSummary as string}
            </div>
          ) : null}
        </div>
      );
    }
    if (latestGeneratedVersion?.downloadUrl && activeScenario === 'production') {
      return <GeneratedStlPreview downloadUrl={latestGeneratedVersion.downloadUrl} />;
    }
    if (importedCadAsset) {
      return <CadImportPreview asset={importedCadAsset} mode={viewMode} />;
    }
    if (activeScenario === 'production' && activeStepIndex === 1) {
      return <StepProxyPreview asset={emptyStepPreviewAsset} />;
    }
    if (activeCadAiProgress !== null) {
      const progress = activeCadAiProgress;
      return (
        <div className={`relative flex ${workspacePreviewHeightClass} items-center justify-center overflow-hidden rounded-lg bg-white`}>
          <div className="absolute inset-0 bg-[linear-gradient(#e5edf7_1px,transparent_1px),linear-gradient(90deg,#e5edf7_1px,transparent_1px)] bg-[size:36px_36px]" />
          <div className="relative w-full max-w-2xl px-8">
            <div className="rounded-3xl border border-blue-100 bg-white/95 p-8 shadow-sm backdrop-blur-sm">
              <div className="flex items-center gap-4">
                <div className="relative flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-600">
                  <span className="absolute h-14 w-14 animate-ping rounded-2xl bg-blue-100/80" />
                  <Sparkles className="relative h-7 w-7 animate-pulse" />
                </div>
                <div>
                  <div className="text-lg font-extrabold text-slate-900">正在生成，请稍候</div>
                  <div className="mt-1 text-sm text-slate-500">
                    {cadAiWorkflow.currentStep || workflowNotice || '系统正在调用生成链路处理当前方案。'}
                  </div>
                </div>
              </div>
              <div className="mt-6">
                <div className="mb-2 flex items-center justify-between text-xs font-semibold text-slate-500">
                  <span>当前进度</span>
                  <span>{progress}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-blue-500 via-cyan-500 to-sky-400 transition-all duration-500"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>
              <div className="mt-6 grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl bg-slate-50 px-4 py-3">
                  <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">任务状态</div>
                  <div className="mt-2 text-sm font-semibold text-slate-900">处理中</div>
                </div>
                <div className="rounded-xl bg-slate-50 px-4 py-3">
                  <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">当前阶段</div>
                  <div className="mt-2 text-sm font-semibold text-slate-900">{currentStepTitle}</div>
                </div>
                <div className="rounded-xl bg-slate-50 px-4 py-3">
                  <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">任务编号</div>
                  <div className="mt-2 truncate text-sm font-semibold text-slate-900">{cadAiWorkflow.taskId}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className={`relative flex ${workspacePreviewHeightClass} items-center justify-center overflow-hidden rounded-lg bg-[#f8fafc]`}>
        <div className="absolute inset-0 bg-[linear-gradient(#e5edf7_1px,transparent_1px),linear-gradient(90deg,#e5edf7_1px,transparent_1px)] bg-[size:36px_36px]" />
        <div className="relative w-full max-w-md px-8 text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-lg border border-blue-100 bg-blue-50 text-blue-600">
            {activeScenario === 'design' ? <DraftingCompass className="h-7 w-7" /> : <Sparkles className="h-7 w-7" />}
          </div>
          <div className="mt-5 text-lg font-bold text-slate-900">{currentStepTitle || '等待开始'}</div>
          <div className="mt-2 text-sm leading-6 text-slate-500">
            创建项目后可先进入工作台；点击生成方案后，这里会显示图纸或场景融合图结果。
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className={`bg-transparent text-slate-900 ${isStandalone ? 'min-h-screen px-3 pb-6 sm:px-5' : ''}`}>
      <section className="overflow-hidden rounded-[32px] border border-white/70 bg-white/88 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur-xl">
        <header className="flex min-h-16 items-center justify-between border-b border-slate-200/80 bg-[linear-gradient(180deg,#ffffff_0%,#f8fbff_100%)] px-5 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-brand-secondary text-white shadow-[0_12px_28px_rgba(37,99,235,0.22)]">
              <Sparkles className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">CoCreation Agent</div>
              <h1 className="truncate text-lg font-bold text-slate-950">{currentProjectName}</h1>
              <select
                value={selectedProjectId}
                onChange={(event) => {
                  const nextProject = projectList.find((item) => item.id === event.target.value) || null;
                  if (!nextProject) return;
                  void persistWorkspaceAndApply(
                    {
                      selectedProjectId: nextProject.id,
                      selectedReferenceVersionId: null,
                      selectedReferenceAssetId: null,
                      selectedIndustry: nextProject.industry,
                      stateData: {
                        previewVersionId: null,
                        selectedAssetId: null,
                      },
                    },
                    () => {
                      setSelectedProjectId(nextProject.id);
                      setSelectedReferenceAsset(null);
                      setPendingReferenceVersionId('');
                      setSelectedPreviewVersion(null);
                      setCurrentProjectName(nextProject.name);
                      setSelectedIndustry(nextProject.industry);
                      setProjectDraft((draft) => ({
                        ...draft,
                        name: nextProject.name,
                        industry: nextProject.industry,
                        description: nextProject.description,
                      }));
                    },
                  );
                }}
                className="mt-1.5 h-9 min-w-[260px] rounded-xl border border-slate-200 bg-white/90 px-3 text-xs font-medium text-slate-700 outline-none transition focus:border-brand-secondary focus:ring-2 focus:ring-blue-100"
              >
                {projectList.length === 0 ? (
                  <option value="">暂无项目</option>
                ) : (
                  projectList.map((project) => (
                    <option key={project.id} value={project.id}>
                      {projectOptionLabel(project)}
                    </option>
                  ))
                )}
              </select>
            </div>
          </div>

          <div className="relative flex flex-wrap items-center justify-end gap-2">
            {!isStandalone && (
              <>
                <button
                  type="button"
                  onClick={() => window.open('/cocreation.html#history', '_blank')}
                  title="查看生成历史记录"
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white/85 px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:bg-indigo-50/50 hover:shadow-md"
                >
                  <Clock className="h-3.5 w-3.5" />
                  历史记录
                </button>
                <button
                  type="button"
                  onClick={() => window.open('/cocreation.html', '_blank')}
                  title="在新窗口打开独立工作台"
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white/85 px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:bg-indigo-50/50 hover:shadow-md"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  新窗口打开
                </button>
              </>
            )}
            <button
              type="button"
              onClick={handleOpenCreateProject}
              disabled={isSubmittingCadAiWorkflow}
              className="inline-flex items-center gap-2 rounded-2xl bg-brand-secondary px-4 py-2.5 text-sm font-semibold text-white shadow-[0_12px_28px_rgba(37,99,235,0.24)] transition hover:-translate-y-0.5 hover:shadow-[0_16px_36px_rgba(37,99,235,0.3)] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
            >
              <Plus className="h-4 w-4" />
              新建项目
            </button>
            <button
              type="button"
              onClick={() => void submitIndustrialDesignWorkflow('toolbar', activeScenario)}
              disabled={isSubmittingCadAiWorkflow}
              className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white/90 px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300 hover:bg-slate-50 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
            >
              <Workflow className={`h-4 w-4 ${isSubmittingCadAiWorkflow ? 'animate-pulse text-blue-600' : ''}`} />
              {isSubmittingCadAiWorkflow ? `${currentSubmitActionLabel}中...` : currentSubmitActionLabel}
            </button>
          </div>
        </header>

        {workspaceLoadError ? (
          <div className="mx-5 mt-4 flex items-center justify-between gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            <span>工作区资产加载失败：{workspaceLoadError}</span>
            <button
              type="button"
              onClick={() => setWorkspaceLoadAttempt((attempt) => attempt + 1)}
              className="rounded-xl border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold"
            >
              重试加载
            </button>
          </div>
        ) : null}

        {isSubmittingCadAiWorkflow || activeCadAiProgress !== null ? (
          <div className="mx-5 mt-4 rounded-2xl border border-blue-200/80 bg-blue-50/90 px-4 py-3 text-sm text-blue-900 shadow-sm">
            <div className="flex items-start gap-3">
              <span className="mt-1 h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-blue-500" />
              <div className="min-w-0">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-semibold">
                    {submitFeedback?.title || `${currentSubmitActionLabel}已触发`}
                  </div>
                  {activeCadAiProgress !== null ? (
                    <span className="shrink-0 text-xs font-bold text-blue-700">{activeCadAiProgress}%</span>
                  ) : null}
                </div>
                <div className="mt-1 text-xs leading-5 text-blue-700">{workflowNotice || taskStatus}</div>
                {activeCadAiProgress !== null ? (
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-blue-100">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-blue-500 via-cyan-500 to-sky-400 transition-all duration-500"
                      style={{ width: `${activeCadAiProgress}%` }}
                    />
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : cadAiWorkflow?.status === 'failed' ? (
          <div className="mx-5 mt-4 rounded-2xl border border-red-200/80 bg-red-50/90 px-4 py-3 text-sm text-red-900 shadow-sm">
            <div className="flex items-start gap-3">
              <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-red-500" />
              <div className="min-w-0">
                <div className="font-semibold">生成失败</div>
                <div className="mt-1 text-xs leading-5 text-red-700">
                  {cadAiWorkflow.error || cadAiWorkflow.currentStep || workflowNotice || '工业品设计任务未能生成有效结果。'}
                </div>
              </div>
            </div>
          </div>
        ) : null}

        <div className="grid xl:grid-cols-[360px_minmax(0,1fr)]">
          <aside className="surface-scrollbar h-[calc(100vh-170px)] overflow-y-auto overscroll-contain border-r border-slate-200/80 bg-[linear-gradient(180deg,#ffffff_0%,#f8fbff_100%)] p-6 pr-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">Control Rail</div>
                <h2 className="mt-1 text-lg font-extrabold text-slate-950">设计需求</h2>
              </div>
              <DraftingCompass className="h-5 w-5 text-blue-500" />
            </div>

            <div className="mt-5 space-y-4">
              <label className="block">
                <span className="text-xs font-semibold text-slate-500">项目名称</span>
                <input
                  value={projectDraft.name}
                  onChange={(event) => {
                    const nextName = event.target.value;
                    setProjectDraft((draft) => ({
                      ...draft,
                      name: nextName,
                      description: isDescriptionManuallyEdited
                        ? draft.description
                        : buildGenericDescription(nextName.trim() || '当前项目'),
                    }));
                  }}
                  className="mt-2 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-blue-500"
                  placeholder="伺服联动底座结构设计"
                />
              </label>

              <label className="block">
                <span className="text-xs font-semibold text-slate-500">所属行业</span>
                <select
                  value={projectDraft.industry}
                  onChange={(event) => {
                    const newIndustry = event.target.value as IndustryFilter;
                    // 同步切换叶子节点：找到新行业下的第一个叶子并更新预填值
                    const firstLeaf = allIndustryLeaves.find(
                      (item) => item.root.label === newIndustry,
                    );
                    if (firstLeaf) {
                      setActiveIndustryRoot(firstLeaf.root.id);
                      setActiveIndustryGroup(firstLeaf.group.id);
                      setActiveIndustrySegment(firstLeaf.segment.id);
                      setSelectedIndustryLeafId(firstLeaf.leaf.id);
                    }
                    const shouldAutofillDescription = !isDescriptionManuallyEdited;
                    setProjectDraft((draft) => ({
                      ...draft,
                      industry: newIndustry,
                      name: draft.name.trim() ? draft.name : (firstLeaf?.leaf.prefill.projectName || draft.name),
                      description: shouldAutofillDescription
                        ? (firstLeaf?.leaf.prefill.description || '')
                        : draft.description,
                    }));
                  }}
                  className="mt-2 h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                >
                  {industryOptions.filter((option) => option !== '全部行业').map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </label>

              <div>
                <div className="text-xs font-semibold text-slate-500">输入方式</div>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {inputModes.map((mode) => {
                    const Icon = mode.icon;
                    const active = projectDraft.inputMode === mode.id;
                    return (
                      <button
                        key={`sidebar-${mode.id}`}
                        type="button"
                        onClick={() => setProjectDraft((draft) => ({ ...draft, inputMode: mode.id }))}
                        className={`flex h-10 items-center justify-center gap-2 rounded-lg border text-xs font-bold transition ${
                          active
                            ? 'border-blue-600 bg-gradient-to-r from-blue-50 to-indigo-50 text-blue-700 shadow-sm'
                            : 'border-slate-200 bg-white text-slate-600 shadow-sm hover:-translate-y-0.5 hover:bg-slate-50 hover:shadow-md'
                        }`}
                      >
                        <Icon className="h-4 w-4" />
                        {mode.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <label className="block">
                <span className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold text-slate-500">设计描述</span>
                  <button
                    type="button"
                    onClick={() => void handleOptimizeDescription()}
                    disabled={isOptimizingDescription || isSubmittingCadAiWorkflow || isSubmittingForgeCad}
                    className="text-xs font-bold text-blue-600 transition hover:text-blue-700 disabled:cursor-not-allowed disabled:text-slate-400"
                  >
                    {isOptimizingDescription ? '优化中...' : 'AI 优化'}
                  </button>
                </span>
                <textarea
                  value={projectDraft.description}
                  onChange={(event) => {
                    setIsDescriptionManuallyEdited(true);
                    setProjectDraft((draft) => ({ ...draft, description: event.target.value, inputMode: 'prompt' }));
                  }}
                  className="mt-2 min-h-[180px] w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm leading-6 text-slate-900 shadow-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                  placeholder="描述产品用途、尺寸范围、结构约束、材料、装配关系，也可以说明参考图中要保留或修改的部分。"
                />
              </label>

              <label className="block">
                <span className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold text-slate-500">优化后 Prompt</span>
                  <button
                    type="button"
                    onClick={() => {
                      void persistWorkspaceAndApply(
                        { generationPrompt: optimizedGenerationPrompt },
                        () => {
                          setIsPromptManuallyEdited(false);
                          setGenerationPrompt(optimizedGenerationPrompt);
                        },
                      );
                    }}
                    className="text-xs font-bold text-blue-600 transition hover:text-blue-700"
                  >
                    恢复自动优化
                  </button>
                </span>
                <textarea
                  value={generationPrompt}
                  onChange={(event) => {
                    savePromptDraft(event.target.value);
                  }}
                  className="mt-2 min-h-[130px] w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm leading-6 text-slate-900 shadow-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                  placeholder="AI 优化后的生图 Prompt 会直接展示在这里；你可以继续编辑，然后直接按当前 Prompt 生成。"
                />
                <span className="mt-1 block text-[11px] leading-5 text-slate-400">
                  这里的内容就是提交给生图模型执行的最终 Prompt；手动编辑后会优先使用你的版本。
                </span>
              </label>

              <label className="block">
                <span className="text-xs font-semibold text-slate-500">生图模型</span>
                <select
                  value={selectedImageModelId}
                  onChange={(e) => setSelectedImageModelId(e.target.value)}
                  className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-medium text-slate-900 shadow-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                >
                  <option value="auto">自动选择（API 优先）</option>
                  {imageModelOptions.map((m) => (
                    <option key={m.id} value={m.id} disabled={!m.connected}>
                      {m.label}{m.connected ? '' : '（未连通）'}
                    </option>
                  ))}
                </select>
                <span className="mt-1 block text-[11px] leading-5 text-slate-400">
                  选择生图模型，自动模式下系统会优先选择可用的 API 服务，不再使用本地模型。
                </span>
                </label>

              <div className="rounded-2xl border border-dashed border-slate-300 bg-gradient-to-br from-slate-50 to-white p-3 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-bold text-slate-900">参考资产</div>
                    <div className="mt-1 text-xs leading-5 text-slate-500">支持实体图片、设计图、STEP、STL、DXF、DWG、PDF，也可以直接选当前项目版本。</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setPendingReferenceVersionId(selectedReferenceAsset?.id || currentProjectVersions[0]?.id || '');
                        setIsVersionPickerOpen(true);
                      }}
                      className="inline-flex h-12 shrink-0 items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:bg-indigo-50/50 hover:shadow-md"
                    >
                      <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-slate-100 text-slate-600">
                        <FileText className="h-4 w-4" />
                      </span>
                      选项目版本
                    </button>
                    <label className={`inline-flex h-12 shrink-0 cursor-pointer items-center gap-2 rounded-2xl px-4 text-sm font-semibold transition ${
                      isUploadingCadImport || isSubmittingForgeCad
                        ? 'bg-slate-200 text-slate-400'
                        : 'bg-slate-900 text-white shadow-[0_10px_24px_rgba(15,23,42,0.18)] hover:-translate-y-0.5 hover:bg-slate-800 hover:shadow-[0_14px_30px_rgba(15,23,42,0.22)]'
                    }`}>
                      <span className={`flex h-8 w-8 items-center justify-center rounded-xl ${
                        isUploadingCadImport || isSubmittingForgeCad ? 'bg-slate-300/70' : 'bg-white/10'
                      }`}>
                        <UploadCloud className="h-4 w-4" />
                      </span>
                      {isUploadingCadImport ? '上传中' : '上传'}
                      <input
                        type="file"
                        accept=".step,.stp,.stl,.dxf,.dwg,.pdf,.png,.jpg,.jpeg,.webp"
                        onChange={handleCadImportChange}
                        disabled={isUploadingCadImport || isSubmittingForgeCad}
                        className="hidden"
                      />
                    </label>
                  </div>
                </div>
                {selectedReferenceAsset ? (
                  <div className="mt-3 rounded-2xl border border-emerald-100 bg-gradient-to-br from-emerald-50 to-white p-4 shadow-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-slate-900">{selectedReferenceAsset.label}</div>
                        <div className="mt-1 text-xs text-slate-500">
                          {selectedReferenceAsset.id}
                          {selectedReferenceAsset.changeType ? ` · ${selectedReferenceAsset.changeType}` : ''}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          void persistWorkspaceAndApply(
                            {
                              selectedReferenceVersionId: null,
                              selectedReferenceAssetId: null,
                            },
                            () => setSelectedReferenceAsset(null),
                          );
                        }}
                        className="rounded-lg px-2.5 py-1.5 text-[11px] font-semibold text-slate-500 transition hover:bg-white hover:text-slate-900"
                      >
                        清除
                      </button>
                    </div>
                    {selectedReferenceAsset.prompt ? (
                      <div className="mt-3 rounded-xl bg-white/80 px-3 py-3 text-xs leading-5 text-slate-600 ring-1 ring-emerald-100">
                        {selectedReferenceAsset.prompt}
                      </div>
                    ) : null}
                  </div>
                ) : importedCadAsset ? (
                  <div className="mt-3 rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50 to-white p-4 shadow-sm">
                    <div className="truncate text-sm font-semibold text-slate-900">{importedCadAsset.filename}</div>
                    <div className="mt-1 text-xs text-slate-500">{importedCadAsset.extension.toUpperCase()} · {formatFileSize(importedCadAsset.sizeBytes)}</div>
                  </div>
                ) : (
                  <div className="mt-3 rounded-2xl border border-dashed border-blue-200 bg-blue-50/40 px-4 py-4 text-xs font-semibold leading-6 text-blue-700">
                    上传实体图片或设计图，或从项目版本里选一个作为参考资产
                  </div>
                )}
              </div>

              <div>
                <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">场景</div>
                <div className="mt-2 space-y-2">
                  {scenarioTabs.map((scenario) => {
                    const isActive = activeScenario === scenario.id;
                    return (
                      <button
                        key={scenario.id}
                        type="button"
                        onClick={() => {
                          void persistWorkspaceAndApply(
                            { activeScenario: scenario.id, activeStepIndex: 0 },
                            () => {
                              setActiveScenario(scenario.id);
                              setActiveStepIndex(0);
                            },
                          );
                        }}
                        className={`flex w-full items-start justify-between rounded-2xl px-3.5 py-3.5 text-left transition ${
                          isActive ? 'bg-gradient-to-r from-blue-50 to-indigo-50 text-blue-700 ring-1 ring-blue-100' : 'text-slate-600 hover:bg-slate-50'
                        }`}
                      >
                        <span>
                          <span className="block text-sm font-extrabold">{scenario.label}</span>
                          <span className="mt-1 block text-xs leading-5 text-slate-500">{scenario.description}</span>
                        </span>
                        <Square className={`mt-1 h-2.5 w-2.5 ${isActive ? 'fill-blue-600 text-blue-600' : 'text-slate-300'}`} />
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={handleStartProject}
                  disabled={isSubmittingForgeCad || isSubmittingCadAiWorkflow}
                  className="rounded-xl border border-slate-200 px-3 py-2.5 text-sm font-bold text-slate-700 shadow-sm transition hover:-translate-y-0.5 hover:bg-slate-50 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
                >
                  创建项目
                </button>
                <button
                  type="button"
                  onClick={() => void submitIndustrialDesignWorkflow('toolbar', activeScenario)}
                  disabled={isSubmittingForgeCad || isSubmittingCadAiWorkflow}
                  className="rounded-xl bg-blue-600 px-3 py-2.5 text-sm font-bold text-white shadow-[0_12px_28px_rgba(37,99,235,0.24)] transition hover:-translate-y-0.5 hover:bg-blue-700 hover:shadow-[0_16px_34px_rgba(37,99,235,0.3)] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
                >
                  {isSubmittingForgeCad || isSubmittingCadAiWorkflow ? '生成中...' : '生成方案'}
                </button>
              </div>

              {submitFeedback ? (
                <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-800">
                  <div className="font-bold">{submitFeedback.title}</div>
                  <div className="mt-1">{submitFeedback.detail}</div>
                </div>
              ) : null}

              {/* 生成历史面板 */}
              {currentProjectVersions.length > 0 ? (
                <div className="mt-6 border-t border-slate-200 pt-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">生成历史</div>
                      <h3 className="mt-1 text-sm font-extrabold text-slate-900">历史记录</h3>
                    </div>
                    <Activity className="h-4 w-4 text-slate-400" />
                  </div>
                  <div className="surface-scrollbar mt-3 max-h-[280px] space-y-2 overflow-y-auto pr-2">
                    {currentProjectVersions.slice(0, 10).map((version) => (
                      <div
                        key={version.id}
                        className="cursor-pointer rounded-lg border border-slate-100 bg-slate-50 p-2.5 transition hover:border-blue-200 hover:bg-blue-50"
                        onClick={() => {
                          void persistWorkspaceAndApply(
                            {
                              activeScenario: 'design',
                              activeWorkflowStage: 'design',
                              activeStepIndex: 2,
                              viewMode: 'preview3d',
                              stateData: {
                                ...workspaceStateRef.current?.stateData,
                                previewVersionId: version.id,
                              },
                            },
                            () => {
                              setSelectedPreviewVersion(version);
                              setActiveScenario('design');
                              setActiveStepIndex(2);
                              setViewMode('preview3d');
                              setWorkflowNotice(version.resultText || version.executionSummary || version.note || '已切换到历史版本预览');
                            },
                          );
                        }}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-xs font-semibold text-slate-900">
                              {version.label}
                            </div>
                            <div className="mt-0.5 truncate text-[10px] text-slate-500">
                              {version.executionSummary || version.note}
                            </div>
                          </div>
                          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                            version.status === '已完成' ? 'bg-green-100 text-green-700' :
                            version.status === 'failed' ? 'bg-red-100 text-red-700' :
                            'bg-blue-100 text-blue-700'
                          }`}>
                            {version.status === 'failed' ? '失败' : version.status}
                          </span>
                        </div>
                        <div className="mt-1.5 flex items-center gap-2 text-[10px] text-slate-400">
                          <span>{formatSnapshotTime(version.createdAt)}</span>
                          {version.changeType ? (
                            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-500">
                              {version.changeType}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : selectedProjectId || currentProjectName ? (
                <div className="mt-6 border-t border-slate-200 pt-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">生成历史</div>
                      <h3 className="mt-1 text-sm font-extrabold text-slate-900">历史记录</h3>
                    </div>
                    <Activity className="h-4 w-4 text-slate-400" />
                  </div>
                  <div className="surface-scrollbar mt-3 max-h-[280px] overflow-y-auto pr-2">
                    <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-xs leading-6 text-slate-500">
                      当前项目还没有历史版本，生成一次方案后会显示在这里。
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          </aside>

          <main className="min-w-0 bg-[linear-gradient(180deg,#f8fafc_0%,#eef4fb_100%)] p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-4 border-b border-slate-200/80 pb-3">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-blue-600">{currentScenarioConfig.description}</div>
                <h2 className="mt-2 text-2xl font-extrabold text-slate-950">{currentStepTitle}</h2>
              </div>
              <div className="rounded-full border border-slate-200 bg-white/90 px-3 py-1.5 text-xs font-semibold text-slate-500 shadow-sm">
                当前场景：{currentScenarioConfig.label}
              </div>
            </div>
            {cadAiWorkflow ? (
              <div className="mb-4 rounded-[26px] border border-slate-200/80 bg-white/94 p-4 shadow-[0_16px_50px_rgba(15,23,42,0.06)]">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">当前任务</div>
                    <div className="mt-1 flex items-center gap-2">
                      <div className="text-sm font-extrabold text-slate-950">{activeTaskStatusLabel || '处理中'}</div>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                        cadAiWorkflow.status === 'completed'
                          ? 'bg-emerald-100 text-emerald-700'
                          : cadAiWorkflow.status === 'failed'
                            ? 'bg-rose-100 text-rose-700'
                            : 'bg-blue-100 text-blue-700'
                      }`}>
                        {cadAiWorkflow.status}
                      </span>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">Task ID</div>
                    <div className="mt-1 text-xs font-mono text-slate-600">{cadAiWorkflow.taskId}</div>
                  </div>
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_120px]">
                  <div className="rounded-xl bg-slate-50 px-3 py-3 text-xs leading-6 text-slate-700">
                    {cadAiWorkflow.error || cadAiWorkflow.currentStep || workflowNotice || '任务处理中'}
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-center">
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">进度</div>
                    <div className="mt-2 text-2xl font-extrabold text-slate-950">
                      {typeof cadAiWorkflow.progress === 'number' ? `${cadAiWorkflow.progress}%` : '--'}
                    </div>
                  </div>
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      cadAiWorkflow.status === 'failed'
                        ? 'bg-rose-500'
                        : cadAiWorkflow.status === 'completed'
                          ? 'bg-emerald-500'
                          : 'bg-gradient-to-r from-blue-500 via-cyan-500 to-sky-400'
                    }`}
                    style={{ width: `${Math.max(5, Math.min(100, cadAiWorkflow.progress || 0))}%` }}
                  />
                </div>
              </div>
            ) : null}
            <div className="overflow-hidden rounded-[30px] border border-slate-200/80 bg-white/94 shadow-[0_20px_70px_rgba(15,23,42,0.07)]">
              <div className="flex items-center justify-between border-b border-slate-200/80 bg-[linear-gradient(180deg,#ffffff_0%,#f8fbff_100%)] px-5 py-3">
                <div>
                  <span className="text-xs font-bold tracking-[0.18em] text-slate-400">WORKSPACE VIEWER</span>
                  <div className="mt-1 text-sm font-semibold text-slate-900">主预览舞台</div>
                </div>
                <div className="flex items-center gap-1">
                  {viewModeTabs.map((tab, index) => (
                    <React.Fragment key={tab.id}>
                      {index > 0 && (
                        <span className="text-xs text-slate-300">/</span>
                      )}
                      <button
                        type="button"
                        onClick={() => {
                          void persistWorkspaceAndApply(
                            { viewMode: tab.id },
                            () => setViewMode(tab.id),
                          );
                        }}
                        className={`text-xs font-semibold transition hover:text-blue-800 ${
                          viewMode === tab.id ? 'text-blue-600' : 'text-slate-400 hover:text-slate-600'
                        }`}
                      >
                        {tab.label}
                      </button>
                    </React.Fragment>
                  ))}
                </div>
              </div>
              <div className="min-h-[620px] p-3 sm:p-4">{renderWorkspacePreview()}</div>
            </div>
          </main>

        </div>
      </section>

      {isVersionPickerOpen ? (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/55 px-4 backdrop-blur-sm">
          <section className="w-full max-w-4xl overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-[0_30px_100px_rgba(15,23,42,0.22)]">
            <div className="flex items-center justify-between border-b border-slate-200 bg-gradient-to-r from-slate-50 to-white px-6 py-4">
              <div>
                <h2 className="text-xl font-bold text-slate-900">选择项目版本</h2>
                <div className="mt-1 text-sm text-slate-500">当前项目：{currentProject?.name || currentProjectName}</div>
              </div>
              <button
                type="button"
                onClick={() => setIsVersionPickerOpen(false)}
                className="flex h-10 w-10 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
                aria-label="关闭"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="max-h-[70vh] overflow-y-auto bg-slate-50/60 p-6">
              {currentProjectVersions.length > 0 ? (
                <div className="grid gap-3">
                  {currentProjectVersions.map((version) => {
                    const active = pendingReferenceVersionId === version.id;
                    return (
                      <button
                        key={version.id}
                        type="button"
                        onClick={() => setPendingReferenceVersionId(version.id)}
                        className={`rounded-3xl border p-4 text-left shadow-sm transition ${
                          active ? 'border-emerald-300 bg-gradient-to-br from-emerald-50 to-white ring-2 ring-emerald-100' : 'border-slate-200 bg-white hover:-translate-y-0.5 hover:border-emerald-200 hover:shadow-md'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="truncate text-sm font-bold text-slate-900">{version.label}</span>
                              {version.changeType ? (
                                <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-600">
                                  {version.changeType}
                                </span>
                              ) : null}
                            </div>
                            <div className="mt-1 text-xs text-slate-500">{version.resultText || version.executionSummary || version.note}</div>
                            <div className="mt-1 text-[11px] text-slate-400">{formatSnapshotTime(version.createdAt)}</div>
                          </div>
                          {version.previewImageUrl ? (
                            <div className="ml-4 h-16 w-24 overflow-hidden rounded-2xl border border-slate-100 bg-white">
                              <PreviewImage src={version.previewImageUrl} alt={version.label} className="h-full w-full object-contain" />
                            </div>
                          ) : null}
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-3xl border border-dashed border-slate-200 bg-white px-4 py-10 text-center text-sm text-slate-500 shadow-sm">
                  当前项目还没有可选版本，先生成一次方案再来选择。
                </div>
              )}
            </div>
            <div className="flex items-center justify-between border-t border-slate-200 bg-white px-6 py-4">
              <div className="text-sm text-slate-500">
                {pendingReferenceVersion ? `已选中：${pendingReferenceVersion.label} · ${pendingReferenceVersion.id}` : '请选择当前项目的一个版本后确认引用。'}
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setIsVersionPickerOpen(false)}
                  className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 transition hover:bg-slate-50"
                >
                  取消
                </button>
                <button
                  type="button"
                  disabled={!pendingReferenceVersion}
                  onClick={() => {
                    if (!pendingReferenceVersion) {
                      return;
                    }
                    void persistWorkspaceAndApply(
                      {
                        selectedProjectId: pendingReferenceVersion.projectId ?? null,
                        selectedReferenceVersionId: pendingReferenceVersion.id,
                        selectedReferenceAssetId: null,
                      },
                      () => {
                        setSelectedReferenceAsset(pendingReferenceVersion);
                        setIsVersionPickerOpen(false);
                      },
                    );
                  }}
                  className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  确认引用
                </button>
              </div>
            </div>
          </section>
        </div>
      ) : null}

      {isCreateOpen ? (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/55 px-4 backdrop-blur-sm">
          <section className="w-full max-w-2xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_30px_100px_rgba(15,23,42,0.22)]">
            <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
              <div>
                <h2 className="text-xl font-bold text-slate-900">新建项目</h2>
                <div className="mt-1 text-sm text-slate-500">填写设计需求后进入工作台，也可以直接生成方案。</div>
              </div>
              <button
                type="button"
                onClick={() => {
                  setIsDraftingNewProject(false);
                  setIsCreateOpen(false);
                }}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
                aria-label="关闭"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-5 p-6">
              {submitFeedback ? (
                <div className={`rounded-xl border px-4 py-3 ${isSubmittingForgeCad ? 'border-cyan-200 bg-cyan-50' : 'border-slate-200 bg-slate-50'}`}>
                  <div className="flex items-center gap-3">
                    <div className={`h-2.5 w-2.5 rounded-full ${isSubmittingForgeCad ? 'animate-pulse bg-cyan-500' : 'bg-slate-400'}`} />
                    <div className="text-sm font-semibold text-slate-900">{submitFeedback.title}</div>
                  </div>
                  <div className="mt-2 text-sm leading-6 text-slate-600">{submitFeedback.detail}</div>
                </div>
              ) : null}

              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block">
                  <span className="text-sm font-semibold text-slate-700">项目名称</span>
                  <input
                    value={projectDraft.name}
                    onChange={(event) => {
                      const nextName = event.target.value;
                      setProjectDraft((draft) => ({
                        ...draft,
                        name: nextName,
                        description: isDescriptionManuallyEdited
                          ? draft.description
                          : buildGenericDescription(nextName.trim() || '当前项目'),
                      }));
                    }}
                    className="mt-2 h-11 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-cyan-500"
                    placeholder="例如：伺服联动底座结构设计"
                  />
                </label>

                <label className="block">
                  <span className="text-sm font-semibold text-slate-700">所属行业</span>
                  <select
                    value={projectDraft.industry}
                    onChange={(event) => {
                      const newIndustry = event.target.value as IndustryFilter;
                      const firstLeaf = allIndustryLeaves.find(
                        (item) => item.root.label === newIndustry,
                      );
                      if (firstLeaf) {
                        setActiveIndustryRoot(firstLeaf.root.id);
                        setActiveIndustryGroup(firstLeaf.group.id);
                        setActiveIndustrySegment(firstLeaf.segment.id);
                        setSelectedIndustryLeafId(firstLeaf.leaf.id);
                      }
                      const shouldAutofillDescription = !isDescriptionManuallyEdited;
                      setProjectDraft((draft) => ({
                        ...draft,
                        industry: newIndustry,
                        name: draft.name.trim() ? draft.name : (firstLeaf?.leaf.prefill.projectName || draft.name),
                        description: shouldAutofillDescription
                          ? (firstLeaf?.leaf.prefill.description || '')
                          : draft.description,
                      }));
                    }}
                    className="mt-2 h-11 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-cyan-500"
                  >
                    {industryOptions.filter((option) => option !== '全部行业').map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div>
                <div className="text-sm font-semibold text-slate-700">输入方式</div>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {inputModes.map((mode) => {
                    const Icon = mode.icon;
                    const active = projectDraft.inputMode === mode.id;
                    return (
                      <button
                        key={mode.id}
                        type="button"
                        onClick={() => setProjectDraft((draft) => ({ ...draft, inputMode: mode.id }))}
                        className={`flex h-11 items-center justify-center gap-2 rounded-lg border text-sm font-semibold transition ${
                          active
                            ? 'border-brand-secondary bg-brand-secondary/5 text-brand-secondary'
                            : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'
                        }`}
                      >
                        <Icon className="h-4 w-4" />
                        {mode.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {projectDraft.inputMode === 'prompt' ? (
                <div className="space-y-4">
                  <label className="block">
                    <span className="flex items-center justify-between gap-3">
                      <span className="text-sm font-semibold text-slate-700">设计描述</span>
                      <button
                        type="button"
                        onClick={() => void handleOptimizeDescription()}
                        disabled={isOptimizingDescription || isSubmittingCadAiWorkflow || isSubmittingForgeCad}
                        className="text-xs font-bold text-brand-secondary transition hover:opacity-80 disabled:cursor-not-allowed disabled:text-slate-400"
                      >
                        {isOptimizingDescription ? '优化中...' : 'AI 优化'}
                      </button>
                    </span>
                    <textarea
                      value={projectDraft.description}
                      onChange={(event) => {
                        setIsDescriptionManuallyEdited(true);
                        setProjectDraft((draft) => ({ ...draft, description: event.target.value }));
                      }}
                      className="mt-2 min-h-[150px] w-full resize-none rounded-lg border border-slate-200 bg-white px-3 py-3 text-sm leading-6 text-slate-900 outline-none transition focus:border-cyan-500"
                      placeholder="描述产品用途、结构约束、尺寸范围、材料偏好、装配关系或参考对象。"
                    />
                  </label>

                  <label className="block">
                    <span className="flex items-center justify-between gap-3">
                      <span className="text-sm font-semibold text-slate-700">优化后 Prompt</span>
                      <button
                        type="button"
                        onClick={() => {
                          void persistWorkspaceAndApply(
                            { generationPrompt: optimizedGenerationPrompt },
                            () => {
                              setIsPromptManuallyEdited(false);
                              setGenerationPrompt(optimizedGenerationPrompt);
                            },
                          );
                        }}
                        className="text-xs font-bold text-brand-secondary transition hover:opacity-80"
                      >
                        恢复自动优化
                      </button>
                    </span>
                    <textarea
                      value={generationPrompt}
                      onChange={(event) => {
                        savePromptDraft(event.target.value);
                      }}
                      className="mt-2 min-h-[140px] w-full resize-none rounded-lg border border-slate-200 bg-cyan-50/40 px-3 py-3 text-sm leading-6 text-slate-900 outline-none transition focus:border-cyan-500"
                      placeholder="AI 优化后的 Prompt 会显示在这里；你可以二次编辑，也可以直接执行。"
                    />
                    <span className="mt-1 block text-[11px] leading-5 text-slate-400">
                      点击“生成设计方案”时，会直接使用这里的 Prompt。
                    </span>
                  </label>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-slate-900">上传图纸 / CAD / 草图</div>
                      <div className="mt-1 text-xs text-slate-500">支持 STEP、STL、DXF、DWG、PDF 和图片，单文件不超过 50MB。</div>
                    </div>
                    <label className={`inline-flex cursor-pointer items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${
                      isUploadingCadImport || isSubmittingForgeCad
                        ? 'bg-slate-200 text-slate-400'
                        : 'bg-slate-900 text-white hover:bg-slate-800'
                    }`}>
                      <UploadCloud className="h-4 w-4" />
                      {isUploadingCadImport ? '上传中...' : '选择文件'}
                      <input
                        type="file"
                        accept=".step,.stp,.stl,.dxf,.dwg,.pdf,.png,.jpg,.jpeg,.webp"
                        onChange={handleCadImportChange}
                        disabled={isUploadingCadImport || isSubmittingForgeCad}
                        className="hidden"
                      />
                    </label>
                  </div>
                  {importedCadAsset ? (
                    <div className="mt-4 rounded-lg border border-emerald-100 bg-emerald-50 p-3">
                      <div className="text-sm font-semibold text-emerald-800">{importedCadAsset.filename}</div>
                      <div className="mt-1 text-xs text-emerald-700">{importedCadAsset.extension.toUpperCase()} · {formatFileSize(importedCadAsset.sizeBytes)}</div>
                    </div>
                  ) : null}
                </div>
              )}

              <div className="flex justify-end gap-3 border-t border-slate-200 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setIsDraftingNewProject(false);
                    setIsCreateOpen(false);
                  }}
                  disabled={isSubmittingForgeCad}
                  className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={handleStartProject}
                  disabled={isSubmittingForgeCad || isSubmittingCadAiWorkflow}
                  className="rounded-lg border border-slate-200 px-5 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  创建项目
                </button>
                <button
                  type="button"
                  onClick={handleCreateProject}
                  disabled={isSubmittingForgeCad || isSubmittingCadAiWorkflow}
                  className="rounded-lg bg-brand-secondary px-5 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isSubmittingForgeCad || isSubmittingCadAiWorkflow ? '正在生成...' : '生成设计方案'}
                </button>
              </div>
            </div>
          </section>
        </div>
      ) : null}

    </div>
  );
};

export default CoCreationAgentWorkspace;
