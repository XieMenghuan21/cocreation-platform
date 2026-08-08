import { get } from './httpRequest';
import type { WorkspaceNode } from './workspaceGraphService';

interface WorkspaceNodeListResponse {
  nodes: WorkspaceNode[];
}

/**
 * Workspace 资源页专用查询。
 * 与 Conversation Turn 解耦：资源页只做查询/归档，不负责调度 Agent。
 */
export const workspaceResourceService = {
  async listNodes(params: { type?: WorkspaceNode['type']; projectId?: string } = {}): Promise<WorkspaceNode[]> {
    const response = await get<WorkspaceNodeListResponse>(
      '/api/v1/conversations/workspace-nodes',
      {
        node_type: params.type,
        project_id: params.projectId,
      },
      { showError: false },
    );
    return response.data.nodes ?? [];
  },
};
