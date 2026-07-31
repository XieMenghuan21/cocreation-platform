/**
 * API配置中心 - 共创平台独立版
 */

const normalizeApiBaseUrl = (value?: string): string => {
  const normalized = String(value ?? '').trim();
  if (!normalized) return '';
  return normalized.replace(/\/+$/, '');
};

const getApiBaseUrl = (): string => {
  const configuredApiBaseUrl = normalizeApiBaseUrl(
    import.meta.env.VITE_API_BASE_URL
  );
  if (import.meta.env.DEV) {
    return configuredApiBaseUrl || '';
  }
  return configuredApiBaseUrl;
};

export const API_BASE_URL = getApiBaseUrl();

export const ensureHttps = (url: string): string => {
  if (!url) return url;
  if (url.startsWith('/') || url.startsWith('./') || url.startsWith('../')) {
    return url;
  }
  return url;
};
