import { request } from './httpRequest';

export interface SessionUser {
  username: string;
  displayName?: string;
}

export const LOGOUT_RETRY_MESSAGE = '退出失败，请重试。';

export async function runLogout(
  logout: () => Promise<void>,
  clearReactAuth: () => void,
): Promise<string | null> {
  try {
    await logout();
    clearReactAuth();
    return null;
  } catch {
    return LOGOUT_RETRY_MESSAGE;
  }
}

interface PasswordLoginPayload {
  username: string;
  password: string;
  auth_source: 'local';
}

interface SessionWireUser {
  username: string;
  displayName?: string;
  display_name?: string;
}

const normalizeUser = (user: SessionWireUser): SessionUser => ({
  username: user.username,
  displayName: user.displayName ?? user.display_name,
});

export const sessionService = {
  async me(): Promise<SessionUser> {
    const response = await request<SessionWireUser>({
      url: '/api/v1/auth/me',
      method: 'GET',
      showError: false,
    });
    return normalizeUser(response.data);
  },

  async login(username: string, password: string): Promise<SessionUser> {
    const payload: PasswordLoginPayload = {
      username,
      password,
      auth_source: 'local',
    };
    const response = await request<SessionWireUser>({
      url: '/api/v1/auth/login/password',
      method: 'POST',
      data: payload,
      showError: false,
      cancelDuplicate: false,
    });
    return normalizeUser(response.data);
  },

  async exchange(platformToken: string): Promise<SessionUser> {
    const response = await request<SessionWireUser>({
      url: '/api/v1/auth/exchange',
      method: 'POST',
      data: { platform_token: platformToken },
      showError: false,
      cancelDuplicate: false,
    });
    return normalizeUser(response.data);
  },

  async logout(): Promise<void> {
    await request<null>({
      url: '/api/v1/auth/logout',
      method: 'POST',
      showError: false,
      cancelDuplicate: false,
    });
  },
};
