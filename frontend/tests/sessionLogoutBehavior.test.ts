import { describe, expect, it, vi } from 'vitest';
import { runLogout } from '../src/services/sessionService';

describe('session logout behavior', () => {
  it('clears React authentication only after backend logout succeeds', async () => {
    const clear = vi.fn();
    await expect(
      runLogout(vi.fn().mockResolvedValue(undefined), clear),
    ).resolves.toBeNull();
    expect(clear).toHaveBeenCalledOnce();
  });

  it('keeps React authentication and returns a stable retryable error on failure', async () => {
    const clear = vi.fn();
    await expect(
      runLogout(vi.fn().mockRejectedValue(new Error('database unavailable')), clear),
    ).resolves.toBe('退出失败，请重试。');
    expect(clear).not.toHaveBeenCalled();
  });
});
