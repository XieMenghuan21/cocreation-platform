import { post, type ApiResponse } from './httpRequest';

export type DesignIntent = 'design' | 'propaganda' | 'production';

export interface IntentSuggestedOptions {
  generateDrawing: boolean;
  generateRender: boolean;
  generateCad: boolean;
  generateExplosion: boolean;
  enhanceImage: boolean;
  generatePlanLine: boolean;
  generateThreePreview: boolean;
}

export interface IntentAnalysis {
  intent: DesignIntent;
  intentLabel: string;
  projectName: string;
  industry: string;
  requirementText: string;
  needsMaterials: boolean;
  suggestedOptions: IntentSuggestedOptions;
  reasoning: string;
}

export interface ProjectRecord {
  id: string;
  name: string;
  industry: string | null;
  description: string;
  inputMode: string;
  createdAt: string;
  updatedAt: string;
  lastTaskId: string | null;
  lastStatus: string | null;
  lastResultText: string | null;
  versionCount: number;
  lastImageUrl: string | null;
}

export interface CreateProjectPayload {
  name: string;
  description?: string;
  industry?: string | null;
  inputMode?: string;
}

export const agentService = {
  async analyzeIntent(text: string): Promise<IntentAnalysis> {
    const response = await post<IntentAnalysis>(
      '/api/v1/agent/intent',
      { text },
      { showError: false },
    );
    return response.data;
  },

  async createProject(payload: CreateProjectPayload): Promise<ProjectRecord> {
    const response = await post<ProjectRecord>(
      '/api/v1/projects',
      {
        name: payload.name,
        description: payload.description ?? '',
        industry: payload.industry ?? null,
        inputMode: payload.inputMode ?? 'prompt',
      },
      { showError: false },
    );
    return response.data;
  },

  async parseMaterials(text: string): Promise<Record<string, string>> {
    const response = await post<Record<string, string>>(
      '/api/v1/agent/materials/parse',
      { text },
      { showError: false },
    );
    return response.data;
  },
};
