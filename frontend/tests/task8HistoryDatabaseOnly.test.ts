import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const frontendRoot = path.resolve(__dirname, '..');

describe('Task8 database-only history frontend', () => {
  it('does not expose local import or browser persistence in history code', () => {
    const service = fs.readFileSync(
      path.join(frontendRoot, 'src/services/cocreationHistoryService.ts'),
      'utf8',
    );
    const page = fs.readFileSync(
      path.join(frontendRoot, 'src/components/CoCreationHistoryPage.tsx'),
      'utf8',
    );

    expect(service).not.toContain('importLocalHistory');
    expect(service).not.toContain('/import-local');
    expect(page).not.toContain('importLocalHistory');
    expect(page).not.toMatch(/localStorage|sessionStorage|indexedDB/i);
  });
});
