import { describe, expect, it, vi } from 'vitest';
import { RequestError } from '../src/services/httpRequest';
import type { AssetRecord } from '../src/services/assetService';
import {
  validateWorkspaceAssetReferences,
  type WorkspaceState,
} from '../src/services/workspaceService';

const workspace = (): WorkspaceState => ({
  selectedProjectId: null,
  selectedReferenceVersionId: null,
  selectedReferenceAssetId: 'asset-201',
  activeScenario: 'design',
  activeWorkflowStage: 'design',
  activeStepIndex: 0,
  viewMode: 'preview3d',
  sceneMode: 'poster',
  selectedIndustry: '',
  generationPrompt: '',
  stateData: {
    selectedAssetId: 'asset-201',
    importedCadAssetId: 'asset-201',
    importedCadPreviewAssetId: 'preview-202',
  },
  version: 3,
});

const asset = (id: string): AssetRecord => ({
  id,
  userId: 'alice',
  projectId: null,
  versionId: null,
  sourceVersionId: null,
  taskId: null,
  kind: 'source',
  filename: `${id}.stl`,
  extension: 'stl',
  contentType: 'model/stl',
  sizeBytes: 16,
  sha256: 'hash',
  chunkSize: 16,
  chunkCount: 1,
  status: 'available',
  source: 'upload',
  metadata: {},
  createdAt: '2026-07-26T00:00:00Z',
  updatedAt: '2026-07-26T00:00:00Z',
});

describe('exact workspace asset reference validation', () => {
  it('restores a saved asset even when it is outside the first 200 list rows', async () => {
    const getAsset = vi.fn(async (id: string) => asset(id));

    const result = await validateWorkspaceAssetReferences(workspace(), getAsset);

    expect(getAsset).toHaveBeenCalledTimes(2);
    expect(result.importedAsset?.id).toBe('asset-201');
    expect(result.previewAsset?.id).toBe('preview-202');
    expect(result.cleanup).toBeNull();
  });

  it('clears only references that return 404 or 410', async () => {
    const getAsset = vi.fn(async (id: string) => {
      if (id === 'preview-202') {
        throw new RequestError('not found', 404);
      }
      return asset(id);
    });

    const result = await validateWorkspaceAssetReferences(workspace(), getAsset);

    expect(result.importedAsset?.id).toBe('asset-201');
    expect(result.previewAsset).toBeNull();
    expect(result.cleanup?.stateData).toMatchObject({
      selectedAssetId: 'asset-201',
      importedCadAssetId: 'asset-201',
      importedCadPreviewAssetId: null,
    });
    expect(result.cleanup?.selectedReferenceAssetId).toBe('asset-201');
  });

  it('validates the top-level reference asset independently from stateData', async () => {
    const persisted = workspace();
    persisted.selectedReferenceAssetId = 'reference-999';
    const getAsset = vi.fn(async (id: string) => {
      if (id === 'reference-999') {
        throw new RequestError('not found', 404);
      }
      return asset(id);
    });

    const result = await validateWorkspaceAssetReferences(persisted, getAsset);

    expect(getAsset).toHaveBeenCalledTimes(3);
    expect(getAsset).toHaveBeenCalledWith('reference-999');
    expect(result.cleanup?.selectedReferenceAssetId).toBeNull();
    expect(result.cleanup?.stateData.selectedAssetId).toBe('asset-201');
  });

  it('preserves all references and surfaces 5xx errors for retry', async () => {
    const getAsset = vi.fn(async () => {
      throw new RequestError('database unavailable', 500);
    });

    await expect(
      validateWorkspaceAssetReferences(workspace(), getAsset),
    ).rejects.toThrow('database unavailable');
    expect(workspace().stateData.importedCadAssetId).toBe('asset-201');
  });
});
