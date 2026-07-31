import { RequestError, request } from './httpRequest';
import type { AssetRecord } from './assetService';

export interface WorkspaceProjectDraftState {
  name: string;
  industry: string;
  description: string;
}

export interface WorkspaceStateData {
  importedCadAssetId?: string | null;
  importedCadPreviewAssetId?: string | null;
  selectedAssetId?: string | null;
  projectDraft?: WorkspaceProjectDraftState;
  inputMode?: string;
  editingVersionId?: string;
  previewVersionId?: string;
}

export interface WorkspaceState {
  selectedProjectId: string | null;
  selectedReferenceVersionId: string | null;
  selectedReferenceAssetId: string | null;
  activeScenario: string;
  activeWorkflowStage: string;
  activeStepIndex: number;
  viewMode: string;
  sceneMode: string;
  selectedIndustry: string;
  generationPrompt: string;
  stateData: WorkspaceStateData;
  version: number;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export type WorkspaceUpdate = Omit<WorkspaceState, 'createdAt' | 'updatedAt'>;
export type WorkspacePatch = Partial<Omit<WorkspaceUpdate, 'version'>>;

type WorkspaceUpdater = (payload: WorkspaceUpdate) => Promise<WorkspaceState>;
type WorkspaceReloader = () => Promise<WorkspaceState>;

const toWorkspaceUpdate = (
  current: WorkspaceState,
  patch: WorkspacePatch,
): WorkspaceUpdate => {
  const { stateData: patchStateData, ...restPatch } = patch;
  return {
    selectedProjectId: current.selectedProjectId,
    selectedReferenceVersionId: current.selectedReferenceVersionId,
    selectedReferenceAssetId: current.selectedReferenceAssetId,
    activeScenario: current.activeScenario,
    activeWorkflowStage: current.activeWorkflowStage,
    activeStepIndex: current.activeStepIndex,
    viewMode: current.viewMode,
    sceneMode: current.sceneMode,
    selectedIndustry: current.selectedIndustry,
    generationPrompt: current.generationPrompt,
    stateData: patchStateData
      ? { ...current.stateData, ...patchStateData }
      : current.stateData,
    version: current.version,
    ...restPatch,
  };
};

export class WorkspaceMutationQueue {
  private tail: Promise<void> = Promise.resolve();
  private latest: WorkspaceState;

  constructor(
    initial: WorkspaceState,
    private readonly update: WorkspaceUpdater,
    private readonly reload: WorkspaceReloader,
  ) {
    this.latest = initial;
  }

  get current(): WorkspaceState {
    return this.latest;
  }

  replaceCurrent(state: WorkspaceState): void {
    this.latest = state;
  }

  enqueue(patch: WorkspacePatch): Promise<WorkspaceState> {
    const pending = this.tail.then(() => this.execute(patch));
    this.tail = pending.then(
      () => undefined,
      () => undefined,
    );
    return pending;
  }

  private async execute(patch: WorkspacePatch): Promise<WorkspaceState> {
    try {
      this.latest = await this.update(toWorkspaceUpdate(this.latest, patch));
      return this.latest;
    } catch (error) {
      if (!(error instanceof RequestError) || error.code !== 409) {
        throw error;
      }
      this.latest = await this.reload();
      this.latest = await this.update(toWorkspaceUpdate(this.latest, patch));
      return this.latest;
    }
  }
}

export interface DebouncedWorkspaceWriter {
  write(prompt: string): Promise<WorkspaceState | null>;
  cancel(): void;
}

export function createDebouncedWorkspaceWriter(
  queue: WorkspaceMutationQueue,
  delayMs: number,
): DebouncedWorkspaceWriter {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pendingResolve: ((value: WorkspaceState | null) => void) | null = null;

  const cancelPending = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    pendingResolve?.(null);
    pendingResolve = null;
  };

  return {
    write(prompt: string): Promise<WorkspaceState | null> {
      cancelPending();
      return new Promise<WorkspaceState | null>((resolve, reject) => {
        pendingResolve = resolve;
        timer = setTimeout(() => {
          timer = null;
          pendingResolve = null;
          void queue.enqueue({ generationPrompt: prompt }).then(resolve, reject);
        }, delayMs);
      });
    },
    cancel: cancelPending,
  };
}

export interface WorkspaceAssetValidationResult {
  importedAsset: AssetRecord | null;
  previewAsset: AssetRecord | null;
  selectedAsset: AssetRecord | null;
  cleanup: WorkspaceUpdate | null;
}

const isMissingAssetError = (error: unknown): boolean =>
  error instanceof RequestError && (error.code === 404 || error.code === 410);

export async function validateWorkspaceAssetReferences(
  workspace: WorkspaceState,
  getAsset: (assetId: string) => Promise<AssetRecord>,
): Promise<WorkspaceAssetValidationResult> {
  const referenceIds = [
    workspace.selectedReferenceAssetId,
    workspace.stateData.selectedAssetId,
    workspace.stateData.importedCadAssetId,
    workspace.stateData.importedCadPreviewAssetId,
  ].filter((value): value is string => Boolean(value));
  const uniqueIds = Array.from(new Set(referenceIds));
  const settled = await Promise.allSettled(
    uniqueIds.map(async (assetId) => [assetId, await getAsset(assetId)] as const),
  );
  const available = new Map<string, AssetRecord>();
  const missing = new Set<string>();

  settled.forEach((result, index) => {
    const assetId = uniqueIds[index];
    if (result.status === 'fulfilled') {
      available.set(result.value[0], result.value[1]);
      return;
    }
    if (isMissingAssetError(result.reason)) {
      missing.add(assetId);
      return;
    }
    throw result.reason;
  });

  const selectedAssetId = workspace.stateData.selectedAssetId;
  const importedAssetId = workspace.stateData.importedCadAssetId;
  const previewAssetId = workspace.stateData.importedCadPreviewAssetId;
  const cleanup = missing.size > 0
    ? {
        selectedProjectId: workspace.selectedProjectId,
        selectedReferenceVersionId: workspace.selectedReferenceVersionId,
        selectedReferenceAssetId:
          workspace.selectedReferenceAssetId
          && missing.has(workspace.selectedReferenceAssetId)
            ? null
            : workspace.selectedReferenceAssetId,
        activeScenario: workspace.activeScenario,
        activeWorkflowStage: workspace.activeWorkflowStage,
        activeStepIndex: workspace.activeStepIndex,
        viewMode: workspace.viewMode,
        sceneMode: workspace.sceneMode,
        selectedIndustry: workspace.selectedIndustry,
        generationPrompt: workspace.generationPrompt,
        stateData: {
          ...workspace.stateData,
          selectedAssetId:
            selectedAssetId && missing.has(selectedAssetId) ? null : selectedAssetId,
          importedCadAssetId:
            importedAssetId && missing.has(importedAssetId) ? null : importedAssetId,
          importedCadPreviewAssetId:
            previewAssetId && missing.has(previewAssetId) ? null : previewAssetId,
        },
        version: workspace.version,
      }
    : null;

  return {
    selectedAsset: selectedAssetId ? available.get(selectedAssetId) ?? null : null,
    importedAsset: importedAssetId ? available.get(importedAssetId) ?? null : null,
    previewAsset: previewAssetId ? available.get(previewAssetId) ?? null : null,
    cleanup,
  };
}

export async function persistBeforeCommit<T>(
  mutation: () => Promise<T>,
  commit: (persisted: T) => void,
): Promise<T> {
  const persisted = await mutation();
  commit(persisted);
  return persisted;
}

export const workspaceService = {
  async get(): Promise<WorkspaceState> {
    const response = await request<WorkspaceState>({
      url: '/api/v1/workspace',
      method: 'GET',
      showError: false,
    });
    return response.data;
  },

  async update(payload: WorkspaceUpdate): Promise<WorkspaceState> {
    const response = await request<WorkspaceState>({
      url: '/api/v1/workspace',
      method: 'PUT',
      data: payload,
      showError: false,
      cancelDuplicate: false,
    });
    return response.data;
  },

  async setReference(
    projectId: string | null,
    versionId: string,
  ): Promise<WorkspaceState> {
    const response = await request<WorkspaceState>({
      url: '/api/v1/workspace/reference',
      method: 'PUT',
      data: { projectId, versionId },
      showError: false,
      cancelDuplicate: false,
    });
    return response.data;
  },
};
