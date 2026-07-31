import { describe, expect, it } from 'vitest';

import {
  workspacePreviewHeightClass,
  workspacePreviewImageFrameClass,
  workspacePreviewImageClass,
} from '../src/components/CoCreationAgentWorkspace.constants';

describe('workspace preview layout tokens', () => {
  it('uses a viewport-adaptive preview height', () => {
    expect(workspacePreviewHeightClass).toBe('h-[clamp(280px,calc(100vh-19rem),720px)]');
  });

  it('keeps the image frame bound to the adaptive preview height', () => {
    expect(workspacePreviewImageFrameClass).toContain('h-[calc(100%-2.25rem)]');
    expect(workspacePreviewImageFrameClass).toContain('min-h-0');
  });

  it('lets the preview image scale inside the available frame', () => {
    expect(workspacePreviewImageClass).toContain('max-h-full');
    expect(workspacePreviewImageClass).toContain('max-w-full');
    expect(workspacePreviewImageClass).toContain('object-contain');
  });
});
