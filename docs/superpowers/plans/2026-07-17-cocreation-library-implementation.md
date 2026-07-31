# Cocreation Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved local-first project library, asset library, and project-version reference flow for the cocreation workspace, with stable data grouping and responsive layout polish.

**Architecture:** Keep the existing single-shell React app and localStorage persistence model, but normalize project/version/asset relationships in shared helpers and types. Implement the new UX in three areas: shell navigation and responsive framing, workspace reference-asset/version-picking flow, and library browsing/publishing surfaces.

**Tech Stack:** React 18, TypeScript, Vite, Tailwind utility classes, localStorage persistence, Vitest for new regression tests.

---

### Task 1: Add a minimal test harness and cover normalization helpers

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/src/components/CoCreationAgentWorkspace.types.ts`
- Modify: `frontend/src/components/CoCreationAgentWorkspace.helpers.ts`
- Create: `frontend/tests/cocreationLibrary.helpers.test.ts`

- [ ] **Step 1: Write the failing helper tests**

```ts
import { describe, expect, it } from 'vitest';
import {
  buildProjectLibraryItems,
  normalizeVersionSnapshot,
  buildAssetLibraryItemsFromVersion,
} from '../src/components/CoCreationAgentWorkspace.helpers';

describe('normalizeVersionSnapshot', () => {
  it('fills project metadata for legacy snapshots', () => {
    const snapshot = normalizeVersionSnapshot({
      id: 'V1.0',
      label: '伺服联动底座 V1',
      sourceObject: '伺服联动底座',
      status: '已完成',
      note: 'legacy',
      createdAt: '2026-07-17T01:00:00.000Z',
    });

    expect(snapshot.projectId).toBe('project-伺服联动底座');
    expect(snapshot.projectName).toBe('伺服联动底座');
    expect(snapshot.versionNumber).toBe(1);
    expect(snapshot.isFinalized).toBe(false);
  });
});

describe('buildProjectLibraryItems', () => {
  it('groups versions by project id instead of latest version label', () => {
    const items = buildProjectLibraryItems([
      {
        id: 'V2.0',
        label: '伺服联动底座 V2',
        projectId: 'project-servo',
        projectName: '伺服联动底座',
        versionNumber: 2,
        sourceObject: '伺服联动底座',
        status: '已完成',
        note: 'ok',
        createdAt: '2026-07-17T03:00:00.000Z',
      },
      {
        id: 'V1.0',
        label: '伺服联动底座 V1',
        projectId: 'project-servo',
        projectName: '伺服联动底座',
        versionNumber: 1,
        sourceObject: '伺服联动底座',
        status: '已完成',
        note: 'ok',
        createdAt: '2026-07-17T01:00:00.000Z',
      },
    ]);

    expect(items).toHaveLength(1);
    expect(items[0]?.project.name).toBe('伺服联动底座');
    expect(items[0]?.versions.map((item) => item.id)).toEqual(['V2.0', 'V1.0']);
  });
});

describe('buildAssetLibraryItemsFromVersion', () => {
  it('builds prompt and image assets with source ids', () => {
    const assets = buildAssetLibraryItemsFromVersion({
      id: 'V3.0',
      label: '伺服联动底座 V3',
      projectId: 'project-servo',
      projectName: '伺服联动底座',
      versionNumber: 3,
      sourceObject: '伺服联动底座',
      status: '已完成',
      note: 'ok',
      prompt: '生成银灰色工业底座',
      previewImageUrl: 'https://example.com/servo.png',
      createdAt: '2026-07-17T04:00:00.000Z',
      isFinalized: true,
    });

    expect(assets).toHaveLength(2);
    expect(assets.every((item) => item.sourceProjectId === 'project-servo')).toBe(true);
  });
});
```

- [ ] **Step 2: Run the new helper test to verify it fails**

Run: `cd frontend && npx vitest run tests/cocreationLibrary.helpers.test.ts`

Expected: FAIL because the helper exports and new fields do not exist yet.

- [ ] **Step 3: Add Vitest scripts and helper implementations**

```ts
// frontend/package.json
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "devDependencies": {
    "vitest": "^2.1.8"
  }
}
```

```ts
// frontend/vite.config.ts
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
```

```ts
// frontend/src/components/CoCreationAgentWorkspace.types.ts
export interface VersionSnapshot {
  id: string;
  label: string;
  projectId?: string;
  projectName?: string;
  versionNumber?: number;
  isFinalized?: boolean;
  status: string;
  note: string;
  ...
}

export interface ProjectRecord {
  id: string;
  name: string;
  ...
}

export interface AssetLibraryItem {
  id: string;
  kind: AssetLibraryItemKind;
  title: string;
  description: string;
  prompt?: string;
  imageUrl?: string | null;
  sourceProjectId: string;
  sourceProjectName: string;
  sourceVersionId: string;
  sourceVersionLabel: string;
  createdAt: string;
  isFinalized: boolean;
}
```

```ts
// frontend/src/components/CoCreationAgentWorkspace.helpers.ts
export const buildProjectId = (projectName: string): string => {
  const normalized = projectName.trim() || 'untitled-project';
  return `project-${normalized.replace(/\s+/g, '-').toLowerCase()}`;
};

export const normalizeVersionSnapshot = (snapshot: VersionSnapshot): VersionSnapshot => {
  const projectName = snapshot.projectName || snapshot.sourceObject || snapshot.label || '未命名项目';
  const matched = snapshot.id.match(/V(\d+(?:\.\d+)?)/);
  const versionNumber = snapshot.versionNumber ?? (matched ? Number(matched[1]) : 1);
  return {
    ...snapshot,
    projectId: snapshot.projectId || buildProjectId(projectName),
    projectName,
    versionNumber,
    isFinalized: snapshot.isFinalized ?? false,
  };
};

export const buildProjectLibraryItems = (snapshots: VersionSnapshot[], projects: ProjectRecord[] = []): ProjectLibraryItem[] => {
  const normalized = snapshots.map(normalizeVersionSnapshot);
  const grouped = new Map<string, VersionSnapshot[]>();
  normalized.forEach((snapshot) => {
    const key = snapshot.projectId as string;
    const list = grouped.get(key) || [];
    list.push(snapshot);
    grouped.set(key, list);
  });

  return Array.from(grouped.entries()).map(([projectId, versions]) => {
    const sortedVersions = versions.slice().sort((left, right) => new Date(right.createdAt || 0).getTime() - new Date(left.createdAt || 0).getTime());
    const fallbackProject = sortedVersions[0];
    const project = projects.find((item) => item.id === projectId) || {
      id: projectId,
      name: fallbackProject?.projectName || '未命名项目',
      industry: '全部行业',
      description: fallbackProject?.note || '',
      inputMode: 'prompt',
      createdAt: fallbackProject?.createdAt || new Date().toISOString(),
      updatedAt: fallbackProject?.createdAt || new Date().toISOString(),
      versionCount: sortedVersions.length,
    };
    return {
      project: { ...project, versionCount: sortedVersions.length },
      versions: sortedVersions,
      latestVersion: sortedVersions[0] || null,
    };
  }).sort((left, right) => new Date(right.latestVersion?.createdAt || 0).getTime() - new Date(left.latestVersion?.createdAt || 0).getTime());
};

export const buildAssetLibraryItemsFromVersion = (version: VersionSnapshot): AssetLibraryItem[] => {
  const normalized = normalizeVersionSnapshot(version);
  const createdAt = normalized.createdAt || new Date().toISOString();
  const items: AssetLibraryItem[] = [];

  if (normalized.previewImageUrl) {
    items.push({
      id: `asset-${normalized.id}-image`,
      kind: 'image',
      title: `${normalized.label} 定稿图`,
      description: normalized.resultText || normalized.executionSummary || normalized.note,
      imageUrl: normalized.previewImageUrl,
      prompt: normalized.prompt,
      sourceProjectId: normalized.projectId as string,
      sourceProjectName: normalized.projectName as string,
      sourceVersionId: normalized.id,
      sourceVersionLabel: normalized.label,
      createdAt,
      isFinalized: true,
    });
  }

  if (normalized.prompt || normalized.optimizedPrompt) {
    items.push({
      id: `asset-${normalized.id}-prompt`,
      kind: 'prompt',
      title: `${normalized.label} Prompt`,
      description: normalized.prompt || normalized.optimizedPrompt || normalized.note,
      prompt: normalized.prompt || normalized.optimizedPrompt,
      sourceProjectId: normalized.projectId as string,
      sourceProjectName: normalized.projectName as string,
      sourceVersionId: normalized.id,
      sourceVersionLabel: normalized.label,
      createdAt,
      isFinalized: true,
    });
  }

  return items;
};
```

- [ ] **Step 4: Run the helper test to verify it passes**

Run: `cd frontend && npx vitest run tests/cocreationLibrary.helpers.test.ts`

Expected: PASS with 3 tests passing.

### Task 2: Rework workspace reference-asset version picking and publishing flow

**Files:**
- Modify: `frontend/src/components/CoCreationAgentWorkspace.tsx`
- Modify: `frontend/src/components/CoCreationAgentWorkspace.helpers.ts`
- Modify: `frontend/src/components/CoCreationAgentWorkspace.types.ts`

- [ ] **Step 1: Write the failing workflow-facing helper test**

```ts
import { describe, expect, it } from 'vitest';
import { getProjectVersionOptions } from '../src/components/CoCreationAgentWorkspace.helpers';

describe('getProjectVersionOptions', () => {
  it('returns only versions for the active project sorted newest first', () => {
    const versions = getProjectVersionOptions(
      'project-servo',
      [
        { id: 'V1.0', label: 'A V1', projectId: 'project-servo', projectName: 'A', versionNumber: 1, status: '已完成', note: 'x', createdAt: '2026-07-17T01:00:00.000Z' },
        { id: 'V2.0', label: 'A V2', projectId: 'project-servo', projectName: 'A', versionNumber: 2, status: '已完成', note: 'x', createdAt: '2026-07-17T03:00:00.000Z' },
        { id: 'V1.0-b', label: 'B V1', projectId: 'project-b', projectName: 'B', versionNumber: 1, status: '已完成', note: 'x', createdAt: '2026-07-17T02:00:00.000Z' },
      ],
    );

    expect(versions.map((item) => item.id)).toEqual(['V2.0', 'V1.0']);
  });
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cd frontend && npx vitest run tests/cocreationLibrary.helpers.test.ts`

Expected: FAIL because `getProjectVersionOptions` does not exist yet.

- [ ] **Step 3: Implement minimal workspace flow**

```ts
// frontend/src/components/CoCreationAgentWorkspace.helpers.ts
export const getProjectVersionOptions = (projectId: string, snapshots: VersionSnapshot[]): VersionSnapshot[] =>
  snapshots
    .map(normalizeVersionSnapshot)
    .filter((snapshot) => snapshot.projectId === projectId)
    .sort((left, right) => new Date(right.createdAt || 0).getTime() - new Date(left.createdAt || 0).getTime());
```

```tsx
// frontend/src/components/CoCreationAgentWorkspace.tsx
const normalizedSnapshots = useMemo(
  () => versionSnapshots.map(normalizeVersionSnapshot),
  [versionSnapshots],
);

const currentProjectVersions = useMemo(
  () => getProjectVersionOptions(currentProject?.id || buildProjectId(currentProjectName), normalizedSnapshots),
  [currentProject?.id, currentProjectName, normalizedSnapshots],
);

const [pendingReferenceVersionId, setPendingReferenceVersionId] = useState<string>('');
const [isAssetAddModalOpen, setIsAssetAddModalOpen] = useState(false);
const [assetDraftMode, setAssetDraftMode] = useState<'publish' | 'upload' | 'prompt'>('publish');

const handleOpenVersionPicker = () => {
  setPendingReferenceVersionId(currentProjectVersions[0]?.id || '');
  setIsVersionPickerOpen(true);
};

const handleConfirmReferenceVersion = () => {
  const selected = currentProjectVersions.find((item) => item.id === pendingReferenceVersionId) || null;
  if (!selected) return;
  setSelectedReferenceAsset(selected);
  setIsVersionPickerOpen(false);
};
```

```tsx
// button area in workspace
<button onClick={handleOpenVersionPicker}>选项目版本</button>
{isVersionPickerOpen ? (
  <div role="dialog" aria-modal="true">
    {currentProjectVersions.length === 0 ? (
      <div>当前项目暂无可引用版本</div>
    ) : (
      currentProjectVersions.map((version) => (
        <button key={version.id} onClick={() => setPendingReferenceVersionId(version.id)}>
          {version.label}
        </button>
      ))
    )}
    <button onClick={handleConfirmReferenceVersion}>确认引用</button>
  </div>
) : null}
```

- [ ] **Step 4: Run the helper test to verify it passes**

Run: `cd frontend && npx vitest run tests/cocreationLibrary.helpers.test.ts`

Expected: PASS with the additional project-version-option case green.

### Task 3: Rebuild the project library and asset library views

**Files:**
- Modify: `frontend/src/components/CoCreationHistoryPage.tsx`
- Modify: `frontend/src/components/CoCreationAgentWorkspace.helpers.ts`
- Modify: `frontend/src/components/CoCreationAgentWorkspace.types.ts`

- [ ] **Step 1: Write the failing library view model test**

```ts
import { describe, expect, it } from 'vitest';
import { buildProjectLibraryItems, buildAssetLibraryItemsFromVersion } from '../src/components/CoCreationAgentWorkspace.helpers';

describe('project library view model', () => {
  it('marks published versions as finalized and keeps asset source linkage', () => {
    const versions = buildProjectLibraryItems([
      {
        id: 'V3.0',
        label: 'A V3',
        projectId: 'project-a',
        projectName: '项目 A',
        versionNumber: 3,
        status: '已完成',
        note: 'ok',
        createdAt: '2026-07-17T05:00:00.000Z',
        isFinalized: true,
      },
    ]);
    const assets = buildAssetLibraryItemsFromVersion({
      id: 'V3.0',
      label: 'A V3',
      projectId: 'project-a',
      projectName: '项目 A',
      versionNumber: 3,
      status: '已完成',
      note: 'ok',
      prompt: 'prompt',
      previewImageUrl: 'https://example.com/a.png',
      createdAt: '2026-07-17T05:00:00.000Z',
      isFinalized: true,
    });

    expect(versions[0]?.versions[0]?.isFinalized).toBe(true);
    expect(assets[0]?.sourceProjectName).toBe('项目 A');
  });
});
```

- [ ] **Step 2: Run the library test and verify it fails or is incomplete**

Run: `cd frontend && npx vitest run tests/cocreationLibrary.helpers.test.ts`

Expected: FAIL if finalized/source-link behavior is missing.

- [ ] **Step 3: Implement the project and asset library surfaces**

```tsx
// frontend/src/components/CoCreationHistoryPage.tsx
const projectItems = useMemo(
  () => buildProjectLibraryItems(snapshots, projectRecords),
  [snapshots, projectRecords],
);

const selectedProject = projectItems.find((item) => item.project.id === selectedProjectId) || projectItems[0] || null;
const selectedVersion = selectedProject?.versions.find((item) => item.id === selectedVersionId) || selectedProject?.versions[0] || null;

// project library layout
<div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
  <section className="space-y-4">
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {projectItems.map((item) => (
        <button key={item.project.id} onClick={() => setSelectedProjectId(item.project.id)}>
          <div>{item.project.name}</div>
          <div>{item.project.versionCount} 个版本</div>
        </button>
      ))}
    </div>
    <div className="space-y-3">
      {selectedProject?.versions.map((version) => (
        <button key={version.id} onClick={() => setSelectedVersionId(version.id)}>
          <div>{version.label}</div>
          <div>{formatTime(version.createdAt)}</div>
        </button>
      ))}
    </div>
  </section>
  <aside>
    <div>{selectedVersion?.prompt || '暂无 Prompt'}</div>
    <button onClick={() => handleUseAsReference(selectedVersion!)}>设为参考资产</button>
    <button onClick={() => handlePublishToLibrary(selectedVersion!)}>发布到资产库</button>
  </aside>
</div>
```

```tsx
// asset add modal states
const [isAddAssetModalOpen, setIsAddAssetModalOpen] = useState(false);
const [assetCreateMode, setAssetCreateMode] = useState<'publish' | 'upload' | 'prompt'>('publish');
const [manualPromptTitle, setManualPromptTitle] = useState('');
const [manualPromptContent, setManualPromptContent] = useState('');
```

```tsx
// asset library add modal behavior
<button onClick={() => setIsAddAssetModalOpen(true)}>添加</button>
{isAddAssetModalOpen ? (
  <div role="dialog" aria-modal="true">
    <button onClick={() => setAssetCreateMode('publish')}>从项目版本发布</button>
    <button onClick={() => setAssetCreateMode('upload')}>上传外部资产</button>
    <button onClick={() => setAssetCreateMode('prompt')}>新建 Prompt 资产</button>
  </div>
) : null}
```

- [ ] **Step 4: Run the library helper tests to verify they pass**

Run: `cd frontend && npx vitest run tests/cocreationLibrary.helpers.test.ts`

Expected: PASS with all helper/library assertions green.

### Task 4: Polish shell layout and run full verification

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/CoCreationHistoryPage.tsx`
- Modify: `frontend/src/components/CoCreationAgentWorkspace.tsx`

- [ ] **Step 1: Add the failing responsive shell assertions manually via build verification**

Run: `cd frontend && npm run build`

Expected: If current JSX/class structure is inconsistent after earlier edits, build may fail or expose type gaps.

- [ ] **Step 2: Implement shell/layout polish**

```tsx
// frontend/src/App.tsx
<div className="fixed inset-x-0 top-0 z-50 border-b border-slate-200 bg-white/92 backdrop-blur">
  <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
      <span className="shrink-0 text-sm font-semibold text-slate-900">AI 共创设计工作台</span>
      <div className="flex max-w-full items-center gap-1 overflow-x-auto rounded-2xl border border-slate-200 bg-slate-100/80 p-1">
        ...
      </div>
    </div>
    <div className="flex shrink-0 items-center gap-2 text-xs text-slate-500">
      ...
    </div>
  </div>
</div>
```

```tsx
// shared visual direction
// Use consistent button classes for primary/secondary/destructive buttons and reduce stacked card density.
```

- [ ] **Step 3: Run tests and production build**

Run: `cd frontend && npx vitest run tests/cocreationLibrary.helpers.test.ts`
Expected: PASS.

Run: `cd frontend && npm run build`
Expected: PASS with Vite build output and exit code 0.

- [ ] **Step 4: Manual runtime spot check**

Run: `cd frontend && npm run dev -- --host 127.0.0.1 --port 4174`

Expected:
- 工作台能打开“选项目版本”弹窗
- 项目库看到卡片、版本列表、版本详情
- 资产库能打开“添加”弹窗
- 顶部在窄宽度下不出现竖排挤压
