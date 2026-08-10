from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.conversation import Conversation
from app.models.orchestration import WorkflowInstance
from app.models.quote import QuoteLineItem, QuoteRecord
from app.services.agents.quote_agent import quote_agent
from app.services.quote_service import QuoteInput, QuoteLineInput, quote_service
from app.services.workspace_graph_service import workspace_graph_service


@pytest.fixture()
def factory(tmp_path: Path) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'quote-service.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


def test_quote_service_persists_backend_quote_and_line_items(
    factory: sessionmaker[Session],
) -> None:
    workflow_id = uuid4()
    payload = QuoteInput(
        user_id="alice",
        project_id="project-alpha",
        workflow_id=workflow_id,
        quantity=10,
        material_lines=(
            QuoteLineInput(category="material", name="fabric", unit="m", quantity=5, unit_price=20),
            QuoteLineInput(category="material", name="frame", unit="set", quantity=1, unit_price=100),
        ),
        process_lines=(
            QuoteLineInput(category="process", name="sewing", unit="hour", quantity=2, unit_price=30),
        ),
        labor_lines=(
            QuoteLineInput(category="labor", name="assembly", unit="hour", quantity=1, unit_price=50),
        ),
        loss_rate=Decimal("0.08"),
        overhead_rate=Decimal("0.12"),
        margin_rate=Decimal("0.25"),
        input_snapshot={"product": "sofa"},
    )

    with factory() as db:
        db.add(
            WorkflowInstance(
                id=workflow_id,
                user_id="alice",
                project_id="project-alpha",
                conversation_id=None,
                status="running",
                input_snapshot={},
                output_snapshot={},
            )
        )
        db.flush()
        quote = quote_service.create_quote(db, payload)
        db.commit()

        record = db.get(QuoteRecord, quote.id)
        items = db.scalars(select(QuoteLineItem).where(QuoteLineItem.quote_id == quote.id)).all()

    assert record is not None
    assert record.project_id == "project-alpha"
    assert record.pricing_source == "seeded_default"
    assert record.material_cost == Decimal("200.00")
    assert record.process_cost == Decimal("60.00")
    assert record.labor_cost == Decimal("50.00")
    assert record.subtotal == Decimal("374.98")
    assert record.final_quote == Decimal("499.97")
    assert len(items) == 4
    assert {item.project_id for item in items} == {"project-alpha"}


def test_quote_agent_creates_workspace_node_and_backend_quote(
    factory: sessionmaker[Session],
) -> None:
    with factory() as db:
        conversation = Conversation(user_id="alice", project_id="project-alpha", title="sofa")
        db.add(conversation)
        db.flush()
        source = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            node_type="design_direction",
            status="completed",
            title="方案 A",
            summary="白色三人位布艺沙发，圆润扶手，棉麻面料",
            agent_key="design_agent",
            input_data={"size": "220cm"},
        )

        node = quote_agent.create_quote_node(
            db=db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            project_name="布艺沙发",
            source_node=source,
        )
        db.commit()

        records = db.scalars(select(QuoteRecord)).all()
        items = db.scalars(select(QuoteLineItem)).all()

    assert node.node_type == "quote"
    assert node.status == "completed"
    assert len(records) == 1
    assert records[0].project_id == "project-alpha"
    assert node.output_data["quoteRecordId"] == str(records[0].id)
    assert len(items) >= 3
