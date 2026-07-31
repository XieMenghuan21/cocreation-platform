import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';

import {
  fetchForgeCadBlobByUrl,
  type ForgeCadImportAsset,
} from '../services/forgecadService';
import { workspacePreviewHeightClass } from './CoCreationAgentWorkspace.constants';

interface MeshPreviewProps {
  loadBlob: () => Promise<Blob>;
  footer?: string;
  message?: string;
}

const MeshPreview: React.FC<MeshPreviewProps> = ({ loadBlob, footer, message }) => {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const mountNode = mountRef.current;
    if (!mountNode) {
      return undefined;
    }

    let disposed = false;
    const width = mountNode.clientWidth || 720;
    const height = mountNode.clientHeight || 420;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x08111f);
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100000);
    camera.position.set(90, 90, 90);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mountNode.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enablePan = true;
    controls.enableZoom = true;

    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    const directionalLight = new THREE.DirectionalLight(0x9be7ff, 1.6);
    directionalLight.position.set(80, 120, 100);
    scene.add(directionalLight);
    scene.add(new THREE.GridHelper(160, 16, 0x2dd4bf, 0x1f2937));

    const animate = () => {
      if (disposed) return;
      controls.update();
      renderer.render(scene, camera);
      window.requestAnimationFrame(animate);
    };
    animate();

    void loadBlob()
      .then((blob) => blob.arrayBuffer())
      .then((buffer) => {
        if (disposed) return;
        const geometry = new STLLoader().parse(buffer);
        geometry.computeVertexNormals();
        geometry.computeBoundingBox();
        const material = new THREE.MeshStandardMaterial({
          color: 0x38bdf8,
          metalness: 0.2,
          roughness: 0.42,
        });
        const mesh = new THREE.Mesh(geometry, material);
        const box = new THREE.Box3().setFromObject(mesh);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        mesh.position.sub(center);
        scene.add(mesh);
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(geometry),
          new THREE.LineBasicMaterial({ color: 0xe0f2fe }),
        );
        edges.position.copy(mesh.position);
        scene.add(edges);
        const maxSize = Math.max(size.x, size.y, size.z, 1);
        camera.position.set(maxSize * 1.4, maxSize * 1.2, maxSize * 1.4);
        camera.near = maxSize / 100;
        camera.far = maxSize * 100;
        camera.updateProjectionMatrix();
        controls.target.set(0, 0, 0);
        controls.update();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'STL 模型加载失败');
      });

    return () => {
      disposed = true;
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mountNode) {
        mountNode.removeChild(renderer.domElement);
      }
    };
  }, [loadBlob]);

  return (
    <div className="w-full">
      <div ref={mountRef} className={`${workspacePreviewHeightClass} w-full overflow-hidden rounded-2xl border border-white/10 bg-slate-950`} />
      {error ? <div className="mt-3 rounded-xl bg-red-500/10 p-3 text-xs text-red-100">{error}</div> : null}
      <div className="mt-3 text-xs text-slate-400">{footer || '支持旋转、缩放和平移；边线用于模型高亮。'}</div>
      {message ? <div className="mt-2 text-xs text-slate-500">{message}</div> : null}
    </div>
  );
};

const StlPreview: React.FC<{ asset: ForgeCadImportAsset }> = ({ asset }) => {
  const loadBlob = React.useCallback(
    () => fetchForgeCadBlobByUrl(asset.previewAssetUrl || asset.downloadUrl),
    [asset.downloadUrl, asset.previewAssetUrl],
  );

  return <MeshPreview loadBlob={loadBlob} message={asset.conversionMessage || undefined} />;
};

const GeneratedStlPreview: React.FC<{ downloadUrl: string }> = ({ downloadUrl }) => {
  const loadBlob = React.useCallback(
    () => fetchForgeCadBlobByUrl(downloadUrl),
    [downloadUrl],
  );

  return <MeshPreview loadBlob={loadBlob} footer="当前预览来自 ForgeCAD 实际导出的 STL 文件。" />;
};

export { StlPreview, GeneratedStlPreview };
