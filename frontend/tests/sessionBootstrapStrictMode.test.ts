import { describe, expect, it, vi } from 'vitest';
import {
  SessionBootstrapController,
  isTrustedParentMessage,
} from '../src/services/sessionBootstrap';
import type { SessionUser } from '../src/services/sessionService';

const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
};

describe('StrictMode session bootstrap', () => {
  it('lets the second setup receive one single-flight exchange result', async () => {
    const exchange = vi.fn().mockResolvedValue({ username: 'ignored' });
    const me = vi.fn().mockResolvedValue({ username: 'alice' });
    const controller = new SessionBootstrapController({ exchange, me });
    const first = vi.fn();
    const second = vi.fn();

    const unsubscribeFirst = controller.subscribeBootstrap('url-token', first);
    unsubscribeFirst();
    controller.subscribeBootstrap(null, second);
    await controller.whenIdle();

    expect(exchange).toHaveBeenCalledOnce();
    expect(me).toHaveBeenCalledOnce();
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith({ username: 'alice' });
  });

  it('serializes iframe exchange and prevents an older me result overwriting it', async () => {
    const oldMe = deferred<SessionUser>();
    const me = vi.fn()
      .mockImplementationOnce(() => oldMe.promise)
      .mockResolvedValueOnce({ username: 'new-user' });
    const exchange = vi.fn().mockResolvedValue({ username: 'exchange' });
    const controller = new SessionBootstrapController({ exchange, me });
    const users: string[] = [];

    controller.subscribeBootstrap(null, (user) => users.push(user.username));
    controller.exchangeFromParent('iframe-token', (user) => users.push(user.username));
    oldMe.resolve({ username: 'old-user' });
    await controller.whenIdle();

    expect(exchange).toHaveBeenCalledOnce();
    expect(users).toEqual(['new-user']);
  });

  it('accepts iframe messages only from the configured parent origin and window', () => {
    const parent = {} as Window;
    expect(isTrustedParentMessage(
      { origin: 'https://parent.example', source: parent },
      'https://parent.example',
      parent,
    )).toBe(true);
    expect(isTrustedParentMessage(
      { origin: 'https://evil.example', source: parent },
      'https://parent.example',
      parent,
    )).toBe(false);
    expect(isTrustedParentMessage(
      { origin: 'https://parent.example', source: {} as Window },
      'https://parent.example',
      parent,
    )).toBe(false);
    expect(isTrustedParentMessage(
      { origin: 'https://parent.example', source: parent },
      '',
      parent,
    )).toBe(false);
  });
});
