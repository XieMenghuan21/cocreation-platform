import { beforeEach, describe, expect, it, vi } from 'vitest';

const requestMock = vi.hoisted(() => vi.fn());

vi.mock('../src/services/httpRequest', () => ({
  request: requestMock,
}));

import * as historyServiceModule from '../src/services/cocreationHistoryService';

type Callable = (...args: unknown[]) => Promise<unknown>;

function serviceMethod(name: string): Callable {
  const service = historyServiceModule.cocreationHistoryService as unknown as Record<
    string,
    unknown
  >;
  const method = service[name];
  expect(typeof method).toBe('function');
  return method as Callable;
}

describe('cocreation history database API actions', () => {
  beforeEach(() => {
    requestMock.mockReset();
    requestMock.mockResolvedValue({ data: {} });
  });

  it('calls the real publish, workspace reference, delete and library endpoints', async () => {
    await serviceMethod('publishVersion')('project/a', 'version/1');
    await serviceMethod('setWorkspaceReference')('project/a', 'version/1');
    await serviceMethod('deleteVersion')('project/a', 'version/1');
    await serviceMethod('listAssetLibrary')();
    await serviceMethod('getWorkspace')();

    expect(requestMock).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        url: '/api/v1/cocreation-history/projects/project%2Fa/versions/version%2F1/publish',
        method: 'POST',
      }),
    );
    expect(requestMock).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        url: '/api/v1/workspace/reference',
        method: 'PUT',
        data: { projectId: 'project/a', versionId: 'version/1' },
      }),
    );
    expect(requestMock).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({
        url: '/api/v1/cocreation-history/projects/project%2Fa/versions/version%2F1',
        method: 'DELETE',
      }),
    );
    expect(requestMock).toHaveBeenNthCalledWith(
      4,
      expect.objectContaining({
        url: '/api/v1/assets',
        method: 'GET',
        params: { library: true, limit: 200, offset: 0 },
      }),
    );
    expect(requestMock).toHaveBeenNthCalledWith(
      5,
      expect.objectContaining({
        url: '/api/v1/workspace',
        method: 'GET',
      }),
    );
  });

  it('refreshes only after a successful mutation', async () => {
    const runMutationAndRefresh = (
      historyServiceModule as unknown as Record<string, unknown>
    ).runHistoryMutationAndRefresh;
    expect(typeof runMutationAndRefresh).toBe('function');
    const refresh = vi.fn().mockResolvedValue(undefined);
    const mutation = vi.fn().mockResolvedValue(undefined);

    await (runMutationAndRefresh as Callable)(mutation, refresh);

    expect(mutation).toHaveBeenCalledOnce();
    expect(refresh).toHaveBeenCalledOnce();
    expect(mutation.mock.invocationCallOrder[0]).toBeLessThan(
      refresh.mock.invocationCallOrder[0],
    );
  });

  it('propagates mutation failures and does not refresh', async () => {
    const runMutationAndRefresh = (
      historyServiceModule as unknown as Record<string, unknown>
    ).runHistoryMutationAndRefresh;
    expect(typeof runMutationAndRefresh).toBe('function');
    const refresh = vi.fn().mockResolvedValue(undefined);
    const mutation = vi.fn().mockRejectedValue(new Error('publish failed'));

    await expect(
      (runMutationAndRefresh as Callable)(mutation, refresh),
    ).rejects.toThrow('publish failed');
    expect(refresh).not.toHaveBeenCalled();
  });
});
