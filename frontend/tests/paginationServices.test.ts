import { beforeEach, describe, expect, it, vi } from 'vitest';

const requestMock = vi.hoisted(() => vi.fn());

vi.mock('../src/services/httpRequest', () => ({
  request: requestMock,
}));

import { assetService, type AssetRecord } from '../src/services/assetService';
import { cocreationHistoryService } from '../src/services/cocreationHistoryService';
import type {
  ProjectRecord,
  VersionSnapshot,
} from '../src/components/CoCreationAgentWorkspace.types';

describe('paginated database readers', () => {
  beforeEach(() => {
    requestMock.mockReset();
  });

  it('reads every asset page until total is reached', async () => {
    const records = Array.from({ length: 201 }, (_, index) => (
      { id: `asset-${index}` } as AssetRecord
    ));
    requestMock
      .mockResolvedValueOnce({ data: { items: records.slice(0, 200), total: 201 } })
      .mockResolvedValueOnce({ data: { items: records.slice(200), total: 201 } });

    const result = await assetService.listAll({ library: true });

    expect(result.items).toHaveLength(201);
    expect(requestMock.mock.calls.map(([config]) => config.params.offset)).toEqual([0, 200]);
  });

  it('reads and merges every history project page', async () => {
    const projectA = { id: 'project-a' } as ProjectRecord;
    const projectB = { id: 'project-b' } as ProjectRecord;
    const snapshotA = { id: 'version-a' } as VersionSnapshot;
    const snapshotB = { id: 'version-b' } as VersionSnapshot;
    requestMock
      .mockResolvedValueOnce({
        data: { projects: [projectA], snapshots: [snapshotA], total: 2 },
      })
      .mockResolvedValueOnce({
        data: { projects: [projectB], snapshots: [snapshotB], total: 2 },
      });

    const response = await cocreationHistoryService.listAllHistory(1);

    expect(response.data.projects).toEqual([projectA, projectB]);
    expect(response.data.snapshots).toEqual([snapshotA, snapshotB]);
    expect(requestMock.mock.calls.map(([config]) => config.params.offset)).toEqual([0, 1]);
  });
});
