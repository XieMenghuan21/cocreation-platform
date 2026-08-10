import { describe, expect, it } from 'vitest';

import {
  buildProjectLinkedPath,
  buildWorkspaceViewPath,
} from '../src/components/workspace/workspaceViewNavigation';

describe('workspace view navigation', () => {
  it('navigates from a conversation url to project archive view', () => {
    const result = buildWorkspaceViewPath({
      pathname: '/workspace/ad9ea2b9-4772-40d2-9da6-d62fd7d05ca8',
      search: '?name=Hello+Kitty&project=HelloKitty',
      next: 'projects',
    });

    expect(result).toBe('/workspace/ad9ea2b9-4772-40d2-9da6-d62fd7d05ca8?name=Hello+Kitty&project=HelloKitty&view=projects');
  });

  it('returns to conversation without leaving stale archive project state', () => {
    const result = buildWorkspaceViewPath({
      pathname: '/workspace/ad9ea2b9-4772-40d2-9da6-d62fd7d05ca8',
      search: '?name=Hello+Kitty&project=HelloKitty&view=projects&archiveProject=p1',
      next: 'chat',
    });

    expect(result).toBe('/workspace/ad9ea2b9-4772-40d2-9da6-d62fd7d05ca8?name=Hello+Kitty&project=HelloKitty');
  });

  it('keeps resource view when a mounted workspace reports the active project', () => {
    const result = buildProjectLinkedPath({
      pathname: '/workspace/ad9ea2b9-4772-40d2-9da6-d62fd7d05ca8',
      search: '?name=Hello+Kitty&project=HelloKitty&view=projects',
      projectId: 'HelloKitty',
      projectName: 'Hello Kitty',
    });

    expect(result).toBe('/workspace/ad9ea2b9-4772-40d2-9da6-d62fd7d05ca8?name=Hello+Kitty&project=HelloKitty&view=projects');
  });
});
