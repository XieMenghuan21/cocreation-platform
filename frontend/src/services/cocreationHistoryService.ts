import { request } from './httpRequest';
import type { ProjectRecord, VersionSnapshot } from '../components/CoCreationAgentWorkspace.types';
import { assetService, type AssetRecord, type AssetList } from './assetService';
import { workspaceService, type WorkspaceState } from './workspaceService';

const HISTORY_BASE_ENDPOINT = '/api/v1/cocreation-history';

export interface CocreationHistoryListResult {
  projects: ProjectRecord[];
  snapshots: VersionSnapshot[];
  total?: number;
}

export type DatabaseAssetRecord = AssetRecord;
export type DatabaseAssetListResult = AssetList;
export type WorkspaceReferenceResult = WorkspaceState;

export async function runHistoryMutationAndRefresh(
  mutation: () => Promise<unknown>,
  refresh: () => Promise<unknown>,
): Promise<void> {
  await mutation();
  await refresh();
}

export const cocreationHistoryService = {
  async listHistory(limit = 200, offset = 0) {
    return request<CocreationHistoryListResult>({
      url: `${HISTORY_BASE_ENDPOINT}/projects`,
      method: 'GET',
      params: { limit, offset },
      showError: false,
    });
  },

  async listAllHistory(pageSize = 200) {
    const projects: ProjectRecord[] = [];
    const snapshots: VersionSnapshot[] = [];
    let offset = 0;
    let total = 0;
    do {
      const response = await cocreationHistoryService.listHistory(pageSize, offset);
      const pageProjects = response.data.projects ?? [];
      projects.push(...pageProjects);
      snapshots.push(...(response.data.snapshots ?? []));
      total = response.data.total ?? pageProjects.length;
      offset += pageProjects.length;
      if (pageProjects.length === 0 || pageProjects.length < pageSize) break;
    } while (projects.length < total);
    return {
      data: {
        projects,
        snapshots,
        total,
      } satisfies CocreationHistoryListResult,
    };
  },

  async upsertProjectWithVersion(payload: { project: ProjectRecord; version: VersionSnapshot }) {
    return request<{ projectId: string; versionId: string }>({
      url: `${HISTORY_BASE_ENDPOINT}/projects/upsert-with-version`,
      method: 'POST',
      data: payload,
      showError: false,
      cancelDuplicate: false,
    });
  },

  async deleteVersion(projectId: string, versionId: string) {
    return request<{ deleted: boolean }>({
      url: `${HISTORY_BASE_ENDPOINT}/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}`,
      method: 'DELETE',
      showError: false,
    });
  },

  async publishVersion(projectId: string, versionId: string) {
    return request<{
      projectId: string;
      versionId: string;
      published: boolean;
      assetCount: number;
    }>({
      url: `${HISTORY_BASE_ENDPOINT}/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}/publish`,
      method: 'POST',
      showError: false,
      cancelDuplicate: false,
    });
  },

  async setWorkspaceReference(projectId: string, versionId: string) {
    return { data: await workspaceService.setReference(projectId, versionId) };
  },

  async getWorkspace() {
    return { data: await workspaceService.get() };
  },

  async listAssetLibrary() {
    return {
      data: await assetService.listAll({
        library: true,
      }),
    };
  },
};
