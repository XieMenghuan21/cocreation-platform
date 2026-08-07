# Conversation Workspace Agent Orchestration Design

**Goal:** Convert the current co-creation platform into a single AI co-design workspace where conversation creates projects, workflow nodes, cards, assets, quotes, and deliverables, with all business state persisted in PostgreSQL.

**Decision:** This spec supersedes `docs/superpowers/plans/2026-08-05-codesign-studio-phase1-mvp.md` for the current repository. That older plan assumes a new Next.js/arq/Redis/MinIO monorepo. The current implementation must stay inside `/Users/pipi/CodeSpace/cocreation-platform` and reuse React/Vite, FastAPI, SQLAlchemy, PostgreSQL, DB-backed assets, and the existing industrial design services.

## Product Shape

The product has one core page: AI co-design workspace.

The homepage stays minimal: one prompt asking what the user wants to design, with text, image, PDF, CAD, and voice entry points. After submission, the user enters the workspace directly.

The workspace is structured as:

- Left resource center, collapsed by default: projects, files, assets, versions, quotes.
- Center conversation timeline: chat messages plus persistent workflow cards.
- Right preview panel, hidden by default: image, 3D, CAD, quote, and engineering package preview.

The user should not jump between pages during the core flow. Legacy pages can remain as compatibility routes, but their content should become workspace panels or redirect into workspace state.

## Backend Workflow

The backend owns orchestration. The frontend does not create fake progress, fake prices, or fake node completion.

The default full chain is:

1. Requirement Agent: parse user prompt and attachments into a structured requirement.
2. Project Agent: create a project, project tree, initial version, and conversation binding.
3. Design Agent: generate design directions and wait for user confirmation.
4. Render Agent: call 5090/ComfyUI for rendering. When a reference image/design image exists, this must run image editing/reference editing. It must not silently fall back to text-to-image.
5. ThreeD Agent: generate 3D assets from the confirmed design state.
6. CAD Agent: generate CAD/STEP/DXF outputs from the 3D or structured design state.
7. Quote Agent: generate BOM and quote records from material, size, process, quantity, labor, loss, overhead, and margin rules.
8. Engineering Package Agent: assemble persisted outputs into a downloadable package.

The initial execution is sequential until design confirmation. After confirmation, render and 3D can run independently. CAD depends on 3D or a structured CAD state. Quote depends on design/CAD/BOM data. Engineering package waits for selected deliverables.

## Persistence Rules

All business state must be recoverable from the database:

- sessions and auth state use server-side or HttpOnly-cookie backed APIs;
- conversations and messages are persisted;
- projects, project selection, project histories, versions, quotes, workflow instances, agent runs, events, and artifacts are persisted;
- generated files and uploaded files are stored in database-backed assets/chunks;
- browser `localStorage`, `sessionStorage`, and `IndexedDB` must not be used for business recovery.

Project isolation is mandatory. Every conversation, workflow, agent run, asset, version, quote, and package must be scoped by `project_id` and user/session identity. Two projects must never share history by accident.

## Data Model

Add these backend models:

- `WorkflowInstance`: one conversation-created workflow for one project.
- `AgentRun`: one execution record for one agent node.
- `AgentRunEvent`: append-only status/progress/event stream for an agent run.
- `AgentArtifactLink`: relation between workflow/agent runs and persisted assets.
- `QuoteRecord`: quote header with totals, margin, status, and version binding.
- `QuoteLineItem`: BOM/material/process/labor rows used to compute quote totals.

Agent statuses:

- `queued`
- `running`
- `waiting_user`
- `succeeded`
- `failed`
- `skipped`
- `cancelled`

Each agent run must store:

- `agent_type`
- `status`
- `input_snapshot`
- `output_snapshot`
- `error_code`
- `error_message`
- `retry_count`
- `started_at`
- `completed_at`

## API Shape

Add a unified orchestration API:

- `POST /api/v1/orchestrations`
- `GET /api/v1/orchestrations/{workflow_id}`
- `GET /api/v1/orchestrations/{workflow_id}/events`
- `POST /api/v1/orchestrations/{workflow_id}/actions`
- `POST /api/v1/orchestrations/{workflow_id}/agent-runs/{agent_run_id}/retry`

Existing project, asset, conversation, industrial design, and engineering package APIs can remain, but the workspace should consume orchestration as the source of truth for workflow state.

## Frontend Behavior

Replace local workflow state composition with server state:

- The timeline loads from conversations plus orchestration events.
- Cards are rendered from backend messages, agent runs, and artifacts.
- Project switching reloads conversation, workflow, assets, versions, and quote records for the selected `project_id`.
- Refresh recovery uses API reload only.
- Progress bars reflect persisted events, not timers or local guesses.
- Failed nodes expose retry from the same workflow.

Expected card groups:

- user message
- agent status
- project created
- requirement summary
- design direction confirmation
- progress
- artifact preview
- quote
- engineering package
- error/retry
- next action

## ComfyUI Contract

5090/ComfyUI is the rendering execution endpoint.

The Render Agent must distinguish:

- reference edit: required when a design image/reference image exists;
- text-to-image: allowed only for flows that explicitly do not have a reference image;
- configuration missing: fail with a visible backend error;
- edit failure: fail clearly and allow retry.

For the user-confirmed requirement "pinned to image editing", promotional scene generation must require the upstream design/reference image.

## Quote Contract

Quote values must be calculated in the backend. The frontend must not contain fixed prices or client-only quote math.

Minimum quote fields:

- material cost
- process cost
- labor cost
- loss rate
- overhead rate
- margin rate
- quantity
- subtotal
- final quote
- BOM line items
- input snapshot

If real enterprise pricing rules are not configured yet, the backend may use seeded default rules, but the quote record must make that explicit in `pricing_source`.

## Acceptance

The implementation is accepted when this end-to-end flow works:

User enters a requirement with optional reference image, the backend creates a project, runs requirement and design agents, waits for direction confirmation, calls ComfyUI reference image editing for promotional output, generates or attempts 3D/CAD, creates a backend quote, builds an engineering package, stores every artifact in the database, and restores the same state after browser refresh.

Acceptance checks:

- No browser storage is used for business recovery.
- Project histories are isolated by `project_id`.
- Every visible workflow card maps to a database record.
- Every agent has a persisted `AgentRun`.
- The render path fails instead of falling back when required reference image editing cannot run.
- Quote numbers come from backend quote records.
- Engineering packages read persisted assets, BOM, and quote records.
- A failed agent can be retried without deleting completed predecessor runs.
