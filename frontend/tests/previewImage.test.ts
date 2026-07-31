import { describe, expect, it } from 'vitest';

import {
  isProtectedApiAssetUrl,
  normalizePreviewImageSource,
  toSvgDataUrl,
} from '../src/utils/previewImage.ts';

describe('previewImage utils', () => {
  it('normalizePreviewImageSource converts inline svg markup to a data url', () => {
    const svgMarkup = '<svg viewBox="0 0 10 10"><rect width="10" height="10" /></svg>';

    const result = normalizePreviewImageSource(svgMarkup);

    expect(result).toBe(toSvgDataUrl(svgMarkup));
  });

  it('normalizePreviewImageSource keeps direct image urls unchanged', () => {
    const result = normalizePreviewImageSource('/api/v1/industrial-design/assets/demo.png');

    expect(result).toBe('/api/v1/industrial-design/assets/demo.png');
  });

  it('normalizePreviewImageSource rejects non-image plain text', () => {
    const result = normalizePreviewImageSource('not-an-image-source');

    expect(result).toBeNull();
  });

  it('isProtectedApiAssetUrl detects relative api assets', () => {
    expect(isProtectedApiAssetUrl('/api/v1/forgecad/assets/abc/download')).toBe(true);
  });

  it('isProtectedApiAssetUrl detects same-origin absolute api assets', () => {
    const result = isProtectedApiAssetUrl(
      'https://design.example.com/api/v1/industrial-design/assets/demo.png',
      'https://design.example.com',
    );

    expect(result).toBe(true);
  });

  it('isProtectedApiAssetUrl ignores public remote images', () => {
    const result = isProtectedApiAssetUrl(
      'https://cdn.example.com/images/demo.png',
      'https://design.example.com',
    );

    expect(result).toBe(false);
  });
});
