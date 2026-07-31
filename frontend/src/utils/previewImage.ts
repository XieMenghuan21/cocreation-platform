const SVG_MARKUP_PATTERN = /^\s*<svg[\s>]/i;
const ABSOLUTE_HTTP_PATTERN = /^https?:\/\//i;

const SVG_DATA_URL_PREFIX = 'data:image/svg+xml;charset=utf-8,';

export const isSvgMarkup = (value: string): boolean => SVG_MARKUP_PATTERN.test(value.trim());

export const toSvgDataUrl = (value: string): string =>
  `${SVG_DATA_URL_PREFIX}${encodeURIComponent(value.trim())}`;

export const normalizePreviewImageSource = (value: string | null | undefined): string | null => {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }

  if (isSvgMarkup(trimmed)) {
    return toSvgDataUrl(trimmed);
  }

  if (
    trimmed.startsWith('data:image/') ||
    trimmed.startsWith('blob:') ||
    trimmed.startsWith('/')
  ) {
    return trimmed;
  }

  if (ABSOLUTE_HTTP_PATTERN.test(trimmed)) {
    return trimmed;
  }

  return null;
};

export const isProtectedApiAssetUrl = (
  value: string | null | undefined,
  appOrigin?: string,
): boolean => {
  const normalized = normalizePreviewImageSource(value);
  if (!normalized || normalized.startsWith('data:image/') || normalized.startsWith('blob:')) {
    return false;
  }

  if (normalized.startsWith('/api/')) {
    return true;
  }

  if (!appOrigin || !ABSOLUTE_HTTP_PATTERN.test(normalized)) {
    return false;
  }

  try {
    const url = new URL(normalized);
    return url.origin === appOrigin && url.pathname.startsWith('/api/');
  } catch {
    return false;
  }
};
