import {
  sessionService,
  type SessionUser,
} from './sessionService';

export interface SessionBootstrapDependencies {
  exchange: (token: string) => Promise<SessionUser>;
  me: () => Promise<SessionUser>;
}

export interface MessageIdentity {
  origin: string;
  source: MessageEventSource | null;
}

export const configuredSsoParentOrigin = (
  import.meta.env.VITE_SSO_PARENT_ORIGIN ?? ''
).trim();

export function isTrustedParentMessage(
  event: MessageIdentity,
  configuredOrigin: string,
  parentWindow: Window,
): boolean {
  return Boolean(configuredOrigin)
    && event.origin === configuredOrigin
    && event.source === parentWindow;
}

export function consumeUrlSessionToken(): string | null {
  const url = new URL(window.location.href);
  const token =
    url.searchParams.get('platform_token')
    ?? url.searchParams.get('sso_token');
  if (!token) return null;
  url.searchParams.delete('platform_token');
  url.searchParams.delete('sso_token');
  window.history.replaceState(
    window.history.state,
    '',
    `${url.pathname}${url.search}${url.hash}`,
  );
  return token;
}

export class SessionBootstrapController {
  private initialPromise: Promise<SessionUser> | null = null;
  private operationChain: Promise<unknown> = Promise.resolve();
  private generation = 0;

  constructor(private readonly dependencies: SessionBootstrapDependencies) {}

  subscribeBootstrap(
    token: string | null,
    listener: (user: SessionUser) => void,
    onError?: (error: unknown) => void,
  ): () => void {
    let active = true;
    const subscribedGeneration = this.generation;
    if (!this.initialPromise) {
      this.initialPromise = token
        ? this.dependencies.exchange(token).then(() => this.dependencies.me())
        : this.dependencies.me();
      this.operationChain = this.initialPromise.catch(() => undefined);
    }
    void this.initialPromise
      .then((user) => {
        if (active && subscribedGeneration === this.generation) listener(user);
      })
      .catch((error: unknown) => {
        if (active && subscribedGeneration === this.generation) onError?.(error);
      });
    return () => {
      active = false;
    };
  }

  exchangeFromParent(
    token: string,
    listener: (user: SessionUser) => void,
    onError?: (error: unknown) => void,
  ): void {
    const exchangeGeneration = ++this.generation;
    const operation = this.operationChain.then(async () => {
      await this.dependencies.exchange(token);
      return this.dependencies.me();
    });
    this.operationChain = operation.catch(() => undefined);
    void operation
      .then((user) => {
        if (exchangeGeneration === this.generation) listener(user);
      })
      .catch((error: unknown) => {
        if (exchangeGeneration === this.generation) onError?.(error);
      });
  }

  async whenIdle(): Promise<void> {
    await this.operationChain;
  }
}

export const sessionBootstrap = new SessionBootstrapController({
  exchange: (token) => sessionService.exchange(token),
  me: () => sessionService.me(),
});
