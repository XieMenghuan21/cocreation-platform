import React, { useEffect, useState } from 'react';

import { fetchForgeCadBlobByUrl } from '../services/forgecadService';
import { isProtectedApiAssetUrl, normalizePreviewImageSource } from '../utils/previewImage';

type PreviewImageProps = Omit<React.ImgHTMLAttributes<HTMLImageElement>, 'src'> & {
  src?: string | null;
};

const PreviewImage: React.FC<PreviewImageProps> = ({ src, alt, ...props }) => {
  const normalizedSource = normalizePreviewImageSource(src);
  const [resolvedSource, setResolvedSource] = useState<string | null>(normalizedSource);

  useEffect(() => {
    const appOrigin = typeof window !== 'undefined' ? window.location.origin : undefined;
    if (!normalizedSource) {
      setResolvedSource(null);
      return undefined;
    }

    if (!isProtectedApiAssetUrl(normalizedSource, appOrigin)) {
      setResolvedSource(normalizedSource);
      return undefined;
    }

    let revokedObjectUrl: string | null = null;
    let cancelled = false;
    setResolvedSource(null);

    void fetchForgeCadBlobByUrl(normalizedSource)
      .then((blob) => {
        if (cancelled) {
          return;
        }
        revokedObjectUrl = URL.createObjectURL(blob);
        setResolvedSource(revokedObjectUrl);
      })
      .catch(() => {
        if (!cancelled) {
          setResolvedSource(normalizedSource);
        }
      });

    return () => {
      cancelled = true;
      if (revokedObjectUrl) {
        URL.revokeObjectURL(revokedObjectUrl);
      }
    };
  }, [normalizedSource]);

  if (!resolvedSource) {
    return null;
  }

  return <img {...props} src={resolvedSource} alt={alt} />;
};

export default PreviewImage;
