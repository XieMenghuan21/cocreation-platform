import { get, post, type ApiResponse } from './httpRequest';

export type WorkspaceNodeType =
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

export type WorkspaceNodeStatus =
  | 'draft'
  | 'waiting_user'
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'superseded';

export interface WorkspaceNodeAsset {
  id: number;
  assetId: string;
  role: string;
  createdAt?: string;
}

export interface WorkspaceNode {
  id: string;
  conversationId: string;
  projectId?: string | null;
  parentId?: string | null;
  branchId?: string | null;
  type: WorkspaceNodeType;
  status: WorkspaceNodeStatus;
  title: string;
  summary: string;
  agentKey?: string | null;
  taskId?: string | null;
  versionId?: string | null;
  inputData?: Record<string, unknown>;
  outputData?: Record<string, unknown>;
  uiData?: Record<string, unknown>;
  assets?: WorkspaceNodeAsset[];
  createdAt: string;
  updatedAt: string;
}

export interface TurnPayload {
  text?: string | null;
  assetIds?: string[];
  action?: {
    nodeId: string;
    type: string;
    value?: unknown;
  } | null;
}

export interface TurnResponse {
  conversationId: string;
  message: {
    id: number;
    role: string;
    text: string;
  };
  nodesCreated: WorkspaceNode[];
  nodesUpdated: WorkspaceNode[];
  tasksStarted?: Array<Record<string, unknown>>;
  workspace: {
    activeNodeId?: string | null;
    previewNodeId?: string | null;
  };
}

export interface WorkspaceSnapshot {
  conversation?: {
    id: string;
    projectId?: string | null;
    title: string;
  } | null;
  project?: Record<string, unknown> | null;
  nodes: WorkspaceNode[];
  nodeAssets?: Record<string, WorkspaceNodeAsset[]>;
  activeTasks?: Array<Record<string, unknown>>;
  uiState?: Record<string, unknown>;
}

const unwrap = <T>(response: ApiResponse<T>): T => response.data;

export const workspaceGraphService = {
  async startTurn(payload: TurnPayload): Promise<TurnResponse> {
    const response = await post<TurnResponse>(
      '/api/v1/conversations/turns',
      {
        text: payload.text ?? null,
        assetIds: payload.assetIds ?? [],
        action: payload.action ?? null,
      },
      { showError: false },
    );
    return unwrap(response);
  },

  async appendTurn(conversationId: string, payload: TurnPayload): Promise<TurnResponse> {
    const response = await post<TurnResponse>(
      `/api/v1/conversations/${conversationId}/turns`,
      {
        text: payload.text ?? null,
        assetIds: payload.assetIds ?? [],
        action: payload.action ?? null,
      },
      { showError: false },
    );
    return unwrap(response);
  },

  async snapshot(conversationId: string): Promise<WorkspaceSnapshot> {
    const response = await get<WorkspaceSnapshot>(
      `/api/v1/conversations/${conversationId}/workspace`,
      undefined,
      { showError: false },
    );
    return unwrap(response);
  },
};
