import { afterEach, describe, expect, it, vi } from 'vitest';
import { sessionService } from '../src/services/sessionService';

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('cookie session service', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('loads the current user with the HttpOnly session cookie', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        code: 200,
        data: { username: 'alice', displayName: 'Alice' },
        message: 'success',
        success: true,
      }),
    );

    await expect(sessionService.me()).resolves.toEqual({
      username: 'alice',
      displayName: 'Alice',
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/auth/me'),
      expect.objectContaining({ credentials: 'include' }),
    );
    expect(
      (fetchSpy.mock.calls[0]?.[1]?.headers as Headers).has('Authorization'),
    ).toBe(false);
  });

  it('exchanges a platform token once without returning it to JavaScript', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ username: 'alice', display_name: 'Alice' }),
    );

    await expect(sessionService.exchange('one-time-token')).resolves.toEqual({
      username: 'alice',
      displayName: 'Alice',
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/auth/exchange'),
      expect.objectContaining({
        credentials: 'include',
        body: JSON.stringify({ platform_token: 'one-time-token' }),
      }),
    );
  });
});
