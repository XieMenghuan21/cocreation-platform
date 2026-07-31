import { describe, expect, it } from 'vitest';
import * as mapperModule from '../src/components/databaseAssetLibraryMapper';
import type { DatabaseAssetRecord } from '../src/services/cocreationHistoryService';

const mapDatabaseAssets = (
  mapperModule as unknown as Record<string, unknown>
).mapDatabaseAssets;

function record(
  id: string,
  filename: string,
  kind: string,
  contentType: string,
): DatabaseAssetRecord {
  return {
    id,
    projectId: 'project-a',
    versionId: 'v1',
    sourceVersionId: 'v1',
    kind,
    filename,
    contentType,
    metadata: {},
    createdAt: '2026-07-26T10:00:00Z',
  };
}

describe('database asset library mapping', () => {
  it('maps STEP, STL, JavaScript and PDF without misclassifying them as prompts', () => {
    expect(typeof mapDatabaseAssets).toBe('function');
    const mapped = (
      mapDatabaseAssets as (
        records: DatabaseAssetRecord[],
        snapshots: [],
      ) => Array<{ kind: string; downloadUrl?: string }>
    )(
      [
        record('step-id', 'part.step', 'cad', 'application/step'),
        record('stl-id', 'mesh.stl', 'model', 'model/stl'),
        record('js-id', 'design.js', 'script', 'text/javascript'),
        record('pdf-id', 'report.pdf', 'document', 'application/pdf'),
      ],
      [],
    );

    expect(mapped.map((item) => item.kind)).toEqual([
      'cad',
      'model',
      'script',
      'document',
    ]);
    expect(mapped.map((item) => item.downloadUrl)).toEqual([
      '/api/v1/assets/step-id/download',
      '/api/v1/assets/stl-id/download',
      '/api/v1/assets/js-id/download',
      '/api/v1/assets/pdf-id/download',
    ]);
    expect(mapped.some((item) => item.kind === 'prompt')).toBe(false);
  });

  it('maps only genuine prompt assets to prompt and preserves backend downloadUrl', () => {
    expect(typeof mapDatabaseAssets).toBe('function');
    const prompt = {
      ...record('prompt-id', 'prompt.txt', 'text', 'text/plain'),
      downloadUrl: '/signed/prompt-id',
      metadata: { prompt: '生成工业零件' },
    };
    const mapped = (
      mapDatabaseAssets as (
        records: DatabaseAssetRecord[],
        snapshots: [],
      ) => Array<{ kind: string; downloadUrl?: string; prompt?: string }>
    )([prompt], []);

    expect(mapped[0]).toMatchObject({
      kind: 'prompt',
      prompt: '生成工业零件',
      downloadUrl: '/signed/prompt-id',
    });
  });
});
