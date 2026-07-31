import httpRequest from './httpRequest';
import { assetDownloadUrl } from './assetService';

export type ForgeCadExportFormat = 'none' | 'step' | 'stl' | 'brep';
export type ForgeCadTaskStatus = 'script_generated' | 'completed';
export type ForgeCadSnapshotAction = 'create' | 'structure' | 'appearance' | 'derive' | 'concept';

export interface ForgeCadModelObject {
  name: string;
  volume: string | null;
  bbox: string | null;
  geometry: string | null;
}

export interface ForgeCadImportAsset {
  assetId: string;
  filename: string;
  extension: string;
  contentType: string;
  sizeBytes: number;
  storagePath: string;
  createdAt: string;
  parseStatus: string;
  parseMessage: string;
  parseFeatures: ForgeCadImportFeature[];
  previewKind: string;
  previewAssetId?: string | null;
  previewAssetPath?: string | null;
  previewAssetFormat?: string | null;
  previewAssetUrl?: string | null;
  conversionStatus?: string | null;
  conversionMessage?: string | null;
  previewEntities: ForgeCadPreviewEntity[];
  bomItems: ForgeCadBomItem[];
  explosionSteps: ForgeCadExplosionStep[];
  downloadUrl: string;
}

export interface ForgeCadImportFeature {
  label: string;
  value: string;
}

export interface ForgeCadPreviewEntity {
  entityType: string;
  points: number[][];
  center: number[] | null;
  radius: number | null;
  startAngle?: number | null;
  endAngle?: number | null;
}

export interface ForgeCadBomItem {
  name: string;
  material: string | null;
  quantity: number;
  size: string | null;
  source: string;
}

export interface ForgeCadExplosionStep {
  step: number;
  name: string;
  offset: number[];
  description: string;
}

export interface ForgeCadParameter {
  name: string;
  defaultValue: string | null;
}

export interface ForgeCadGeneratedAsset {
  assetId?: string | null;
  name: string;
  assetType: string;
  path: string | null;
  downloadUrl?: string | null;
  status: string;
}

export interface ForgeCadDiagnostic {
  level: 'info' | 'warning' | 'error';
  title: string;
  detail: string;
}

export interface ForgeCadVersionSnapshot {
  taskId: string;
  changeType: string;
  sourceObject: string;
  scriptPath: string;
  workDir: string;
  outputPath: string | null;
  downloadUrl?: string;
  executionSummary: string;
  createdAt: string;
  statusLabel: string;
  cliExecuted: boolean;
  exportFormat: ForgeCadExportFormat;
  modelObjects: ForgeCadModelObject[];
  parameters: ForgeCadParameter[];
  generatedAssets: ForgeCadGeneratedAsset[];
  diagnostics: ForgeCadDiagnostic[];
}

export interface ForgeCadGenerateRequest {
  prompt: string;
  exportFormat?: ForgeCadExportFormat;
  runCli?: boolean;
  temperature?: number;
  maxTokens?: number;
  action?: ForgeCadSnapshotAction;
  sourceObject?: string;
}

export interface ForgeCadGenerateResult {
  taskId: string;
  status: ForgeCadTaskStatus;
  script: string;
  scriptPath: string;
  workDir: string;
  outputPath: string | null;
  downloadUrl?: string;
  logs: string;
  cliExecuted: boolean;
  exportFormat: ForgeCadExportFormat;
  snapshot: ForgeCadVersionSnapshot | null;
  modelObjects: ForgeCadModelObject[];
  parameters: ForgeCadParameter[];
  generatedAssets: ForgeCadGeneratedAsset[];
  diagnostics: ForgeCadDiagnostic[];
}

const API_BASE = '/api/v1/forgecad';

const normalizeImportedAsset = (
  asset: ForgeCadImportAsset,
): ForgeCadImportAsset => ({
  ...asset,
  downloadUrl: assetDownloadUrl(asset.assetId),
  previewAssetUrl:
    asset.previewAssetId
      ? assetDownloadUrl(asset.previewAssetId)
      : asset.previewAssetUrl,
});

export const uploadForgeCadImportAsset = async (file: File): Promise<ForgeCadImportAsset> => {
  const response = await httpRequest.upload<ForgeCadImportAsset>(`${API_BASE}/import`, file, {
    timeout: 120000,
  });
  return normalizeImportedAsset(response.data);
};

export const uploadForgeCadVoiceAsset = async (file: File): Promise<ForgeCadImportAsset> => {
  const response = await httpRequest.upload<ForgeCadImportAsset>(`${API_BASE}/voice/import`, file, {
    timeout: 120000,
  });
  return normalizeImportedAsset(response.data);
};

export const generateForgeCadModel = async (
  payload: ForgeCadGenerateRequest,
): Promise<ForgeCadGenerateResult> => {
  const response = await httpRequest.post<ForgeCadGenerateResult>(`${API_BASE}/generate`, payload, {
    timeout: 190000,
  });
  return response.data;
};

export const fetchForgeCadBlobByUrl = async (url: string): Promise<Blob> => {
  const response = await httpRequest.get<Blob>(url, undefined, {
    responseType: 'blob',
    timeout: 120000,
  });
  return response.data;
};

export interface GenerateWithDrawingPayload {
  forgecadRequest: ForgeCadGenerateRequest;
  drawingRequest: {
    industry?: string;
    templateType?: string;
    projectName: string;
    width: number;
    height: number;
    depth: number;
    doorType?: string;
    material?: string;
    modules: Array<{
      sectionType: string;
      width: number;
      drawerCount?: number;
      shelfCount?: number;
      topStorage?: boolean;
      shoeZone?: boolean;
      label?: string;
    }>;
  };
}

export interface GenerateWithDrawingResult {
  forgecadResult?: ForgeCadGenerateResult;
  forgecadError?: string;
  drawingResult?: {
    drawingId: string;
    svgContent: string;
    summary: string;
    views: Array<{ key: string; title: string; scale: string; description: string }>;
    bomItems: Array<{ name: string; material: string; quantity: number; size: string; remark?: string }>;
    generatedAt: string;
  };
  drawingError?: string;
}

export const generateWithDrawing = async (
  payload: GenerateWithDrawingPayload,
): Promise<GenerateWithDrawingResult> => {
  const response = await httpRequest.post<GenerateWithDrawingResult>(
    `${API_BASE}/generate-with-drawing`,
    payload,
    { timeout: 240000 },
  );
  return response.data;
};

export type CadAiInputType = 'text' | 'voice' | 'cad' | 'image' | 'pdf';
export type CadAiTaskRuntimeStatus = 'pending' | 'running' | 'completed' | 'failed' | string;

export interface CadAiAutoGenerateOptions {
  generateDrawing: boolean;
  generateRender: boolean;
  generateExplosion: boolean;
  enhanceImage: boolean;
  generateTrellisAsset: boolean;
}

export interface CadAiAutoGeneratePayload {
  inputType: CadAiInputType;
  text?: string | null;
  assetIds?: string[];
  assetUrls?: string[];
  assetMetas?: Array<{
    assetId: string;
    filename: string;
    extension: string;
    contentType: string;
    sizeBytes: number;
    parseStatus: string;
    parseMessage: string;
    previewAssetUrl?: string | null;
  }>;
  projectName?: string | null;
  industry?: string | null;
  options: CadAiAutoGenerateOptions;
}

export type IndustrialDesignInputType = 'text' | 'voice' | 'drawing' | 'cad' | 'image' | 'pdf';

export interface IndustrialDesignWorkflowOptions extends CadAiAutoGenerateOptions {
  generateCad: boolean;
  generatePlanLine?: boolean;
  generateRenderViews?: boolean;
  cadProvider?: string | null;
  generateThreePreview: boolean;
  optimizePrompt?: boolean;
  imageModel?: string | null;
  imageProvider?: string | null;
}

export interface IndustrialDesignWorkflowPayload extends Omit<CadAiAutoGeneratePayload, 'inputType' | 'options'> {
  inputType: IndustrialDesignInputType;
  mode?: 'create' | 'redesign';
  context?: Record<string, unknown>;
  options: IndustrialDesignWorkflowOptions;
}

export interface CadAiTaskOutputs {
  drawingSvg?: string | null;
  drawingPdf?: string | null;
  drawingDxf?: string | null;
  modelStep?: string | null;
  modelStl?: string | null;
  modelGlb?: string | null;
  renderPng?: string | null;
  explosionPng?: string | null;
  enhancedImage?: string | null;
  trellisGlb?: string | null;
  [key: string]: unknown;
}

export interface CadAiTaskStatus {
  taskId: string;
  status: CadAiTaskRuntimeStatus;
  progress?: number | null;
  currentStep?: string | null;
  projectId?: string | null;
  versionId?: string | null;
  outputs?: CadAiTaskOutputs | null;
  error?: string | null;
  designSpec?: unknown;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export const autoGenerateCadAiProject = async (
  payload: CadAiAutoGeneratePayload,
): Promise<CadAiTaskStatus> => {
  const response = await httpRequest.post<CadAiTaskStatus>(`${API_BASE}/auto-generate`, payload, {
    timeout: 120000,
  });
  return response.data;
};

export const createIndustrialDesignWorkflow = async (
  payload: IndustrialDesignWorkflowPayload,
): Promise<CadAiTaskStatus> => {
  const response = await httpRequest.post<CadAiTaskStatus>('/api/v1/industrial-design/workflows', payload, {
    timeout: 240000,
  });
  return response.data;
};

export const getIndustrialDesignWorkflowTask = async (taskId: string): Promise<CadAiTaskStatus> => {
  const response = await httpRequest.get<CadAiTaskStatus>(`/api/v1/industrial-design/workflows/${taskId}`, undefined, {
    timeout: 60000,
  });
  return response.data;
};

export const getCadAiTask = async (taskId: string): Promise<CadAiTaskStatus> => {
  const response = await httpRequest.get<CadAiTaskStatus>(`${API_BASE}/tasks/${taskId}`, undefined, {
    timeout: 60000,
  });
  return response.data;
};

export const getCadAiAssetDownloadUrl = (assetId: string): string => {
  return `${API_BASE}/assets/${assetId}/download`;
};
