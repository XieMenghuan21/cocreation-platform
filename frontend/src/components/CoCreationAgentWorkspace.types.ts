import type {
  ForgeCadModelObject,
  ForgeCadParameter,
  ForgeCadGeneratedAsset,
  ForgeCadDiagnostic,
  ForgeCadGenerateResult,
  ForgeCadVersionSnapshot,
} from '../services/forgecadService';

export type ViewMode = 'cad' | 'preview3d' | 'exploded';
export type ProjectInputMode = 'prompt' | 'upload';
export type UploadDesignIntent = 'drawing' | 'objectToDrawing';
export type SceneMode = 'poster' | 'mid' | 'detail';
export type WorkspaceSubpage = 'overview' | 'planning' | 'modeling' | 'assets' | 'review';
export type RefineType = 'appearance' | 'structure' | 'concept';
export type CoCreationScenario = 'design' | 'propaganda' | 'production';
export type WorkflowStage = CoCreationScenario;

export interface ScenarioConfig {
  label: string;
  description: string;
  steps: string[];
  toneClass: string;
}

export interface ScenarioTab {
  id: CoCreationScenario;
  label: string;
  description: string;
}

export type IndustryCategory = '装备制造' | '汽车零部件' | '医疗器械' | '家居智造';
export type IndustryFilter = '全部行业' | string;

export interface IndustryTemplate {
  name: string;
  category: IndustryCategory;
  progress: string;
  color: string;
}

export interface IndustryLeafPrefill {
  projectName: string;
  description: string;
  fileTips: string;
}

export interface IndustryLeaf {
  id: string;
  label: string;
  keywords: string[];
  prefill: IndustryLeafPrefill;
}

export interface IndustrySegment {
  id: string;
  label: string;
  leaves: IndustryLeaf[];
}

export interface IndustryGroup {
  id: string;
  label: string;
  segments: IndustrySegment[];
}

export interface IndustryRoot {
  id: string;
  label: IndustryCategory;
  groups: IndustryGroup[];
}

export interface PartNode {
  code: string;
  name: string;
  status: string;
  level: string;
}

export interface BomRow {
  name: string;
  material: string;
  process: string;
  quantity: string;
}

export interface ProjectDraft {
  name: string;
  industry: IndustryFilter;
  inputMode: ProjectInputMode;
  uploadIntent: UploadDesignIntent;
  description: string;
}

export interface ProjectRecord {
  id: string;
  name: string;
  industry: IndustryFilter;
  description: string;
  inputMode: ProjectInputMode;
  createdAt: string;
  updatedAt: string;
  lastTaskId?: string | null;
  lastStatus?: string | null;
  lastResultText?: string | null;
  lastImageUrl?: string | null;
  versionCount?: number;
}

export interface VersionSnapshot {
  id: string;
  label: string;
  status: string;
  note: string;
  projectId?: string;
  projectName?: string;
  versionNumber?: number;
  isFinalized?: boolean;
  sourceProjectId?: string;
  prompt?: string;
  optimizedPrompt?: string;
  resultText?: string;
  previewImageUrl?: string | null;
  generatedImageUrls?: string[];
  changeType?: string;
  sourceObject?: string;
  taskId?: string;
  scriptAssetId?: string | null;
  outputAssetId?: string | null;
  scriptPath?: string;
  workDir?: string;
  outputPath?: string | null;
  downloadUrl?: string;
  executionSummary?: string;
  createdAt?: string;
  cliExecuted?: boolean;
  exportFormat?: string;
  modelObjects?: ForgeCadModelObject[];
  parameters?: ForgeCadParameter[];
  generatedAssets?: ForgeCadGeneratedAsset[];
  diagnostics?: ForgeCadDiagnostic[];
}

export interface RefineActionState {
  source: string;
  type: RefineType;
}

export interface BuildVersionSnapshotArgs {
  previousSnapshots: VersionSnapshot[];
  projectId: string;
  projectName: string;
  label: string;
  status: string;
  notePrefix: string;
  result: ForgeCadGenerateResult;
  fallbackChangeType: string;
  fallbackSourceObject: string;
  sourceProjectId?: string;
  prompt?: string;
}

export interface SubmitFeedbackState {
  title: string;
  detail: string;
}

export interface ResolvedProjectDraft {
  nextIndustry: IndustryFilter;
  nextName: string;
  description: string;
}

export interface CadAiWorkflowState {
  taskId: string;
  status: string;
  progress: number;
  currentStep: string;
  outputs: Record<string, unknown>;
  error?: string | null;
}

export interface ProjectLibraryItem {
  project: ProjectRecord;
  versions: VersionSnapshot[];
  latestVersion: VersionSnapshot | null;
}

export type AssetLibraryItemKind =
  | 'image'
  | 'prompt'
  | 'document'
  | 'model'
  | 'cad'
  | 'script'
  | 'archive'
  | 'audio'
  | 'other';

export interface AssetLibraryItem {
  id: string;
  kind: AssetLibraryItemKind;
  projectId: string;
  projectName: string;
  versionNumber: number;
  title: string;
  description: string;
  prompt?: string;
  imageUrl?: string | null;
  downloadUrl?: string | null;
  sourceProjectName: string;
  sourceProjectId: string;
  sourceVersionId: string;
  sourceVersionLabel: string;
  createdAt: string;
  isFinalized: boolean;
}
