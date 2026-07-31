import { describe, expect, it, vi } from 'vitest';
import { RequestError } from '../src/services/httpRequest';
import {
  WorkspaceMutationQueue,
  createDebouncedWorkspaceWriter,
  type WorkspaceState,
} from '../src/services/workspaceService';

const state = (version: number, prompt = ''): WorkspaceState => ({
  selectedProjectId: null,
  selectedReferenceVersionId: null,
  selectedReferenceAssetId: null,
  activeScenario: 'design',
  activeWorkflowStage: 'design',
  activeStepIndex: 0,
  viewMode: 'preview3d',
  sceneMode: 'poster',
  selectedIndustry: '',
  generationPrompt: prompt,
  stateData: {},
  version,
});

describe('workspace mutation queue', () => {
  it('serializes writes and uses the latest server version for every mutation', async () => {
    let concurrent = 0;
    let maxConcurrent = 0;
    const update = vi.fn(async (payload: { version: number; generationPrompt: string }) => {
      concurrent += 1;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      await Promise.resolve();
      concurrent -= 1;
      return state(payload.version + 1, payload.generationPrompt);
    });
    const queue = new WorkspaceMutationQueue(state(1), update, vi.fn());

    await Promise.all([
      queue.enqueue({ generationPrompt: 'first' }),
      queue.enqueue({ generationPrompt: 'second' }),
    ]);

    expect(maxConcurrent).toBe(1);
    expect(update.mock.calls.map(([payload]) => payload.version)).toEqual([1, 2]);
    expect(queue.current.generationPrompt).toBe('second');
  });

  it('reloads, merges and retries once after a 409 conflict', async () => {
    const update = vi.fn()
      .mockRejectedValueOnce(new RequestError('conflict', 409))
      .mockResolvedValueOnce(state(8, 'mine'));
    const reload = vi.fn().mockResolvedValue(state(7, 'server'));
    const queue = new WorkspaceMutationQueue(state(3), update, reload);

    await queue.enqueue({ generationPrompt: 'mine' });

    expect(reload).toHaveBeenCalledOnce();
    expect(update.mock.calls[1]?.[0]).toMatchObject({
      version: 7,
      generationPrompt: 'mine',
    });
  });

  it('merges queued stateData patches onto the latest persisted state', async () => {
    const initial = state(1);
    initial.stateData = { inputMode: 'prompt' };
    const update = vi.fn(async (payload) => ({
      ...payload,
      version: payload.version + 1,
    }));
    const queue = new WorkspaceMutationQueue(initial, update, vi.fn());

    await Promise.all([
      queue.enqueue({ stateData: { importedCadAssetId: 'asset-1' } }),
      queue.enqueue({ stateData: { previewVersionId: 'version-2' } }),
    ]);

    expect(queue.current.stateData).toEqual({
      inputMode: 'prompt',
      importedCadAssetId: 'asset-1',
      previewVersionId: 'version-2',
    });
  });

  it('debounces prompt drafts with last-write-wins', async () => {
    vi.useFakeTimers();
    const update = vi.fn(async (payload) => state(payload.version + 1, payload.generationPrompt));
    const queue = new WorkspaceMutationQueue(state(1), update, vi.fn());
    const writer = createDebouncedWorkspaceWriter(queue, 200);

    const first = writer.write('a');
    const second = writer.write('ab');
    const third = writer.write('abc');
    await vi.advanceTimersByTimeAsync(200);

    await expect(first).resolves.toBeNull();
    await expect(second).resolves.toBeNull();
    await expect(third).resolves.toMatchObject({ generationPrompt: 'abc' });
    expect(update).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });
});
