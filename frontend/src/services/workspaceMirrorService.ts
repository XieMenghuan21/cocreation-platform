import { request } from './httpRequest';

export type MirrorNodeType =
  | 'project'
  | 'requirement'
  | 'decision'
  | 'design_direction'
  | 'render'
  | 'model_3d'
  | 'cad'
  | 'quote'
  | 'engineering_package'
  | 'version'
  | 'status'
  | 'next_action';

export interface WorkspaceMirrorPayload {
  sourceKey: string;
  type: MirrorNodeType;
  status?: 'draft' | 'waiting_user' | 'queued' | 'running' | 'completed' | 'failed' | 'superseded';
  title: string;
  summary?: string;
  projectId?: string | null;
  taskId?: string | null;
  versionId?: string | null;
  parentSourceKey?: string | null;
  inputData?: Record<string, unknown>;
  outputData?: Record<string, unknown>;
  uiData?: Record<string, unknown>;
}

export const workspaceMirrorService = {
  async mirror(conversationId: string, payload: WorkspaceMirrorPayload): Promise<void> {
    await request({
      url: `/api/v1/conversations/${encodeURIComponent(conversationId)}/workspace/mirror`,
      method: 'POST',
      data: payload,
      showError: false,
      cancelDuplicate: false,
    });
  },

  /**
   * 镜像永远不能影响稳定工作流。调用方统一使用 safeMirror。
   */
  async safeMirror(conversationId: string | null | undefined, payload: WorkspaceMirrorPayload): Promise<void> {
    if (!conversationId) return;
    try {
      await workspaceMirrorService.mirror(conversationId, payload);
    } catch (error) {
      console.warn('[workspace-mirror] ignored:', error);
    }
  },
};
