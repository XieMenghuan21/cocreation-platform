import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

describe('workspace imported database asset restoration', () => {
  it('loads history and workspace, then validates saved assets by exact id', () => {
    const source = readFileSync(
      'src/components/CoCreationAgentWorkspace.tsx',
      'utf8',
    );
    expect(source).toMatch(
      /Promise\.all\(\[[\s\S]*listAllHistory\(\)[\s\S]*workspaceService\.get\(\)/,
    );
    expect(source).toContain('(assetId) => assetService.get(assetId)');
    expect(source).not.toContain('assetService.list({ limit: 200, offset: 0 })');
  });
});
