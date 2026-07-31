import { industryCatalog, scenarioConfigs } from './CoCreationAgentWorkspace.constants';
import type {
  CoCreationScenario,
  VersionSnapshot,
  BuildVersionSnapshotArgs,
  IndustryRoot,
  IndustryGroup,
  IndustrySegment,
  IndustryLeaf,
  ProjectLibraryItem,
  AssetLibraryItem,
} from './CoCreationAgentWorkspace.types';
import type {
  CadAiTaskStatus,
  ForgeCadGeneratedAsset,
  ForgeCadGenerateResult,
  ForgeCadVersionSnapshot,
} from '../services/forgecadService';
import { normalizePreviewImageSource } from '../utils/previewImage';

export const buildScenarioFlow = (scenario: CoCreationScenario): string[] => scenarioConfigs[scenario].steps;

export const getScenarioStepLabel = (scenario: CoCreationScenario, stepIndex: number): string => {
  const steps = scenarioConfigs[scenario].steps;
  if (steps.length === 0) {
    return '';
  }
  const boundedIndex = Math.max(0, Math.min(stepIndex, steps.length - 1));
  return steps[boundedIndex] || '';
};

const COMPLETED_STATUSES = new Set(['已完成', 'completed']);

const getSnapshotTime = (value?: string): number => {
  if (!value) {
    return 0;
  }
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
};

const parseVersionNumber = (snapshotId: string): number => {
  const matched = snapshotId.match(/V(\d+(?:\.\d+)?)/i);
  if (!matched) {
    return 0;
  }
  const parsed = Number(matched[1]);
  return Number.isFinite(parsed) ? parsed : 0;
};

const inferProjectName = (snapshot: VersionSnapshot): string =>
  snapshot.projectName?.trim() || snapshot.sourceObject?.trim() || snapshot.label.trim() || '未命名项目';

const inferProjectId = (snapshot: VersionSnapshot, projectName: string): string =>
  snapshot.projectId?.trim() || snapshot.sourceProjectId?.trim() || snapshot.sourceObject?.trim() || projectName;

const inferIsFinalized = (snapshot: VersionSnapshot): boolean =>
  snapshot.isFinalized ??
  (COMPLETED_STATUSES.has(snapshot.status) ||
    Boolean(snapshot.previewImageUrl || snapshot.downloadUrl || snapshot.generatedAssets?.length || snapshot.prompt || snapshot.optimizedPrompt));

export const normalizeVersionSnapshots = (snapshots: VersionSnapshot[]): VersionSnapshot[] =>
  snapshots
    .map((snapshot) => {
      const projectName = inferProjectName(snapshot);
      const projectId = inferProjectId(snapshot, projectName);
      return {
        ...snapshot,
        projectId,
        projectName,
        versionNumber: snapshot.versionNumber ?? parseVersionNumber(snapshot.id),
        isFinalized: inferIsFinalized(snapshot),
        sourceProjectId: snapshot.sourceProjectId?.trim() || projectId,
        sourceObject: snapshot.sourceObject || projectName,
      };
    })
    .sort((left, right) => {
      const timeDiff = getSnapshotTime(right.createdAt) - getSnapshotTime(left.createdAt);
      if (timeDiff !== 0) {
        return timeDiff;
      }
      return (right.versionNumber || 0) - (left.versionNumber || 0);
    });

export const getVersionsForProject = (
  snapshots: VersionSnapshot[],
  projectId?: string,
  projectName?: string,
): VersionSnapshot[] => {
  const normalized = normalizeVersionSnapshots(snapshots);
  if (projectId) {
    return normalized.filter((snapshot) => snapshot.projectId === projectId);
  }
  if (projectName) {
    return normalized.filter((snapshot) => snapshot.projectName === projectName);
  }
  return [];
};

export const getProjectVersionCount = (
  snapshots: VersionSnapshot[],
  projectId?: string,
  projectName?: string,
): number => getVersionsForProject(snapshots, projectId, projectName).length;

export const groupSnapshotsByProject = (snapshots: VersionSnapshot[]): ProjectLibraryItem[] => {
  const grouped = new Map<string, VersionSnapshot[]>();
  normalizeVersionSnapshots(snapshots).forEach((snapshot) => {
    const key = snapshot.projectId || snapshot.projectName || snapshot.id;
    const existing = grouped.get(key) || [];
    existing.push(snapshot);
    grouped.set(key, existing);
  });

  return Array.from(grouped.entries())
    .map(([projectId, versions]) => {
      const sortedVersions = versions
        .slice()
        .sort((left, right) => getSnapshotTime(right.createdAt) - getSnapshotTime(left.createdAt));
      const latestVersion = sortedVersions[0] || null;
      const oldestVersion = sortedVersions[sortedVersions.length - 1] || latestVersion;
      const projectName = latestVersion?.projectName || latestVersion?.label || projectId;
      return {
        project: {
          id: projectId,
          name: projectName,
          industry: '全部行业',
          description: latestVersion?.resultText || latestVersion?.note || '',
          inputMode: 'prompt' as const,
          createdAt: oldestVersion?.createdAt || new Date().toISOString(),
          updatedAt: latestVersion?.createdAt || new Date().toISOString(),
          lastTaskId: latestVersion?.taskId || null,
          lastStatus: latestVersion?.status || null,
          lastResultText: latestVersion?.resultText || latestVersion?.executionSummary || latestVersion?.note || null,
          lastImageUrl: latestVersion?.previewImageUrl || null,
          versionCount: sortedVersions.length,
        },
        versions: sortedVersions,
        latestVersion,
      };
    })
    .sort((left, right) => getSnapshotTime(right.latestVersion?.createdAt) - getSnapshotTime(left.latestVersion?.createdAt));
};

const makeAssetId = (): string => `asset-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export const buildAssetsFromVersion = (
  version: VersionSnapshot,
  createAssetId: () => string = makeAssetId,
): AssetLibraryItem[] => {
  const normalizedVersion = normalizeVersionSnapshots([version])[0];
  if (!normalizedVersion) {
    return [];
  }

  const sourceProjectName = normalizedVersion.projectName || normalizedVersion.label;
  const createdAt = normalizedVersion.createdAt || new Date().toISOString();
  const baseAsset = {
    projectId: normalizedVersion.projectId || sourceProjectName,
    projectName: normalizedVersion.projectName || sourceProjectName,
    versionNumber: normalizedVersion.versionNumber || 0,
    sourceProjectName,
    sourceProjectId: normalizedVersion.sourceProjectId || normalizedVersion.projectId || sourceProjectName,
    sourceVersionId: normalizedVersion.id,
    sourceVersionLabel: normalizedVersion.label,
    createdAt,
    isFinalized: normalizedVersion.isFinalized ?? true,
  };

  const assets: AssetLibraryItem[] = [];
  if (normalizedVersion.previewImageUrl) {
    assets.push({
      id: createAssetId(),
      kind: 'image',
      title: `${normalizedVersion.label} 定稿图`,
      description: normalizedVersion.resultText || normalizedVersion.executionSummary || normalizedVersion.note,
      imageUrl: normalizedVersion.previewImageUrl,
      prompt: normalizedVersion.prompt,
      ...baseAsset,
    });
  }
  if (normalizedVersion.prompt || normalizedVersion.optimizedPrompt) {
    assets.push({
      id: createAssetId(),
      kind: 'prompt',
      title: `${normalizedVersion.label} Prompt`,
      description: normalizedVersion.prompt || normalizedVersion.optimizedPrompt || normalizedVersion.resultText || normalizedVersion.note,
      prompt: normalizedVersion.prompt || normalizedVersion.optimizedPrompt,
      ...baseAsset,
    });
  }
  return assets;
};

const getNextVersionId = (previousSnapshots: VersionSnapshot[]): string => {
  const currentMax = previousSnapshots.reduce((maxValue, snapshot) => {
    const matched = snapshot.id.match(/^V(\d+(?:\.\d+)?)$/);
    if (!matched) {
      return maxValue;
    }

    return Math.max(maxValue, Number(matched[1]));
  }, 0);

  return `V${(Math.round((currentMax + 0.1) * 10) / 10).toFixed(1)}`;
};

const getLogSummary = (logs: string): string => {
  const compactLogs = logs
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 2)
    .join('；');

  return compactLogs || 'ForgeCAD 已返回脚本与执行结果。';
};

export const formatSnapshotTime = (value?: string): string => {
  if (!value) {
    return '刚刚';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
};

export const formatFileSize = (sizeBytes: number): string => {
  if (sizeBytes >= 1024 * 1024) {
    return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
  }
  if (sizeBytes >= 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${sizeBytes} B`;
};

export const getIndustryLeafPathLabel = (root: IndustryRoot, group: IndustryGroup, segment: IndustrySegment, leaf: IndustryLeaf): string =>
  `${root.label} / ${group.label} / ${segment.label} / ${leaf.label}`;

export const getAllIndustryLeaves = (): Array<{
  root: IndustryRoot;
  group: IndustryGroup;
  segment: IndustrySegment;
  leaf: IndustryLeaf;
  pathLabel: string;
}> =>
  industryCatalog.flatMap((root) =>
    root.groups.flatMap((group) =>
      group.segments.flatMap((segment) =>
        segment.leaves.map((leaf) => ({
          root,
          group,
          segment,
          leaf,
          pathLabel: getIndustryLeafPathLabel(root, group, segment, leaf),
        })),
      ),
    ),
  );

const stringifyCadAiOutput = (value: unknown): string | null => {
  if (typeof value === 'string' && value.trim()) {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return null;
};

export const getCadAiOutputValue = (outputs: Record<string, unknown> | null | undefined, keys: string[]): string | null => {
  if (!outputs) {
    return null;
  }
  for (const key of keys) {
    const value = stringifyCadAiOutput(outputs[key]);
    if (value) {
      return value;
    }
  }
  return null;
};

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ASSET_DOWNLOAD_PATTERN =
  /^\/api\/v1\/assets\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/download$/i;

export const extractAssetIdFromWorkflowReference = (value: unknown): string | null => {
  const reference = stringifyCadAiOutput(value)?.trim();
  if (!reference) return null;
  if (UUID_PATTERN.test(reference)) return reference;
  return reference.match(ASSET_DOWNLOAD_PATTERN)?.[1] ?? null;
};

const normalizeWorkflowAssetUrl = (value: unknown): string | null => {
  const reference = stringifyCadAiOutput(value)?.trim();
  if (!reference) return null;
  const assetId = extractAssetIdFromWorkflowReference(reference);
  return assetId ? `/api/v1/assets/${assetId}/download` : null;
};

const getCadAiAssetUrl = (
  outputs: Record<string, unknown>,
  keys: string[],
): string | null => {
  for (const key of keys) {
    const url = normalizeWorkflowAssetUrl(outputs[key]);
    if (url) return url;
  }
  return null;
};

const getCadAiAssetId = (
  outputs: Record<string, unknown>,
  keys: string[],
): string | null => {
  for (const key of keys) {
    const assetId = extractAssetIdFromWorkflowReference(outputs[key]);
    if (assetId) return assetId;
  }
  return null;
};

export const buildCadAiGeneratedAssets = (outputs: Record<string, unknown> | null | undefined): ForgeCadGeneratedAsset[] => {
  if (!outputs) {
    return [];
  }
  const assetDefs: Array<{ key: string; idKey: string; name: string; assetType: string }> = [
    { key: 'modelStep', idKey: 'modelStepAssetId', name: 'STEP 模型', assetType: 'step' },
    { key: 'modelStl', idKey: 'modelStlAssetId', name: 'STL 模型', assetType: 'stl' },
    { key: 'modelGlb', idKey: 'modelGlbAssetId', name: 'GLB 模型', assetType: 'glb' },
    { key: 'drawingSvg', idKey: 'drawingSvgAssetId', name: 'SVG 工程图', assetType: 'svg' },
    { key: 'drawingPdf', idKey: 'drawingPdfAssetId', name: 'PDF 工程图', assetType: 'pdf' },
    { key: 'drawingDxf', idKey: 'drawingDxfAssetId', name: 'DXF 工程图', assetType: 'dxf' },
    { key: 'renderPng', idKey: 'renderPngAssetId', name: '真实 3D 图', assetType: 'png' },
    { key: 'explosionPng', idKey: 'explosionPngAssetId', name: '爆炸图', assetType: 'png' },
    { key: 'enhancedImage', idKey: 'enhancedImageAssetId', name: '增强效果图', assetType: 'image' },
    { key: 'trellisGlb', idKey: 'trellisGlbAssetId', name: '展示级 3D 资产', assetType: 'glb' },
  ];

  const assets: ForgeCadGeneratedAsset[] = [];
  assetDefs.forEach((item) => {
    const downloadUrl = normalizeWorkflowAssetUrl(outputs[item.key]);
    if (!downloadUrl) {
      return;
    }
    const assetId =
      extractAssetIdFromWorkflowReference(outputs[item.idKey]) ??
      extractAssetIdFromWorkflowReference(outputs[item.key]);
    if (!assetId) {
      return;
    }
    assets.push({
      assetId,
      name: item.name,
      assetType: item.assetType,
      path: downloadUrl,
      downloadUrl,
      status: '已生成',
    });
  });
  return assets;
};

const normalizeForgeCadSnapshot = (
  result: ForgeCadGenerateResult,
  fallbackChangeType: string,
  fallbackSourceObject: string,
): ForgeCadVersionSnapshot => ({
  taskId: result.snapshot?.taskId || result.taskId,
  changeType: result.snapshot?.changeType || fallbackChangeType,
  sourceObject: result.snapshot?.sourceObject || fallbackSourceObject,
  scriptPath: result.snapshot?.scriptPath || result.scriptPath,
  workDir: result.snapshot?.workDir || result.workDir,
  outputPath: result.snapshot?.outputPath ?? result.outputPath,
  downloadUrl: result.snapshot?.downloadUrl || result.downloadUrl,
  executionSummary: result.snapshot?.executionSummary || getLogSummary(result.logs),
  createdAt: result.snapshot?.createdAt || new Date().toISOString(),
  statusLabel: result.snapshot?.statusLabel || (result.status === 'completed' ? '执行完成' : '脚本已生成'),
  cliExecuted: result.snapshot?.cliExecuted ?? result.cliExecuted,
  exportFormat: result.snapshot?.exportFormat || result.exportFormat,
  modelObjects: result.snapshot?.modelObjects?.length ? result.snapshot.modelObjects : result.modelObjects,
  parameters: result.snapshot?.parameters?.length ? result.snapshot.parameters : result.parameters,
  generatedAssets: result.snapshot?.generatedAssets?.length ? result.snapshot.generatedAssets : result.generatedAssets,
  diagnostics: result.snapshot?.diagnostics?.length ? result.snapshot.diagnostics : result.diagnostics,
});

export const buildVersionSnapshot = ({
  previousSnapshots,
  projectId,
  projectName,
  label,
  status,
  notePrefix,
  result,
  fallbackChangeType,
  fallbackSourceObject,
  sourceProjectId,
  prompt,
}: BuildVersionSnapshotArgs): VersionSnapshot => {
  const snapshot = normalizeForgeCadSnapshot(result, fallbackChangeType, fallbackSourceObject);

  return {
    id: getNextVersionId(previousSnapshots),
    label,
    projectId,
    projectName,
    versionNumber: previousSnapshots.length + 1,
    isFinalized: false,
    sourceProjectId: sourceProjectId || projectId,
    status: snapshot.statusLabel || status,
    note: `${notePrefix}；${snapshot.executionSummary}`,
    prompt,
    changeType: snapshot.changeType,
    sourceObject: snapshot.sourceObject,
    taskId: snapshot.taskId,
    scriptPath: snapshot.scriptPath,
    workDir: snapshot.workDir,
    outputPath: snapshot.outputPath,
    downloadUrl: snapshot.downloadUrl,
    executionSummary: snapshot.executionSummary,
    createdAt: snapshot.createdAt,
    cliExecuted: snapshot.cliExecuted,
    exportFormat: snapshot.exportFormat,
    modelObjects: snapshot.modelObjects,
    parameters: snapshot.parameters,
    generatedAssets: snapshot.generatedAssets,
    diagnostics: snapshot.diagnostics,
  };
};

export const buildCadAiVersionSnapshot = (
  task: CadAiTaskStatus,
  previousSnapshots: VersionSnapshot[],
  projectId: string,
  projectName: string,
  prompt?: string,
): VersionSnapshot => {
  const outputs = (task.outputs || {}) as Record<string, unknown>;
  const outputPath = getCadAiAssetUrl(
    outputs,
    ['modelGlb', 'modelStl', 'modelStep', 'renderPng', 'explosionPng'],
  );
  const downloadUrl = outputPath ?? undefined;
  const progress = typeof task.progress === 'number' ? `${task.progress}%` : '100%';
  const resultText = task.currentStep || task.error || `项目「${projectName || task.projectId}」${task.status}，进度 ${progress}`;
  const previewImageUrl = normalizePreviewImageSource(
    getCadAiAssetUrl(outputs, ['renderPng', 'drawingSvg', 'enhancedImage']),
  );
  const scriptAssetId = getCadAiAssetId(outputs, ['modelScriptAssetId']);
  const outputAssetId = getCadAiAssetId(outputs, [
    'renderPngAssetId',
    'enhancedImageAssetId',
    'drawingSvgAssetId',
    'modelGlbAssetId',
    'modelStepAssetId',
  ]);
  const generatedImageUrls = [
    normalizePreviewImageSource(getCadAiAssetUrl(outputs, ['renderPng'])),
    normalizePreviewImageSource(getCadAiAssetUrl(outputs, ['enhancedImage'])),
  ].filter((value): value is string => Boolean(value));

  return {
    id: getNextVersionId(previousSnapshots),
    label: projectName || task.projectId || '未命名项目',
    projectId,
    projectName: projectName || task.projectId || '未命名项目',
    versionNumber: previousSnapshots.length + 1,
    isFinalized: false,
    sourceProjectId: projectId,
    status: task.status === 'completed' ? '已完成' : task.status,
    note: task.currentStep || `项目「${projectName || task.projectId}」设计任务已返回状态 ${task.status}`,
    prompt,
    resultText,
    previewImageUrl,
    generatedImageUrls,
    changeType: '方案生成',
    sourceObject: task.projectId || projectName,
    taskId: task.taskId,
    scriptAssetId,
    outputAssetId,
    scriptPath: task.versionId ? `版本：${task.versionId}` : '由共创工作台生成',
    workDir: task.projectId || '',
    outputPath,
    downloadUrl,
    executionSummary: task.error ? `任务失败：${task.error}` : `项目「${projectName || task.projectId}」${task.status}，进度 ${progress}`,
    createdAt: task.updatedAt || task.createdAt || new Date().toISOString(),
    cliExecuted: true,
    exportFormat: outputPath?.split('.').pop() || 'glb',
    generatedAssets: buildCadAiGeneratedAssets(outputs),
    diagnostics: task.error
      ? [{ level: 'error', title: '设计任务失败', detail: task.error }]
      : [{ level: 'info', title: '方案生成', detail: task.currentStep || '设计任务已完成。' }],
  };
};

export const resolveIndustrialDesignSubmitError = (error: unknown): string => {
  const rawMessage = error instanceof Error ? error.message : '工业品设计工作流提交失败。';
  if (/401|需要认证|未认证|登录状态|Unauthorized/i.test(rawMessage)) {
    return '登录状态失效或未携带认证信息，请重新登录后重试。';
  }
  if (/Failed to fetch|NetworkError|Load failed|fetch/i.test(rawMessage)) {
    return '后端服务未连接，请检查服务状态或 VITE_API_BASE_URL 后重试。';
  }
  return rawMessage;
};

export const buildProjectId = (projectName: string): string =>
  `${projectName.trim() || 'project'}-${Date.now().toString(36)}`;

export const ensureUniqueProjectName = (
  candidateName: string,
  existingProjects: Array<{ id: string; name: string }>,
  activeProjectId?: string,
): string => {
  const trimmed = candidateName.trim() || '未命名项目';
  const occupied = new Set(
    existingProjects
      .filter((project) => project.id !== activeProjectId)
      .map((project) => project.name.trim()),
  );
  if (!occupied.has(trimmed)) {
    return trimmed;
  }
  let index = 2;
  let nextName = `${trimmed}（${index}）`;
  while (occupied.has(nextName)) {
    index += 1;
    nextName = `${trimmed}（${index}）`;
  }
  return nextName;
};
