import { describe, expect, it } from 'vitest';
import {
  buildCadAiGeneratedAssets,
  buildCadAiVersionSnapshot,
} from '../src/components/CoCreationAgentWorkspace.helpers';
import type { CadAiTaskStatus } from '../src/services/forgecadService';

const assetId = '123e4567-e89b-42d3-a456-426614174000';
const downloadUrl = `/api/v1/assets/${assetId}/download`;

describe('workflow output asset allowlist', () => {
  it('accepts only UUID asset ids and exact database download URLs', () => {
    const assets = buildCadAiGeneratedAssets({
      modelGlb: assetId,
      modelStl: downloadUrl,
      modelStep: '/tmp/private.step',
      renderPng: 'https://attacker.example/steal.png',
      drawingSvg: '/api/v1/assets/not-a-uuid/download',
    });

    expect(assets).toHaveLength(2);
    expect(assets.map((item) => item.downloadUrl)).toEqual([
      downloadUrl,
      downloadUrl,
    ]);
  });

  it('does not expose arbitrary output paths or remote URLs in snapshots', () => {
    const task = {
      taskId: 'task-1',
      projectId: 'project-1',
      status: 'completed',
      outputs: {
        modelGlb: 'https://attacker.example/model.glb',
        renderPng: '/tmp/render.png',
      },
    } as CadAiTaskStatus;

    const snapshot = buildCadAiVersionSnapshot(
      task,
      [],
      'project-1',
      'Project',
    );

    expect(snapshot.outputPath).toBeNull();
    expect(snapshot.downloadUrl).toBeUndefined();
    expect(snapshot.previewImageUrl).toBeNull();
    expect(snapshot.generatedAssets).toEqual([]);
  });
});
