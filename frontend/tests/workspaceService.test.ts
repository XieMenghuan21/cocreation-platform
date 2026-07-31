import { beforeEach, describe, expect, it, vi } from 'vitest';

const requestMock = vi.hoisted(() => vi.fn());

vi.mock('../src/services/httpRequest', () => ({
  request: requestMock,
}));

import {
  persistBeforeCommit,
  workspaceService,
} from '../src/services/workspaceService';

describe('database workspace service', () => {
  beforeEach(() => {
    requestMock.mockReset();
  });

  it('sends the optimistic-lock version with the complete workspace state', async () => {
    requestMock.mockResolvedValue({ data: { version: 4 } });

    await workspaceService.update({
      selectedProjectId: 'project-1',
      selectedReferenceVersionId: null,
      selectedReferenceAssetId: null,
      activeScenario: 'design',
      activeWorkflowStage: 'design',
      activeStepIndex: 1,
      viewMode: 'preview3d',
      sceneMode: 'poster',
      selectedIndustry: '装备制造',
      generationPrompt: 'prompt',
      stateData: {},
      version: 3,
    });

    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/v1/workspace',
        method: 'PUT',
        data: expect.objectContaining({ version: 3, generationPrompt: 'prompt' }),
      }),
    );
  });

  it('updates React state only after the database mutation succeeds', async () => {
    const commit = vi.fn();
    const mutation = vi.fn().mockResolvedValue('persisted');

    await expect(persistBeforeCommit(mutation, commit)).resolves.toBe('persisted');
    expect(mutation.mock.invocationCallOrder[0]).toBeLessThan(
      commit.mock.invocationCallOrder[0],
    );
    expect(commit).toHaveBeenCalledWith('persisted');
  });

  it('does not update React state when persistence fails', async () => {
    const commit = vi.fn();
    const mutation = vi.fn().mockRejectedValue(new Error('database offline'));

    await expect(persistBeforeCommit(mutation, commit)).rejects.toThrow(
      'database offline',
    );
    expect(commit).not.toHaveBeenCalled();
  });
});
