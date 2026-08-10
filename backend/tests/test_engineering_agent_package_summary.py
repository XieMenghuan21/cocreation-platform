from __future__ import annotations

import importlib
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.conversation import Conversation
from app.models.orchestration import WorkflowInstance
from app.models.persistence import WorkflowTask
from app.models.quote import QuoteLineItem, QuoteRecord
from app.services.agents.engineering_agent import engineering_agent
from app.services.workspace_graph_service import workspace_graph_service

engineering_agent_module = importlib.import_module("app.services.agents.engineering_agent")


@pytest.fixture()
def factory(tmp_path: Path) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'engineering-agent.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


def test_engineering_agent_package_output_references_quote_bom_and_source_assets(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    model_step_asset_id = uuid4()
    render_asset_id = uuid4()
    package_asset_id = uuid4()
    called_task_ids: list[str] = []

    def fake_build_package(**kwargs: object) -> dict[str, object]:
        called_task_ids.append(str(kwargs["task_id"]))
        return {
            "taskId": "engineering_package_fake",
            "status": "completed",
            "packageAssetId": str(package_asset_id),
            "packageDownloadUrl": f"/api/v1/assets/{package_asset_id}/download",
            "filename": "工程包.zip",
        }

    monkeypatch.setattr(
        engineering_agent_module.engineering_package_service,
        "build_package",
        fake_build_package,
    )

    with factory() as db:
        conversation = Conversation(user_id="alice", project_id="project-alpha", title="sofa")
        db.add(conversation)
        db.add(
            WorkflowInstance(
                id=workflow_id,
                user_id="alice",
                project_id="project-alpha",
                conversation_id=conversation.id,
                status="running",
                input_snapshot={},
                output_snapshot={},
            )
        )
        db.add(
            WorkflowTask(
                id="task-model",
                user_id="alice",
                project_id="project-alpha",
                version_id=None,
                conversation_id=conversation.id,
                status="completed",
                progress=100,
                current_step="done",
                input_payload={"text": "sofa"},
                design_spec={"projectName": "布艺沙发"},
                outputs={
                    "modelStepAssetId": str(model_step_asset_id),
                    "renderPngAssetId": str(render_asset_id),
                },
                diagnostics=[],
            )
        )
        quote = QuoteRecord(
            user_id="alice",
            project_id="project-alpha",
            workflow_id=workflow_id,
            pricing_source="seeded_default",
            currency="CNY",
            quantity=1,
            material_cost=Decimal("1200.00"),
            process_cost=Decimal("800.00"),
            labor_cost=Decimal("200.00"),
            loss_rate=Decimal("0.0800"),
            overhead_rate=Decimal("0.1200"),
            margin_rate=Decimal("0.2500"),
            subtotal=Decimal("2661.12"),
            final_quote=Decimal("3548.16"),
            input_snapshot={"product": "sofa"},
        )
        quote.line_items.append(
            QuoteLineItem(
                user_id="alice",
                project_id="project-alpha",
                workflow_id=workflow_id,
                category="material",
                name="棉麻面料",
                unit="项",
                quantity=Decimal("1"),
                unit_price=Decimal("1200"),
                total_price=Decimal("1200"),
                item_metadata={},
            )
        )
        db.add(quote)
        db.flush()
        source_node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            node_type="quote",
            status="completed",
            title="报价",
            summary="报价完成",
            agent_key="quote_agent",
            output_data={"quoteRecordId": str(quote.id)},
        )

        node, text = engineering_agent.build_package_node(
            db=db,
            auth_user={"sub": "alice"},
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            project_name="布艺沙发",
            source_node=source_node,
        )
        db.commit()

    assert node is not None
    assert text.startswith("工程包已生成")
    assert called_task_ids == ["task-model"]
    assert node.output_data["quoteRecordId"] == str(quote.id)
    assert node.output_data["bomLineItemIds"]
    assert str(model_step_asset_id) in node.output_data["includedAssetIds"]
    assert str(render_asset_id) in node.output_data["includedAssetIds"]
    assert node.output_data["packageAssetId"] == str(package_asset_id)


def test_engineering_agent_requires_backend_quote_before_package(
    factory: sessionmaker[Session],
) -> None:
    with factory() as db:
        conversation = Conversation(user_id="alice", project_id="project-alpha", title="sofa")
        db.add(conversation)
        db.add(
            WorkflowTask(
                id="task-model",
                user_id="alice",
                project_id="project-alpha",
                version_id=None,
                conversation_id=conversation.id,
                status="completed",
                progress=100,
                current_step="done",
                input_payload={},
                design_spec={},
                outputs={"modelStepAssetId": str(uuid4())},
                diagnostics=[],
            )
        )
        db.flush()
        source_node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            node_type="cad",
            status="completed",
            title="CAD",
            summary="CAD 完成",
            agent_key="cad_agent",
        )

        node, text = engineering_agent.build_package_node(
            db=db,
            auth_user={"sub": "alice"},
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            project_name="布艺沙发",
            source_node=source_node,
        )

    assert node is None
    assert "报价" in text
