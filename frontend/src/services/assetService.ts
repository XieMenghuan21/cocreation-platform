import { request } from './httpRequest';

export interface AssetRecord {
  id: string;
  userId: string;
  projectId: string | null;
  versionId: string | null;
  sourceVersionId: string | null;
  taskId: string | null;
  kind: string;
  filename: string;
  extension: string | null;
  contentType: string;
  sizeBytes: number;
  sha256: string;
  chunkSize: number;
  chunkCount: number;
  status: string;
  source: string;
  metadata: Record<string, unknown>;
  downloadUrl?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AssetList {
  items: AssetRecord[];
  total: number;
}

export interface AssetListQuery {
  limit?: number;
  offset?: number;
  kind?: string;
  status?: string;
  library?: boolean;
  source?: string;
}

export interface AssetUploadOptions {
  kind: string;
  source: string;
  projectId?: string;
  versionId?: string;
  taskId?: string;
  metadata?: Record<string, unknown>;
}

export const assetDownloadUrl = (assetId: string): string =>
  `/api/v1/assets/${encodeURIComponent(assetId)}/download`;

export const assetService = {
  async list(params: AssetListQuery = {}): Promise<AssetList> {
    const response = await request<AssetList>({
      url: '/api/v1/assets',
      method: 'GET',
      params: { ...params },
      showError: false,
    });
    return response.data;
  },

  async listAll(params: Omit<AssetListQuery, 'limit' | 'offset'> = {}): Promise<AssetList> {
    const limit = 200;
    const items: AssetRecord[] = [];
    let offset = 0;
    let total = 0;
    do {
      const page = await assetService.list({ ...params, limit, offset });
      const pageItems = page.items ?? [];
      total = page.total ?? pageItems.length;
      items.push(...pageItems);
      offset += pageItems.length;
      if (pageItems.length === 0 || pageItems.length < limit) break;
    } while (items.length < total);
    return { items, total };
  },

  async get(assetId: string): Promise<AssetRecord> {
    const response = await request<AssetRecord>({
      url: `/api/v1/assets/${encodeURIComponent(assetId)}`,
      method: 'GET',
      showError: false,
    });
    return response.data;
  },

  async upload(file: File, options: AssetUploadOptions): Promise<AssetRecord> {
    const form = new FormData();
    form.append('file', file);
    form.append('kind', options.kind);
    form.append('source', options.source);
    if (options.projectId) form.append('projectId', options.projectId);
    if (options.versionId) form.append('versionId', options.versionId);
    if (options.taskId) form.append('taskId', options.taskId);
    if (options.metadata) form.append('metadata', JSON.stringify(options.metadata));

    const response = await request<AssetRecord>({
      url: '/api/v1/assets/upload',
      method: 'POST',
      data: form,
      timeout: 120_000,
      showError: false,
      cancelDuplicate: false,
    });
    return response.data;
  },

  async remove(assetId: string): Promise<void> {
    await request<null>({
      url: `/api/v1/assets/${encodeURIComponent(assetId)}`,
      method: 'DELETE',
      showError: false,
    });
  },
};
