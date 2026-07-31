import { API_BASE_URL, ensureHttps } from '../config/api';

export type HttpMethod =
  | 'GET'
  | 'POST'
  | 'PUT'
  | 'DELETE'
  | 'PATCH'
  | 'HEAD'
  | 'OPTIONS';

export type ResponseType = 'json' | 'text' | 'blob' | 'arrayBuffer' | 'formData';

export interface RequestConfig
  extends Omit<RequestInit, 'body' | 'headers' | 'method'> {
  baseURL?: string;
  url?: string;
  method?: HttpMethod;
  params?: Record<string, unknown>;
  data?: unknown;
  body?: BodyInit | null;
  timeout?: number;
  headers?: HeadersInit;
  responseType?: ResponseType;
  showError?: boolean;
  errorMessage?: string;
  retryCount?: number;
  retryDelay?: number;
  cancelDuplicate?: boolean;
}

export interface ApiResponse<T = unknown> {
  code: number;
  data: T;
  message: string;
  success: boolean;
  errorCode?: string | null;
  requestId?: string | null;
  timestamp?: number;
}

export class RequestError extends Error {
  constructor(
    message: string,
    public readonly code?: number,
    public readonly errorCode?: string,
    public readonly requestId?: string,
    public readonly response?: Response,
    public readonly config?: RequestConfig,
  ) {
    super(message);
    this.name = 'RequestError';
  }
}

interface ErrorEnvelope {
  detail?: unknown;
  message?: unknown;
  errorCode?: unknown;
  requestId?: unknown;
}

const DEFAULT_TIMEOUT = 30_000;
const DEFAULT_RETRY_DELAY = 1_000;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

const textValue = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value.trim() : undefined;

const extractErrorMessage = (payload: unknown, fallback: string): string => {
  const plainText = textValue(payload);
  if (plainText) return plainText;
  if (!isRecord(payload)) return fallback;
  const envelope = payload as ErrorEnvelope;
  const message = textValue(envelope.message);
  if (message) return message;
  if (typeof envelope.detail === 'string' && envelope.detail.trim()) {
    return envelope.detail.trim();
  }
  if (isRecord(envelope.detail)) {
    return textValue(envelope.detail.message) ?? fallback;
  }
  return fallback;
};

const parseResponseBody = async (
  response: Response,
  responseType: ResponseType,
): Promise<unknown> => {
  if (responseType === 'blob') return response.blob();
  if (responseType === 'arrayBuffer') return response.arrayBuffer();
  if (responseType === 'formData') return response.formData();
  if (responseType === 'text') return response.text();
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
};

class HttpRequest {
  private readonly pendingRequests = new Map<string, AbortController>();

  private buildUrl(config: RequestConfig): string {
    const path = config.url ?? '';
    const base = config.baseURL ?? API_BASE_URL;
    const resolved =
      /^https?:\/\//i.test(path)
        ? path
        : path.startsWith('/')
          ? `${base}${path}`
          : `${base}/${path}`;
    const url = ensureHttps(resolved);
    if (!config.params) return url;

    const search = new URLSearchParams();
    Object.entries(config.params).forEach(([key, value]) => {
      if (value === undefined || value === null) return;
      if (Array.isArray(value)) {
        value.forEach((item) => search.append(key, String(item)));
        return;
      }
      search.append(key, String(value));
    });
    const query = search.toString();
    return query ? `${url}${url.includes('?') ? '&' : '?'}${query}` : url;
  }

  private requestKey(config: RequestConfig): string {
    return `${config.method ?? 'GET'}:${config.url ?? ''}:${JSON.stringify(config.params)}:${JSON.stringify(config.data)}`;
  }

  private async execute<T>(config: RequestConfig): Promise<ApiResponse<T>> {
    const key = this.requestKey(config);
    if (config.method !== 'GET' && config.cancelDuplicate !== false) {
      this.pendingRequests.get(key)?.abort();
    }

    const controller = new AbortController();
    this.pendingRequests.set(key, controller);
    const timeoutId = globalThis.setTimeout(
      () => controller.abort(),
      config.timeout ?? DEFAULT_TIMEOUT,
    );

    const headers = new Headers(config.headers);
    headers.set('Accept', 'application/json');
    headers.set('X-Requested-With', 'XMLHttpRequest');
    const body =
      config.body ??
      (config.data instanceof FormData
        ? config.data
        : config.data === undefined
          ? undefined
          : JSON.stringify(config.data));
    if (!(body instanceof FormData) && body !== undefined && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    try {
      const response = await fetch(this.buildUrl(config), {
        ...config,
        method: config.method ?? 'GET',
        headers,
        body,
        credentials: 'include',
        signal: controller.signal,
      });
      const responseType = config.responseType ?? 'json';
      const parsed = await parseResponseBody(response, responseType);
      if (!response.ok) {
        const fallback = `HTTP ${response.status}: ${response.statusText}`;
        const envelope = isRecord(parsed) ? parsed : {};
        throw new RequestError(
          extractErrorMessage(parsed, fallback),
          response.status,
          textValue(envelope.errorCode),
          textValue(envelope.requestId) ??
            response.headers.get('X-Request-ID') ??
            undefined,
          response,
          config,
        );
      }

      if (isRecord(parsed) && typeof parsed.code === 'number' && 'data' in parsed) {
        const envelope: ApiResponse<T> = {
          code: parsed.code,
          data: parsed.data as T,
          message: textValue(parsed.message) ?? 'success',
          success:
            typeof parsed.success === 'boolean'
              ? parsed.success
              : parsed.code >= 200 && parsed.code < 300,
          errorCode: textValue(parsed.errorCode) ?? null,
          requestId:
            textValue(parsed.requestId) ??
            response.headers.get('X-Request-ID'),
        };
        if (!envelope.success) {
          throw new RequestError(
            envelope.message,
            envelope.code,
            envelope.errorCode ?? undefined,
            envelope.requestId ?? undefined,
            response,
            config,
          );
        }
        return envelope;
      }

      return {
        code: response.status,
        data: parsed as T,
        message: 'success',
        success: true,
        errorCode: null,
        requestId: response.headers.get('X-Request-ID'),
        timestamp: Date.now(),
      };
    } finally {
      globalThis.clearTimeout(timeoutId);
      this.pendingRequests.delete(key);
    }
  }

  async request<T = unknown>(config: RequestConfig): Promise<ApiResponse<T>> {
    const retries = config.retryCount ?? 0;
    let lastError: unknown;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        return await this.execute<T>(config);
      } catch (error) {
        lastError = error;
        if (attempt < retries) {
          await new Promise<void>((resolve) => {
            globalThis.setTimeout(resolve, (config.retryDelay ?? DEFAULT_RETRY_DELAY) * (attempt + 1));
          });
        }
      }
    }
    const requestError =
      lastError instanceof RequestError
        ? lastError
        : new RequestError(
            lastError instanceof Error ? lastError.message : '网络请求失败',
            undefined,
            undefined,
            undefined,
            undefined,
            config,
          );
    if (config.showError !== false) {
      console.error(config.errorMessage ?? requestError.message);
    }
    throw requestError;
  }

  get<T = unknown>(
    url: string,
    params?: Record<string, unknown>,
    config?: Omit<RequestConfig, 'url' | 'method' | 'params'>,
  ): Promise<ApiResponse<T>> {
    return this.request<T>({ ...config, url, method: 'GET', params });
  }

  post<T = unknown>(
    url: string,
    data?: unknown,
    config?: Omit<RequestConfig, 'url' | 'method' | 'data'>,
  ): Promise<ApiResponse<T>> {
    return this.request<T>({ ...config, url, method: 'POST', data });
  }

  put<T = unknown>(
    url: string,
    data?: unknown,
    config?: Omit<RequestConfig, 'url' | 'method' | 'data'>,
  ): Promise<ApiResponse<T>> {
    return this.request<T>({ ...config, url, method: 'PUT', data });
  }

  delete<T = unknown>(
    url: string,
    params?: Record<string, unknown>,
    config?: Omit<RequestConfig, 'url' | 'method' | 'params'>,
  ): Promise<ApiResponse<T>> {
    return this.request<T>({ ...config, url, method: 'DELETE', params });
  }

  patch<T = unknown>(
    url: string,
    data?: unknown,
    config?: Omit<RequestConfig, 'url' | 'method' | 'data'>,
  ): Promise<ApiResponse<T>> {
    return this.request<T>({ ...config, url, method: 'PATCH', data });
  }

  upload<T = unknown>(
    url: string,
    file: File,
    config?: Omit<RequestConfig, 'url' | 'method' | 'body'>,
  ): Promise<ApiResponse<T>> {
    const data = new FormData();
    data.append('file', file);
    return this.request<T>({ ...config, url, method: 'POST', data });
  }

  async download(
    url: string,
    filename?: string,
    config?: Omit<RequestConfig, 'url' | 'method' | 'responseType'>,
  ): Promise<void> {
    const response = await this.request<Blob>({
      ...config,
      url,
      method: 'GET',
      responseType: 'blob',
    });
    const objectUrl = URL.createObjectURL(response.data);
    try {
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename ?? url.split('/').pop() ?? 'download';
      document.body.appendChild(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  }

  cancelAllRequests(): void {
    this.pendingRequests.forEach((controller) => controller.abort());
    this.pendingRequests.clear();
  }
}

export const httpRequest = new HttpRequest();
export const request = <T = unknown>(
  config: RequestConfig,
): Promise<ApiResponse<T>> => httpRequest.request<T>(config);
export const get = <T = unknown>(
  url: string,
  params?: Record<string, unknown>,
  config?: Omit<RequestConfig, 'url' | 'method' | 'params'>,
): Promise<ApiResponse<T>> => httpRequest.get<T>(url, params, config);
export const post = <T = unknown>(
  url: string,
  data?: unknown,
  config?: Omit<RequestConfig, 'url' | 'method' | 'data'>,
): Promise<ApiResponse<T>> => httpRequest.post<T>(url, data, config);
export const put = <T = unknown>(
  url: string,
  data?: unknown,
  config?: Omit<RequestConfig, 'url' | 'method' | 'data'>,
): Promise<ApiResponse<T>> => httpRequest.put<T>(url, data, config);
export const del = <T = unknown>(
  url: string,
  params?: Record<string, unknown>,
  config?: Omit<RequestConfig, 'url' | 'method' | 'params'>,
): Promise<ApiResponse<T>> => httpRequest.delete<T>(url, params, config);
export const patch = <T = unknown>(
  url: string,
  data?: unknown,
  config?: Omit<RequestConfig, 'url' | 'method' | 'data'>,
): Promise<ApiResponse<T>> => httpRequest.patch<T>(url, data, config);
export const upload = <T = unknown>(
  url: string,
  file: File,
  config?: Omit<RequestConfig, 'url' | 'method' | 'body'>,
): Promise<ApiResponse<T>> => httpRequest.upload<T>(url, file, config);
export const download = (
  url: string,
  filename?: string,
  config?: Omit<RequestConfig, 'url' | 'method' | 'responseType'>,
): Promise<void> => httpRequest.download(url, filename, config);

export default httpRequest;
