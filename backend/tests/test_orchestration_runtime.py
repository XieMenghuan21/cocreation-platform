from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.orchestration import AgentRun, AgentRunEvent, WorkflowInstance
from app.models.quote import QuoteLineItem, QuoteRecord
from app.services.orchestration.contracts import AgentExecutionResult
from app.services.orchestration.runtime import OrchestrationRuntime


@pytest.fixture()
def factory(tmp_path: Path) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'orchestration.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


def test_workflow_agent_runs_events_and_quotes_are_project_scoped(
    factory: sessionmaker[Session],
) -> None:
    with factory() as db:
        runtime = OrchestrationRuntime(db)
        workflow = runtime.create_workflow(
            user_id="alice",
            project_id="project-alpha",
            conversation_id=None,
            initial_input={"prompt": "white fabric sofa"},
        )

        for agent_type in (
            "requirement",
            "project",
            "design",
            "render",
            "three_d",
            "cad",
            "quote",
            "engineering_package",
        ):
            run = runtime.enqueue_agent(
                workflow_id=str(workflow.id),
                agent_type=agent_type,
                input_snapshot={"source": agent_type},
            )
            runtime.mark_running(run)
            runtime.mark_succeeded(
                run,
                AgentExecutionResult(
                    status="succeeded",
                    output_snapshot={"agent": agent_type},
                    artifact_ids=(),
                    next_agents=(),
                ),
            )

        quote = QuoteRecord(
            user_id="alice",
            project_id="project-alpha",
            workflow_id=workflow.id,
            pricing_source="seeded_default",
            currency="CNY",
            quantity=10,
            material_cost=100,
            process_cost=50,
            labor_cost=30,
            loss_rate=0.08,
            overhead_rate=0.12,
            margin_rate=0.25,
            subtotal=194.4,
            final_quote=243,
            input_snapshot={"size": "220cm"},
        )
        quote.line_items.append(
            QuoteLineItem(
                user_id="alice",
                project_id="project-alpha",
                workflow_id=workflow.id,
                category="material",
                name="fabric",
                unit="m",
                quantity=5,
                unit_price=20,
                total_price=100,
                item_metadata={},
            )
        )
        db.add(quote)
        db.commit()

        workflows = db.scalars(select(WorkflowInstance)).all()
        runs = db.scalars(select(AgentRun)).all()
        events = db.scalars(select(AgentRunEvent)).all()
        quotes = db.scalars(select(QuoteRecord)).all()
        items = db.scalars(select(QuoteLineItem)).all()

    assert {item.project_id for item in workflows} == {"project-alpha"}
    assert {item.project_id for item in runs} == {"project-alpha"}
    assert {item.project_id for item in events} == {"project-alpha"}
    assert {item.project_id for item in quotes} == {"project-alpha"}
    assert {item.project_id for item in items} == {"project-alpha"}
    assert len(runs) == 8
    assert {run.status for run in runs} == {"succeeded"}
    assert len(events) >= 16


def test_retry_failed_agent_preserves_completed_predecessor(
    factory: sessionmaker[Session],
) -> None:
    with factory() as db:
        runtime = OrchestrationRuntime(db)
        workflow = runtime.create_workflow(
            user_id="alice",
            project_id="project-beta",
            conversation_id=None,
            initial_input={"prompt": "portable power station"},
        )
        requirement = runtime.enqueue_agent(
            workflow_id=str(workflow.id),
            agent_type="requirement",
            input_snapshot={"prompt": "portable power station"},
        )
        runtime.mark_succeeded(
            requirement,
            AgentExecutionResult(
                status="succeeded",
                output_snapshot={"requirement": "parsed"},
                artifact_ids=(),
                next_agents=("design",),
            ),
        )
        render = runtime.enqueue_agent(
            workflow_id=str(workflow.id),
            agent_type="render",
            input_snapshot={"referenceAssetId": "missing"},
        )
        runtime.mark_failed(
            render,
            error_code="RENDER_REFERENCE_EDIT_REQUIRED",
            error_message="reference image is required",
        )
        retried = runtime.retry_agent(str(render.id))
        db.commit()

        completed = db.get(AgentRun, requirement.id)
        failed = db.get(AgentRun, render.id)
        retry = db.get(AgentRun, retried.id)

    assert completed is not None
    assert completed.status == "succeeded"
    assert failed is not None
    assert failed.status == "failed"
    assert retry is not None
    assert retry.status == "queued"
    assert retry.retry_count == 1
    assert retry.input_snapshot == {"referenceAssetId": "missing"}
