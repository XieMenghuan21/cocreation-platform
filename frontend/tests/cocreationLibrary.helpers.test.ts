import { describe, expect, it } from 'vitest';

import {
  buildAssetsFromVersion,
  getProjectVersionCount,
  getVersionsForProject,
  groupSnapshotsByProject,
  normalizeVersionSnapshots,
} from '../src/components/CoCreationAgentWorkspace.helpers';
import type { VersionSnapshot } from '../src/components/CoCreationAgentWorkspace.types';

const legacySnapshot = (): VersionSnapshot => ({
  id: 'V1.0',
  label: '泵体支架',
  status: '已完成',
  note: 'legacy snapshot',
  sourceObject: '泵体支架',
  createdAt: '2026-07-16T10:00:00.000Z',
  previewImageUrl: 'https://example.com/pump.png',
  prompt: 'legacy prompt',
});

describe('normalizeVersionSnapshots', () => {
  it('fills project metadata for legacy snapshots', () => {
    const [snapshot] = normalizeVersionSnapshots([legacySnapshot()]);

    expect(snapshot.projectId).toBe('泵体支架');
    expect(snapshot.projectName).toBe('泵体支架');
    expect(snapshot.versionNumber).toBe(1);
    expect(snapshot.isFinalized).toBe(true);
    expect(snapshot.sourceProjectId).toBe('泵体支架');
  });

  it('preserves explicit project metadata and sorts newest first', () => {
    const normalized = normalizeVersionSnapshots([
      {
        ...legacySnapshot(),
        id: 'V1.0',
        createdAt: '2026-07-15T10:00:00.000Z',
        projectId: 'project-a',
        projectName: '项目 A',
        versionNumber: 1,
        isFinalized: false,
        sourceProjectId: 'seed-a',
      },
      {
        ...legacySnapshot(),
        id: 'V2.0',
        createdAt: '2026-07-16T10:00:00.000Z',
        projectId: 'project-a',
        projectName: '项目 A',
        versionNumber: 2,
        isFinalized: true,
        sourceProjectId: 'seed-a',
      },
    ]);

    expect(normalized.map((item) => item.id)).toEqual(['V2.0', 'V1.0']);
    expect(normalized[0]?.projectId).toBe('project-a');
    expect(normalized[0]?.sourceProjectId).toBe('seed-a');
  });
});

describe('groupSnapshotsByProject', () => {
  it('groups by project id and exposes latest version', () => {
    const groups = groupSnapshotsByProject(
      normalizeVersionSnapshots([
        {
          ...legacySnapshot(),
          id: 'V1.0',
          projectId: 'project-a',
          projectName: '项目 A',
          versionNumber: 1,
          createdAt: '2026-07-15T10:00:00.000Z',
        },
        {
          ...legacySnapshot(),
          id: 'V2.0',
          projectId: 'project-a',
          projectName: '项目 A',
          versionNumber: 2,
          createdAt: '2026-07-16T10:00:00.000Z',
        },
        {
          ...legacySnapshot(),
          id: 'V1.0-B',
          label: '项目 B',
          sourceObject: '项目 B',
          projectId: 'project-b',
          projectName: '项目 B',
          versionNumber: 1,
          createdAt: '2026-07-14T10:00:00.000Z',
        },
      ]),
    );

    expect(groups).toHaveLength(2);
    expect(groups[0]?.project.id).toBe('project-a');
    expect(groups[0]?.latestVersion?.id).toBe('V2.0');
    expect(groups[0]?.versions.map((item) => item.id)).toEqual(['V2.0', 'V1.0']);
  });
});

describe('project selectors', () => {
  const snapshots = normalizeVersionSnapshots([
    {
      ...legacySnapshot(),
      id: 'V1.0',
      projectId: 'project-a',
      projectName: '项目 A',
      versionNumber: 1,
      createdAt: '2026-07-15T10:00:00.000Z',
    },
    {
      ...legacySnapshot(),
      id: 'V2.0',
      projectId: 'project-a',
      projectName: '项目 A',
      versionNumber: 2,
      createdAt: '2026-07-16T10:00:00.000Z',
    },
    {
      ...legacySnapshot(),
      id: 'V3.0',
      projectId: 'project-b',
      projectName: '项目 B',
      versionNumber: 3,
      createdAt: '2026-07-17T10:00:00.000Z',
    },
  ]);

  it('returns versions only from the current project', () => {
    const versions = getVersionsForProject(snapshots, 'project-a', '项目 A');

    expect(versions.map((item) => item.id)).toEqual(['V2.0', 'V1.0']);
  });

  it('counts versions only from the matching project', () => {
    expect(getProjectVersionCount(snapshots, 'project-a', '项目 A')).toBe(2);
    expect(getProjectVersionCount(snapshots, 'project-b', '项目 B')).toBe(1);
  });
});

describe('buildAssetsFromVersion', () => {
  it('builds finalized image and prompt assets with project metadata', () => {
    const [imageAsset, promptAsset] = buildAssetsFromVersion(
      normalizeVersionSnapshots([
        {
          ...legacySnapshot(),
          projectId: 'project-a',
          projectName: '项目 A',
          versionNumber: 7,
          sourceProjectId: 'seed-a',
          isFinalized: true,
        },
      ])[0]!,
      (() => {
        let index = 0;
        return () => `asset-${++index}`;
      })(),
    );

    expect(imageAsset).toMatchObject({
      id: 'asset-1',
      kind: 'image',
      projectId: 'project-a',
      projectName: '项目 A',
      versionNumber: 7,
      isFinalized: true,
      sourceProjectId: 'seed-a',
      sourceProjectName: '项目 A',
      sourceVersionId: 'V1.0',
    });
    expect(promptAsset).toMatchObject({
      id: 'asset-2',
      kind: 'prompt',
      projectId: 'project-a',
      projectName: '项目 A',
      versionNumber: 7,
      prompt: 'legacy prompt',
      isFinalized: true,
      sourceProjectId: 'seed-a',
    });
  });
});
