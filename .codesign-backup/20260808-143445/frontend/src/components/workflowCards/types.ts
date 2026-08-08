import type { CadAiTaskStatus } from '../../services/forgecadService';
export type CardType =
  | 'project_created'
  | 'requirement'
  | 'design_scheme'
  | 'quote'
  | 'status'
  | 'next_step'
  | 'materials_request'
  | 'prompt_confirm';

export interface MaterialField {
  key: string;
  label: string;
  hint: string;
  collected: boolean;
}

export interface MaterialsRequestCardData {
  projectName: string;
  fields: MaterialField[];
  collected: Record<string, string>;
}

export interface ProjectCreatedCardData {
  name: string;
  description: string;
  projectType: string;
  projectId: string;
}

export interface RequirementCardData {
  productType: string;
  scene: string;
  style: string;
  budget: string;
  dimensions: Record<string, unknown>;
  materials: string[];
  constraints: string[];
  completeness: number;
  missing: string[];
}

export interface DesignSchemeCardData {
  schemeId: string;
  name: string;
  thumbnails: string[];
  materials: string[];
  estimatedPrice: { min: number; max: number } | null;
  renderUrl: string | null;
  drawingUrl: string | null;
  outputs: CadAiTaskStatus['outputs'] | null;
}

export interface QuoteCardData {
  quoteId: string;
  schemeName: string;
  materialCost: number;
  productionCost: number;
  totalInternal: number;
  totalCustomer: number;
}

export interface StatusCardData {
  agent: string;
  task: string;
  progress: number;
  stage: string;
  estimatedRemaining: string | null;
}

export interface PromptCardData {
  original: string;
  optimized: string;
  references: Array<{ source: string; prompt: string }>;
}

export interface NextStepRecommendation {
  label: string;
  agent: string;
  icon: string;
  action: 'render' | '3d' | 'cad' | 'quote' | 'package';
}

export interface NextStepCardData {
  current: string;
  recommendations: NextStepRecommendation[];
}

export interface WorkflowCard {
  id: string;
  type: CardType;
  data: ProjectCreatedCardData | RequirementCardData | DesignSchemeCardData | QuoteCardData | StatusCardData | NextStepCardData | MaterialsRequestCardData | PromptCardData;
}

export type CardDataByType = {
  project_created: ProjectCreatedCardData;
  requirement: RequirementCardData;
  design_scheme: DesignSchemeCardData;
  quote: QuoteCardData;
  status: StatusCardData;
  next_step: NextStepCardData;
  materials_request: MaterialsRequestCardData;
  prompt_confirm: PromptCardData;
};
