# Conversation Workspace Agent Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a database-backed conversation workspace where eight backend agents create and update project workflow cards, ComfyUI handles reference-image rendering, quotes are calculated in the backend, and browser storage is not used for business recovery.

**Architecture:** Keep the current React/Vite/FastAPI/PostgreSQL stack. Add a persisted orchestration domain with workflow instances, agent runs, events, artifact links, and quote records. Reuse existing project, asset, industrial design, image edit, ComfyUI, CAD, and engineering package services through typed agent executors instead of moving workflow logic into the frontend.

**Tech Stack:** React 18 + Vite + TypeScript + Tailwind; FastAPI + SQLAlchemy + Alembic + PostgreSQL; existing DB-backed asset chunks; 5090/ComfyUI for render/image edit execution.

---

## File Map

Create backend orchestration files:

- `backend/app/models/orchestration.py`: workflow, agent run, event, and artifact link SQLAlchemy models.
- `backend/app/models/quote.py`: quote record and quote line item models.
- `backend/app/schemas/orchestration.py`: API request/response schemas and enum contracts.
- `backend/app/schemas/quote.py`: quote schemas.
- `backend/app/services/orchestration/contracts.py`: shared typed agent executor contracts.
- `backend/app/services/orchestration/runtime.py`: workflow creation, transitions, event writing, retry logic.
- `backend/app/services/orchestration/registry.py`: maps agent type to executor.
- `backend/app/services/orchestration/executors/requirement_agent.py`
- `backend/app/services/orchestration/executors/project_agent.py`
- `backend/app/services/orchestration/executors/design_agent.py`
- `backend/app/services/orchestration/executors/render_agent.py`
- `backend/app/services/orchestration/executors/threed_agent.py`
- `backend/app/services/orchestration/executors/cad_agent.py`
- `backend/app/services/orchestration/executors/quote_agent.py`
- `backend/app/services/orchestration/executors/engineering_package_agent.py`
- `backend/app/services/quote_service.py`: deterministic backend quote calculation with seeded rules.
- `backend/app/api/v1/orchestrations.py`: orchestration API.
- `backend/alembic/versions/20260807_0007_orchestration_agents_quotes.py`: migration.

Modify backend files:

- `backend/app/models/__init__.py`: export new models.
- `backend/app/api/v1/router.py`: include orchestration router.
- `backend/app/services/industrial_design_workflow_service.py`: expose reusable methods or adapters for render/3D/CAD instead of duplicating logic.
- `backend/app/services/engineering_package_service.py`: accept persisted quote/BOM/artifact inputs.
- `backend/app/config/settings.py`: add ComfyUI/render agent configuration validation where missing.

Create frontend workspace files:

- `frontend/src/features/workbench/types.ts`: typed server state.
- `frontend/src/features/workbench/workbenchApi.ts`: orchestration API client.
- `frontend/src/features/workbench/WorkbenchShell.tsx`: one-page workspace layout.
- `frontend/src/features/workbench/ResourceCenter.tsx`: collapsed resource center.
- `frontend/src/features/workbench/ConversationTimeline.tsx`: chat plus backend cards.
- `frontend/src/features/workbench/AgentRunCard.tsx`: status/progress/error/retry cards.
- `frontend/src/features/workbench/PreviewPanel.tsx`: image/3D/CAD/quote/package preview.

Modify frontend files:

- `frontend/src/App.tsx`: route core workspace to `WorkbenchShell`.
- `frontend/src/components/LandingPage.tsx`: submit prompt through orchestration start.
- `frontend/src/components/GptWorkspace.tsx`: either shrink to compatibility wrapper or migrate state into `features/workbench`.
- `frontend/src/components/GptSidebar.tsx`: replace full navigation with collapsed resource center behavior.
- `frontend/src/components/QuotesPage.tsx`: stop using client-side fixed quote values.
- `frontend/src/services/conversationService.ts`: keep messages API but avoid workflow state ownership.
- `frontend/src/services/workspaceService.ts`: remove browser-storage recovery paths if present.

Test files:

- `backend/tests/test_orchestration_runtime.py`
- `backend/tests/test_orchestration_api.py`
- `backend/tests/test_quote_service.py`
- `backend/tests/test_render_agent_reference_edit.py`
- `backend/tests/test_project_isolation.py`
- `frontend/src/features/workbench/__tests__/WorkbenchShell.test.tsx`
- `frontend/src/features/workbench/__tests__/AgentRunCard.test.tsx`

## Task 1: Add Orchestration And Quote Models

**Files:**
- Create: `backend/app/models/orchestration.py`
- Create: `backend/app/models/quote.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/20260807_0007_orchestration_agents_quotes.py`
- Test: `backend/tests/test_orchestration_runtime.py`

- [ ] **Step 1: Write model tests**

Add tests that create one project-scoped workflow, eight agent runs, events, and a quote, then assert all records carry the same `project_id`.

Run:

```bash
cd /Users/pipi/CodeSpace/cocreation-platform/backend
uv run pytest tests/test_orchestration_runtime.py -q
```

Expected before implementation: import/model failure.

- [ ] **Step 2: Add SQLAlchemy models**

Define enums as strings:

```python
AgentType = Literal[
    "requirement",
    "project",
    "design",
    "render",
    "three_d",
    "cad",
    "quote",
    "engineering_package",
]
AgentStatus = Literal["queued", "running", "waiting_user", "succeeded", "failed", "skipped", "cancelled"]
```

Columns must include `id`, `project_id`, `conversation_id`, JSON snapshots, status, retry count, timestamps, and error fields. Do not use nullable `project_id` on workflow, run, quote, or artifact link records.

- [ ] **Step 3: Add Alembic migration**

Migration creates:

- `workflow_instances`
- `agent_runs`
- `agent_run_events`
- `agent_artifact_links`
- `quote_records`
- `quote_line_items`

Add indexes on `project_id`, `workflow_id`, `agent_run_id`, and `(project_id, created_at)`.

- [ ] **Step 4: Run tests**

Run:

```bash
cd /Users/pipi/CodeSpace/cocreation-platform/backend
uv run pytest tests/test_orchestration_runtime.py -q
```

Expected: model creation and project isolation tests pass.

## Task 2: Implement Runtime And Agent Contracts

**Files:**
- Create: `backend/app/services/orchestration/contracts.py`
- Create: `backend/app/services/orchestration/runtime.py`
- Create: `backend/app/services/orchestration/registry.py`
- Create: `backend/app/services/orchestration/executors/*.py`
- Test: `backend/tests/test_orchestration_runtime.py`

- [ ] **Step 1: Write runtime state tests**

Cover:

- workflow starts with requirement agent queued;
- agent transitions append events;
- design agent can set `waiting_user`;
- retry increments retry count and preserves completed predecessor runs;
- failed render agent does not mark workflow complete.

- [ ] **Step 2: Add executor contract**

Use a small typed contract:

```python
@dataclass(frozen=True)
class AgentExecutionContext:
    db: Session
    workflow_id: str
    project_id: str
    conversation_id: str | None
    user_id: str | None
    input_snapshot: dict[str, JsonValue]

@dataclass(frozen=True)
class AgentExecutionResult:
    status: AgentRunStatus
    output_snapshot: dict[str, JsonValue]
    artifact_ids: tuple[str, ...] = ()
    next_agents: tuple[AgentType, ...] = ()
    message: str | None = None
```

Use the repo's existing JSON types rather than `any`.

- [ ] **Step 3: Implement runtime**

`OrchestrationRuntime` responsibilities:

- create workflow;
- enqueue agent run;
- run a single agent;
- persist status/event changes;
- handle `waiting_user`;
- resume after user action;
- retry failed agent;
- return a complete workflow view for the frontend.

- [ ] **Step 4: Implement registry**

Register eight executors. At this stage, executors can call existing services through adapters, but each executor must write a real `AgentRun`.

- [ ] **Step 5: Run runtime tests**

Run:

```bash
cd /Users/pipi/CodeSpace/cocreation-platform/backend
uv run pytest tests/test_orchestration_runtime.py -q
```

## Task 3: Add Orchestration API

**Files:**
- Create: `backend/app/schemas/orchestration.py`
- Create: `backend/app/api/v1/orchestrations.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_orchestration_api.py`

- [ ] **Step 1: Write API tests**

Cover:

- `POST /api/v1/orchestrations` creates workflow and returns id;
- `GET /api/v1/orchestrations/{id}` returns project-scoped runs/events;
- `POST /api/v1/orchestrations/{id}/actions` confirms design direction and resumes downstream agents;
- retry endpoint rejects another project's agent run.

- [ ] **Step 2: Implement schemas**

Create typed request/response models for start, action, retry, workflow view, agent run view, event view, and artifact view.

- [ ] **Step 3: Implement router**

Use existing auth/session dependencies. Every query must filter by user/session and `project_id` where applicable.

- [ ] **Step 4: Run API tests**

Run:

```bash
cd /Users/pipi/CodeSpace/cocreation-platform/backend
uv run pytest tests/test_orchestration_api.py -q
```

## Task 4: Wire Eight Agent Executors To Existing Services

**Files:**
- Modify: `backend/app/services/orchestration/executors/*.py`
- Modify: `backend/app/services/industrial_design_workflow_service.py`
- Modify: `backend/app/services/engineering_package_service.py`
- Test: `backend/tests/test_render_agent_reference_edit.py`
- Test: `backend/tests/test_project_isolation.py`

- [ ] **Step 1: Requirement Agent**

Use existing prompt/intent/official chat services to output:

- normalized requirement text;
- industry;
- product category;
- constraints;
- attachment asset ids.

- [ ] **Step 2: Project Agent**

Use `cocreation_history_service` or project API service to create or bind the project. Prevent duplicate project names by appending a short suffix server-side when a name already exists for the same user.

- [ ] **Step 3: Design Agent**

Generate direction cards and set status to `waiting_user`. Persist the available directions in `output_snapshot`.

- [ ] **Step 4: Render Agent**

Call ComfyUI through the existing image service. If a design/reference image is present, require image edit/reference edit. If edit input or config is missing, fail with `RENDER_REFERENCE_EDIT_REQUIRED` or `RENDER_CONFIG_MISSING`.

- [ ] **Step 5: ThreeD Agent**

Wrap the existing 3D workflow service. Persist produced model assets or a clear failure.

- [ ] **Step 6: CAD Agent**

Wrap existing CAD/build123d/ForgeCAD adapters and persist STEP/DXF/script/model asset ids.

- [ ] **Step 7: Quote Agent**

Call `QuoteService` from Task 5 and persist quote + line items.

- [ ] **Step 8: Engineering Package Agent**

Call `engineering_package_service` with persisted artifacts, quote, and BOM. The output package asset id must be linked back to the workflow.

## Task 5: Move Quotes To Backend

**Files:**
- Create: `backend/app/services/quote_service.py`
- Create: `backend/app/schemas/quote.py`
- Modify: `frontend/src/components/QuotesPage.tsx`
- Test: `backend/tests/test_quote_service.py`

- [ ] **Step 1: Write quote tests**

Test a deterministic quote:

- two material lines;
- one process line;
- quantity 10;
- loss rate 8%;
- overhead 12%;
- margin 25%;
- final quote equals computed backend total.

- [ ] **Step 2: Implement quote service**

Inputs:

- project id;
- workflow id;
- design/CAD dimensions;
- material/process hints;
- quantity;
- pricing source.

Outputs:

- quote record;
- BOM line items;
- totals.

- [ ] **Step 3: Remove frontend fake quote math**

Delete hardcoded fixed quote calculations from `QuotesPage.tsx` and any workspace card that uses static prices. Render backend `QuoteRecord` only.

## Task 6: Build The New Workspace Shell

**Files:**
- Create: `frontend/src/features/workbench/types.ts`
- Create: `frontend/src/features/workbench/workbenchApi.ts`
- Create: `frontend/src/features/workbench/WorkbenchShell.tsx`
- Create: `frontend/src/features/workbench/ResourceCenter.tsx`
- Create: `frontend/src/features/workbench/ConversationTimeline.tsx`
- Create: `frontend/src/features/workbench/AgentRunCard.tsx`
- Create: `frontend/src/features/workbench/PreviewPanel.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/LandingPage.tsx`
- Modify: `frontend/src/components/GptWorkspace.tsx`
- Test: `frontend/src/features/workbench/__tests__/WorkbenchShell.test.tsx`

- [ ] **Step 1: Add frontend types**

Mirror backend schemas without `any`. Use `unknown` only for opaque JSON snapshots and narrow before rendering.

- [ ] **Step 2: Add API client**

Implement start, fetch, events, action, and retry calls through existing `httpRequest`.

- [ ] **Step 3: Add workspace layout**

Layout:

- collapsed left resource center;
- center conversation timeline;
- right preview panel hidden until an artifact is opened.

- [ ] **Step 4: Replace local progress with server progress**

Agent cards consume `agent_runs` and `agent_run_events`. Remove timers and synthetic completion states from the main path.

- [ ] **Step 5: Project switching**

When selected project changes, reload workflow, conversation, assets, versions, and quotes by `project_id`. Clear in-memory view state that belongs to the previous project.

## Task 7: Remove Browser Business Storage

**Files:**
- Modify: `frontend/src/services/workspaceService.ts`
- Modify: `frontend/src/services/sessionBootstrap.ts`
- Modify: `frontend/src/services/conversationService.ts`
- Modify: workspace components that call browser storage
- Test: existing and new frontend storage tests

- [ ] **Step 1: Search storage usage**

Run:

```bash
cd /Users/pipi/CodeSpace/cocreation-platform
rg "localStorage|sessionStorage|indexedDB|IndexedDB" frontend/src
```

- [ ] **Step 2: Remove business recovery usage**

Allowed usage is only non-business UI preference if explicitly harmless. Project, auth, conversation, files, assets, workflow, and quote state must come from APIs.

- [ ] **Step 3: Add regression test**

Assert workspace still renders after clearing browser storage because state is loaded through the API.

## Task 8: End-To-End Verification

**Files:**
- Modify only files needed by failures discovered during verification.

- [ ] **Step 1: Backend tests**

Run:

```bash
cd /Users/pipi/CodeSpace/cocreation-platform/backend
uv run pytest tests/test_orchestration_runtime.py tests/test_orchestration_api.py tests/test_quote_service.py tests/test_render_agent_reference_edit.py tests/test_project_isolation.py -q
```

- [ ] **Step 2: Frontend tests/build**

Run:

```bash
cd /Users/pipi/CodeSpace/cocreation-platform/frontend
npm test -- --run
npm run build
```

- [ ] **Step 3: Local service smoke**

Start backend and frontend, then verify:

- homepage prompt creates a workflow;
- workflow creates project and requirement card;
- design directions wait for confirmation;
- confirmation triggers render/3D/CAD/quote/package chain;
- ComfyUI render uses reference image edit when a design image exists;
- refresh restores state from DB;
- switching projects shows different histories.

## Delivery Scope

In the two-day target, eight agents are real persisted backend nodes. They do not need to be eight separately deployed services. Separate microservices can be introduced later by replacing executor implementations behind the same runtime contract.

ComfyUI/5090 is treated as live for render execution. 910A execution can be represented by backend agent executors and existing model gateway calls until a concrete 910A endpoint is available; the API contract leaves room to swap in a 910A adapter without changing frontend state.

## Self-Review

- Spec coverage: covers one-page workspace, database-only persistence, eight agents, ComfyUI reference editing, quote backend, engineering package, project isolation, retry, and refresh recovery.
- Placeholder scan: no `TBD`, no deferred "implement later" language, and no fake frontend state accepted as delivery.
- Type consistency: agent types and statuses match the design spec.
- Scope check: focused on current repository incremental transformation, not the old new-monorepo plan.
