import { afterEach, describe, expect, it, vi } from 'vitest';
import { RequestError, request } from '../src/services/httpRequest';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('http response error handling', () => {
  it('preserves status and text for non-JSON error responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('gateway down', { status: 502, statusText: 'Bad Gateway' }),
    ));

    await expect(request({ url: '/failure', showError: false })).rejects.toMatchObject({
      code: 502,
      message: 'gateway down',
    } satisfies Partial<RequestError>);
  });

  it('preserves status when an error body contains malformed JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('{"broken":', {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }),
    ));

    await expect(request({ url: '/failure', showError: false })).rejects.toMatchObject({
      code: 500,
    } satisfies Partial<RequestError>);
  });

  it('rejects a 2xx envelope that explicitly reports success false', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      Response.json({
        code: 200,
        success: false,
        message: 'business rejected',
        data: null,
      }),
    ));

    await expect(request({ url: '/failure', showError: false })).rejects.toThrow(
      'business rejected',
    );
  });
});
