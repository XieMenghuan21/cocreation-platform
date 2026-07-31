import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const sourceFiles = (directory: string): string[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory()
      ? sourceFiles(path)
      : /\.(ts|tsx)$/.test(entry.name)
        ? [path]
        : [];
  });

describe('database asset URLs', () => {
  it('contains no legacy ForgeCAD import file or preview URL', () => {
    const violations = sourceFiles('src').filter((file) =>
      /\/forgecad\/import\/|preview-file/.test(readFileSync(file, 'utf8')),
    );
    expect(violations).toEqual([]);
  });

  it('passes workflow inputs by asset id without a legacy asset URL', () => {
    const workspace = readFileSync(
      'src/components/CoCreationAgentWorkspace.tsx',
      'utf8',
    );
    expect(workspace).toContain('assetIds');
    expect(workspace).not.toMatch(/assetUrls\s*=\s*workflowAsset/);
  });
});
