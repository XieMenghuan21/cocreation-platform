# Cocreation Visual Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the cocreation platform into a wider, more exhibition-style light industrial design workspace without changing its functional flows.

**Architecture:** Keep the current React/Vite single-app structure and localStorage-driven behavior, but rebuild the visual system from the shell inward. Apply the refresh in layers: global style tokens and shell framing first, then login branding, then workspace stage/control hierarchy, then project and asset library presentation so the whole product reads as one system.

**Tech Stack:** React 18, TypeScript, Vite, Tailwind utilities, existing localStorage state, Vitest.

---

### Task 1: Establish global visual system and shell framing

**Files:**
- Modify: `frontend/src/styles/index.css`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/tests/previewImage.test.ts`

- [ ] **Step 1: Confirm the current preview utility tests still pass before shell-only work**

Run: `cd frontend && npm test -- previewImage.test.ts`

Expected: PASS with 6 tests passing.

- [ ] **Step 2: Refresh global style tokens and page background layers**

```css
/* frontend/src/styles/index.css */
body {
  min-height: 100vh;
  color: rgb(var(--color-brand-text-primary));
  background:
    radial-gradient(circle at top left, rgba(148, 163, 184, 0.18), transparent 24%),
    radial-gradient(circle at 85% 12%, rgba(59, 130, 246, 0.12), transparent 22%),
    linear-gradient(180deg, #f8fafc 0%, #edf2f8 100%);
}

:root {
  --color-brand-primary: 15 23 42;
  --color-brand-secondary: 37 99 235;
  --color-brand-accent: 14 165 233;
  --color-brand-background: 248 250 252;
}
```

- [ ] **Step 3: Rebuild the app shell into a floating exhibition control bar**

```tsx
/* frontend/src/App.tsx */
<div className="fixed inset-x-0 top-0 z-50 px-3 pt-3 sm:px-5">
  <div className="mx-auto max-w-7xl rounded-[28px] border border-white/70 bg-white/82 shadow-[0_18px_60px_rgba(15,23,42,0.08)] backdrop-blur-xl">
    ...
  </div>
</div>
```

- [ ] **Step 4: Re-run the focused test to ensure shell changes did not break imports**

Run: `cd frontend && npm test -- previewImage.test.ts`

Expected: PASS with 6 tests passing.

### Task 2: Rebuild the login page into a branded entry surface

**Files:**
- Modify: `frontend/src/components/CoCreationLogin.tsx`
- Test: `frontend/tests/previewImage.test.ts`

- [ ] **Step 1: Reuse the passing baseline and keep logic untouched**

Run: `cd frontend && npm test -- previewImage.test.ts`

Expected: PASS with 6 tests passing.

- [ ] **Step 2: Upgrade the login page presentation without changing auth behavior**

```tsx
/* frontend/src/components/CoCreationLogin.tsx */
<div className="min-h-screen bg-[linear-gradient(180deg,#f8fafc_0%,#edf2f8_100%)] lg:grid lg:grid-cols-2">
  <div className="relative hidden overflow-hidden lg:flex lg:flex-col lg:justify-between bg-[linear-gradient(135deg,#0f172a_0%,#1d4ed8_42%,#38bdf8_100%)] p-12 text-white">
    ...
  </div>
  <div className="relative flex items-center justify-center px-6 py-10 sm:px-8 lg:px-12">
    <div className="relative w-full max-w-[430px] rounded-[32px] border border-white/75 bg-white/92 p-8 shadow-[0_24px_80px_rgba(15,23,42,0.10)] backdrop-blur-xl sm:p-10">
      ...
    </div>
  </div>
</div>
```

- [ ] **Step 3: Keep the character animation but lower the cartoon feel**

```tsx
/* frontend/src/components/CoCreationLogin.tsx */
<div className="relative z-20 max-w-sm">
  <div className="text-xs font-semibold uppercase tracking-[0.28em] text-white/45">AI Assisted Industrial Design</div>
  <div className="mt-3 text-3xl font-semibold leading-tight text-white">让项目、版本和资产在同一条设计链路里协作。</div>
  <div className="mt-4 text-sm leading-7 text-white/60">从设计图、Prompt、3D 打样到定稿资产，统一沉淀在一个可持续复用的工作台里。</div>
</div>
```

- [ ] **Step 4: Re-run the focused test after the login refresh**

Run: `cd frontend && npm test -- previewImage.test.ts`

Expected: PASS with 6 tests passing.

### Task 3: Rebuild the workspace into a preview-led exhibition control page

**Files:**
- Modify: `frontend/src/components/CoCreationAgentWorkspace.constants.ts`
- Modify: `frontend/src/components/CoCreationAgentWorkspace.tsx`
- Test: `frontend/tests/workspacePreviewLayout.test.ts`

- [ ] **Step 1: Use the preview layout regression test as the red/green guard**

Run: `cd frontend && npm test -- workspacePreviewLayout.test.ts`

Expected: PASS on the current adaptive-height baseline before broader workspace restyling.

- [ ] **Step 2: Expand the workspace visual hierarchy around the preview stage**

```tsx
/* frontend/src/components/CoCreationAgentWorkspace.tsx */
<div className={`bg-transparent text-slate-900 ${isStandalone ? 'min-h-screen px-3 pb-6 sm:px-5' : ''}`}>
  <section className="overflow-hidden rounded-[32px] border border-white/70 bg-white/88 shadow-[0_20px_80px_rgba(15,23,42,0.08)] backdrop-blur-xl">
    ...
    <div className="grid gap-0 xl:grid-cols-[360px_minmax(0,1fr)]">
      <aside className="border-r border-slate-200/80 bg-[linear-gradient(180deg,#ffffff_0%,#f8fbff_100%)] p-6">
      <main className="min-w-0 bg-[linear-gradient(180deg,#f8fafc_0%,#eef4fb_100%)] p-5">
```

- [ ] **Step 3: Turn the main preview into a stage instead of a boxed utility pane**

```tsx
/* frontend/src/components/CoCreationAgentWorkspace.tsx */
<div className="overflow-hidden rounded-[28px] border border-slate-200/80 bg-white/94 shadow-[0_20px_70px_rgba(15,23,42,0.07)]">
  <div className="flex items-center justify-between border-b border-slate-200 bg-[linear-gradient(180deg,#ffffff_0%,#f8fbff_100%)] px-5 py-3">
    ...
  </div>
  <div className="p-3 sm:p-4">{renderWorkspacePreview()}</div>
</div>
```

- [ ] **Step 4: Reorder and polish the left control rail without changing behavior**

```tsx
/* frontend/src/components/CoCreationAgentWorkspace.tsx */
<div className="rounded-[24px] border border-slate-200/80 bg-white/90 p-4 shadow-sm">
  ...
</div>
```

- [ ] **Step 5: Re-run the preview-specific regression test**

Run: `cd frontend && npm test -- workspacePreviewLayout.test.ts`

Expected: PASS with 3 tests passing.

### Task 4: Turn the project and asset pages into exhibition-style library surfaces

**Files:**
- Modify: `frontend/src/components/CoCreationHistoryPage.tsx`
- Test: `frontend/tests/cocreationLibrary.helpers.test.ts`

- [ ] **Step 1: Use the library helper test as the non-regression guard**

Run: `cd frontend && npm test -- cocreationLibrary.helpers.test.ts`

Expected: PASS with 6 tests passing.

- [ ] **Step 2: Upgrade the project library landing into a cleaner project wall**

```tsx
/* frontend/src/components/CoCreationHistoryPage.tsx */
<div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
  {groupedProjects.map((project) => (
    <button className="overflow-hidden rounded-[28px] border border-slate-200 bg-white text-left shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-lg">
      ...
    </button>
  ))}
</div>
```

- [ ] **Step 3: Upgrade the project detail and asset library surfaces with the same visual language**

```tsx
/* frontend/src/components/CoCreationHistoryPage.tsx */
<section className="overflow-hidden rounded-[32px] border border-slate-200 bg-white shadow-sm">
  <div className="border-b border-slate-200 bg-[linear-gradient(135deg,#ffffff_0%,#f7f8fc_50%,#eef2ff_100%)] px-6 py-6">
    ...
  </div>
</section>
```

- [ ] **Step 4: Re-run the helper regression test**

Run: `cd frontend && npm test -- cocreationLibrary.helpers.test.ts`

Expected: PASS with 6 tests passing.

### Task 5: Run full verification

**Files:**
- No new files

- [ ] **Step 1: Run the full frontend test suite**

Run: `cd frontend && npm test`

Expected: PASS with all test files green.

- [ ] **Step 2: Run TypeScript verification**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`

Expected: exit code 0 and no type errors.

- [ ] **Step 3: Run the production build**

Run: `cd frontend && npm run build`

Expected: Vite build succeeds; chunk size warnings are acceptable but build must exit 0.
