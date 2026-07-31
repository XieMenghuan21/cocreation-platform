import { API_BASE_URL } from '../config/api';
import { request } from './httpRequest';

const ENDPOINT = `${API_BASE_URL}/api/v1/platform-tools/aggregation-workbench/catalog`;
const WORKBENCH_BASE_ENDPOINT = `${API_BASE_URL}/api/v1/platform-tools/aggregation-workbench`;

export interface AggregationWorkbenchCatalogItem {
  id: string;
  label: string;
  expectedType: string;
  connected: boolean;
  platformName: string | null;
  platformDisplayName: string | null;
  platformType: string | null;
  description: string | null;
  tags: string[];
  provider?: string | null;
  modelType?: string | null;
  supportedEndpointTypes?: string[];
}

export interface AggregationWorkbenchCatalogData {
  provider: string;
  configured: boolean;
  imageConfigured?: boolean;
  imageProvider?: string;
  imageProviders?: Record<string, boolean>;
  balance: number | null;
  unit: string;
  apiKeyQuota: Record<string, unknown>;
  totalModels: number;
  counts: Record<string, number>;
  models: AggregationWorkbenchCatalogItem[];
  rawModels: Array<Record<string, unknown>>;
}

export interface AggregationWorkbenchChatPayload {
  model: string;
  prompt: string;
  images?: string[];
  temperature?: number;
  maxTokens?: number;
}

export interface AggregationWorkbenchChatResult {
  model: string;
  content: string;
  raw: Record<string, unknown>;
}

export interface AggregationWorkbenchImagePayload {
  prompt: string;
  images?: string[];
  model?: string;
  provider?: string;
  optimizePrompt?: boolean;
}

export interface ImagePromptReference {
  source: string;
  category: string;
  prompt: string;
  tags?: string[];
  score?: number;
}

export interface ImagePromptMeta {
  originalPrompt: string;
  optimizedPrompt: string;
  finalPrompt: string;
  enabled: boolean;
  references: ImagePromptReference[];
}

export interface OptimizePromptPayload {
  prompt: string;
  model?: string | null;
}

export interface OptimizePromptResult {
  originalPrompt: string;
  optimizedPrompt: string;
  finalPrompt: string;
  enabled: boolean;
  aiOptimized: boolean;
  references: ImagePromptReference[];
}

export interface AggregationWorkbenchImageResult {
  taskId: string;
  model: string;
  resultUrl: string;
  status: string;
  raw: Record<string, unknown>;
  promptMeta?: ImagePromptMeta;
}

export const aggregationWorkbenchService = {
  async getCatalog() {
    return request<AggregationWorkbenchCatalogData>({
      url: ENDPOINT,
      method: 'GET',
    });
  },

  async createChatCompletion(payload: AggregationWorkbenchChatPayload) {
    return request<AggregationWorkbenchChatResult>({
      url: `${WORKBENCH_BASE_ENDPOINT}/chat`,
      method: 'POST',
      data: {
        model: payload.model,
        prompt: payload.prompt,
        images: payload.images ?? [],
        temperature: payload.temperature ?? 0.7,
        max_tokens: payload.maxTokens ?? 1024,
      },
      timeout: 130000,
    });
  },

  async createImageGeneration(payload: AggregationWorkbenchImagePayload) {
    return request<AggregationWorkbenchImageResult>({
      url: `${WORKBENCH_BASE_ENDPOINT}/image`,
      method: 'POST',
      data: {
        prompt: payload.prompt,
        images: payload.images ?? [],
        model: payload.model,
        provider: payload.provider,
        optimizePrompt: payload.optimizePrompt ?? true,
      },
      timeout: 930000,
      cancelDuplicate: false,
    });
  },

  async optimizePrompt(payload: OptimizePromptPayload) {
    return request<OptimizePromptResult>({
      url: `${WORKBENCH_BASE_ENDPOINT}/prompt/optimize`,
      method: 'POST',
      data: {
        prompt: payload.prompt,
        model: payload.model ?? null,
      },
      timeout: 130000,
      cancelDuplicate: false,
    });
  },
};
