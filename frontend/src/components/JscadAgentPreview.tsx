import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLExporter } from 'three/examples/jsm/exporters/STLExporter.js';
import { cuboid, cylinder, sphere, torus, roundedCuboid, roundedCylinder, ellipsoid, cube, polygon } from '@jscad/modeling/src/primitives';
import { union, subtract, intersect } from '@jscad/modeling/src/operations/booleans';
import { translate, rotate, scale, center } from '@jscad/modeling/src/operations/transforms';
import { extrudeLinear } from '@jscad/modeling/src/operations/extrusions';
import { Download, Sliders } from 'lucide-react';

interface ParamSlider {
  name: string;
  value: number;
  min: number;
  max: number;
  step: number;
}

interface JscadAgentPreviewProps {
  description: string;
  industry: string;
  projectName: string;
}

const createDefaultJscadCode = (description: string, industry: string, projectName: string): string => {
  const normalizedText = `${projectName} ${industry} ${description}`.toLowerCase();
  const isCabinet = /柜|箱|壳|罩|shell|cabinet/u.test(normalizedText);
  const isBracket = /支架|底座|夹具|bracket|fixture|base/u.test(normalizedText);
  const baseLength = isCabinet ? 36 : isBracket ? 42 : 32;
  const baseWidth = isCabinet ? 22 : 18;
  const baseHeight = isCabinet ? 34 : isBracket ? 10 : 14;

  return `const baseLength = ${baseLength};
const baseWidth = ${baseWidth};
const baseHeight = ${baseHeight};
const wallThickness = 2.4;
const ribThickness = 1.6;
const holeRadius = 2.2;

const main = () => {
  const base = cuboid({ size: [baseLength, baseWidth, wallThickness] });
  const leftWall = translate([-baseLength / 2 + wallThickness / 2, 0, baseHeight / 2], cuboid({ size: [wallThickness, baseWidth, baseHeight] }));
  const rightWall = translate([baseLength / 2 - wallThickness / 2, 0, baseHeight / 2], cuboid({ size: [wallThickness, baseWidth, baseHeight] }));
  const backRib = translate([0, -baseWidth / 2 + wallThickness / 2, baseHeight / 2], cuboid({ size: [baseLength, wallThickness, baseHeight] }));
  const centerRib = translate([0, 0, baseHeight / 2], cuboid({ size: [ribThickness, baseWidth, baseHeight * 0.86] }));
  const topBeam = translate([0, 0, baseHeight], cuboid({ size: [baseLength, baseWidth, wallThickness] }));
  const holeLeft = translate([-baseLength * 0.3, 0, 0], cylinder({ height: wallThickness * 3, radius: holeRadius, segments: 48 }));
  const holeRight = translate([baseLength * 0.3, 0, 0], cylinder({ height: wallThickness * 3, radius: holeRadius, segments: 48 }));

  return subtract(
    union(base, leftWall, rightWall, backRib, centerRib, topBeam),
    holeLeft,
    holeRight,
  );
};`.trim();
};

const JscadAgentPreview: React.FC<JscadAgentPreviewProps> = ({ description, industry, projectName }) => {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const groupRef = useRef<THREE.Group | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const animRef = useRef<number>(0);

  const [generatedCode, setGeneratedCode] = useState('');
  const [params, setParams] = useState<ParamSlider[]>([]);
  const [hasResult, setHasResult] = useState(false);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<'preview' | 'code' | 'params'>('preview');

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const width = mount.clientWidth || 400;
    const height = 380;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0e1a);
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(40, 30, 40);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controlsRef.current = controls;

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const dirLight = new THREE.DirectionalLight(0x9be7ff, 1.4);
    dirLight.position.set(40, 60, 50);
    scene.add(dirLight);
    scene.add(new THREE.GridHelper(60, 12, 0x2dd4bf, 0x1f2937));

    const group = new THREE.Group();
    groupRef.current = group;
    scene.add(group);

    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      animRef.current = window.requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(animRef.current);
      renderer.dispose();
      if (mount.contains(renderer.domElement)) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, []);

  const executeJscadCode = (code: string, paramValues?: ParamSlider[]): unknown => {
    let processedCode = code;
    if (paramValues) {
      paramValues.forEach((p) => {
        const regex = new RegExp(`const\\s+${p.name}\\s*=\\s*[\\d.]+\\s*;?`, 'g');
        processedCode = processedCode.replace(regex, `const ${p.name} = ${p.value};`);
      });
    }

    const fn = new Function(
      'cuboid', 'cylinder', 'sphere', 'torus',
      'roundedCuboid', 'roundedCylinder', 'ellipsoid', 'cube', 'polygon',
      'union', 'subtract', 'intersect',
      'translate', 'rotate', 'scale', 'center',
      'extrudeLinear',
      `${processedCode}\nreturn main();`,
    );

    return fn(
      cuboid, cylinder, sphere, torus,
      roundedCuboid, roundedCylinder, ellipsoid, cube, polygon,
      union, subtract, intersect,
      translate, rotate, scale, center,
      extrudeLinear,
    );
  };

  const toThreeMesh = (geometry: Record<string, unknown>, color = 0x4fc3f7): THREE.Mesh => {
    const polygons = (geometry?.polygons || []) as Array<{ vertices: number[][] }>;
    const positions: number[] = [];
    polygons.forEach((poly) => {
      const verts = poly?.vertices || [];
      if (verts.length >= 3) {
        for (let i = 1; i < verts.length - 1; i++) {
          positions.push(...verts[0], ...verts[i], ...verts[i + 1]);
        }
      }
    });

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
      color,
      metalness: 0.3,
      roughness: 0.5,
    });
    return new THREE.Mesh(geo, mat);
  };

  const clearSceneGroup = () => {
    const group = groupRef.current;
    if (!group) return;
    while (group.children.length > 0) {
      const child = group.children[0];
      if (child instanceof THREE.Mesh) {
        child.geometry?.dispose();
        const mat = child.material;
        if (Array.isArray(mat)) {
          mat.forEach((m) => m.dispose());
        } else {
          mat?.dispose();
        }
      }
      group.remove(child);
    }
  };

  const renderJscadResult = (result: unknown) => {
    clearSceneGroup();
    const group = groupRef.current;
    if (!group) return;

    const geometries = Array.isArray(result) ? result : [result];
    const colors = [0x4fc3f7, 0x81c784, 0xffb74d, 0xba68c8, 0xef5350, 0x26c6da];

    geometries.forEach((geo: unknown, i: number) => {
      const g = geo as Record<string, unknown>;
      if (g && g.polygons && Array.isArray(g.polygons) && g.polygons.length > 0) {
        const mesh = toThreeMesh(g, colors[i % colors.length]);
        group.add(mesh);
      }
    });

    if (group.children.length > 0) {
      const box = new THREE.Box3().setFromObject(group);
      const size = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z);
      if (maxDim > 0 && cameraRef.current) {
        const dist = Math.max(maxDim * 1.8, 10);
        cameraRef.current.position.set(dist, dist * 0.7, dist);
        controlsRef.current?.update();
      }
    }
  };

  const extractParams = (code: string): ParamSlider[] => {
    const sliders: ParamSlider[] = [];
    const regex = /const\s+(\w+)\s*=\s*(\d+(?:\.\d+)?)\s*;?/g;
    let match;
    while ((match = regex.exec(code)) !== null) {
      const name = match[1];
      const val = parseFloat(match[2]);
      if (val > 0 && val < 1000 && !name.includes('PI') && !name.startsWith('_')) {
        sliders.push({
          name,
          value: val,
          min: Math.max(0.1, val * 0.2),
          max: val * 3,
          step: val > 20 ? 1 : 0.1,
        });
      }
    }
    return sliders.slice(0, 12);
  };

  useEffect(() => {
    if (!groupRef.current) return;
    try {
      const code = createDefaultJscadCode(description, industry, projectName);
      const extractedParams = extractParams(code);
      const result = executeJscadCode(code, extractedParams);
      setGeneratedCode(code);
      setParams(extractedParams);
      setActiveTab('preview');
      setError('');
      renderJscadResult(result);
      setHasResult(true);
    } catch (err) {
      setHasResult(false);
      clearSceneGroup();
      setError(err instanceof Error ? err.message : '本地 JSCAD 生成失败');
    }
  }, [description, industry, projectName]);

  const handleParamChange = (index: number, newValue: number) => {
    setParams((prev) => {
      const updated = [...prev];
      updated[index] = { ...updated[index], value: newValue };
      return updated;
    });
    try {
      const currentParams = [...params];
      currentParams[index] = { ...currentParams[index], value: newValue };
      const result = executeJscadCode(generatedCode, currentParams);
      renderJscadResult(result);
    } catch {
      /* ignore param re-render errors */
    }
  };

  const handleExportStl = () => {
    const group = groupRef.current;
    if (!group) return;
    try {
      const exporter = new STLExporter();
      const result = exporter.parse(group, { binary: false });
      const blob = new Blob([result], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${projectName.replace(/[\s/]+/g, '_')}_3d_model.stl`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('STL 导出失败');
    }
  };

  return (
    <div className="space-y-3">
      {hasResult && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={handleExportStl}
            className="rounded-lg border border-indigo-200 bg-white px-3 py-2.5 text-sm font-semibold text-indigo-700 transition hover:bg-indigo-50"
            title="导出 STL"
          >
            <Download className="h-4 w-4" />
          </button>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm leading-6 text-red-800">
          {error}
        </div>
      )}

      {hasResult && (
        <div className="flex gap-2">
          {(['preview', 'code', 'params'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                activeTab === tab ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
              }`}
            >
              {tab === 'preview' && '3D 预览'}
              {tab === 'code' && '代码'}
              {tab === 'params' && (
                <span className="inline-flex items-center gap-1">
                  参数 <Sliders className="h-3 w-3" />
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {activeTab === 'preview' && (
        <div ref={mountRef} className="h-[380px] w-full overflow-hidden rounded-xl" />
      )}

      {activeTab === 'code' && generatedCode && (
        <pre className="max-h-[380px] overflow-auto rounded-xl bg-[#0d1117] p-4 text-xs leading-5 text-[#e6edf3]">
          <code>{generatedCode}</code>
        </pre>
      )}

      {activeTab === 'params' && params.length > 0 && (
        <div className="max-h-[380px] space-y-3 overflow-auto rounded-xl border border-slate-200 bg-white p-4">
          {params.map((p, i) => (
            <div key={p.name}>
              <div className="flex items-center justify-between text-sm">
                <span className="font-mono text-xs font-semibold text-slate-700">{p.name}</span>
                <span className="font-mono text-xs text-indigo-600">{p.value.toFixed(p.step < 1 ? 1 : 0)}</span>
              </div>
              <input
                type="range"
                min={p.min}
                max={p.max}
                step={p.step}
                value={p.value}
                onChange={(e) => handleParamChange(i, parseFloat(e.target.value))}
                className="mt-1 h-1.5 w-full cursor-pointer appearance-none rounded-full bg-slate-200 accent-indigo-600"
              />
            </div>
          ))}
        </div>
      )}

      {activeTab === 'params' && params.length === 0 && hasResult && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
          此模型无可调节参数
        </div>
      )}
    </div>
  );
};

export default JscadAgentPreview;
