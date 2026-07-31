import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import viteConfig from '../vite.config';

const workspaceRoot = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(workspaceRoot, 'src/App.tsx'), 'utf8');
const workspaceSource = fs.readFileSync(path.join(workspaceRoot, 'src/components/CoCreationAgentWorkspace.tsx'), 'utf8');

describe('build chunking strategy', () => {
  it('splits heavy vendor groups into dedicated chunks', () => {
    const output = viteConfig.build?.rollupOptions?.output;
    const manualChunks = output && !Array.isArray(output) ? output.manualChunks : undefined;

    expect(typeof manualChunks).toBe('function');
    expect(manualChunks?.('/tmp/node_modules/three/build/three.module.js')).toBe('three-core-vendor');
    expect(manualChunks?.('/tmp/node_modules/three/examples/jsm/controls/OrbitControls.js')).toBe('three-examples-vendor');
    expect(manualChunks?.('/tmp/node_modules/@jscad/modeling/src/primitives/index.js')).toBe('jscad-vendor');
    expect(manualChunks?.('/tmp/node_modules/react-router-dom/dist/index.js')).toBe('router-vendor');
  });
});

describe('surface code splitting', () => {
  it('lazy loads the major app surfaces instead of statically importing them', () => {
    expect(appSource).toContain('lazy(() => import(');
    expect(appSource).not.toContain("import { CoCreationLogin } from './components/CoCreationLogin'");
    expect(appSource).not.toContain("import { CoCreationHistoryPage } from './components/CoCreationHistoryPage'");
    expect(appSource).not.toContain("import CoCreationAgentWorkspace from './components/CoCreationAgentWorkspace'");
  });

  it('lazy loads the heavy JSCAD preview inside the workspace', () => {
    expect(workspaceSource).toContain("lazy(() => import('./JscadAgentPreview'))");
    expect(workspaceSource).not.toContain("import JscadAgentPreview from './JscadAgentPreview'");
  });

  it('does not keep three/STL preview dependencies in the workspace root module', () => {
    expect(workspaceSource).toContain("lazy(() => import('./ThreeMeshPreview')");
    expect(workspaceSource).not.toContain("import * as THREE from 'three'");
    expect(workspaceSource).not.toContain("import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'");
    expect(workspaceSource).not.toContain("import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'");
  });
});
