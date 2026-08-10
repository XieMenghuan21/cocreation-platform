"""Backend quote calculation and persistence."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.quote import QuoteLineItem, QuoteRecord

MONEY = Decimal("0.01")


@dataclass(frozen=True)
class QuoteLineInput:
    category: str
    name: str
    unit: str
    quantity: Decimal | int | float
    unit_price: Decimal | int | float
    note: str | None = None
    item_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class QuoteInput:
    user_id: str
    project_id: str
    workflow_id: UUID
    quantity: int
    material_lines: tuple[QuoteLineInput, ...]
    process_lines: tuple[QuoteLineInput, ...]
    labor_lines: tuple[QuoteLineInput, ...]
    loss_rate: Decimal
    overhead_rate: Decimal
    margin_rate: Decimal
    input_snapshot: dict[str, object]
    pricing_source: str = "seeded_default"
    currency: str = "CNY"


class QuoteService:
    def create_quote(self, db: Session, payload: QuoteInput) -> QuoteRecord:
        material_cost = self._sum_lines(payload.material_lines)
        process_cost = self._sum_lines(payload.process_lines)
        labor_cost = self._sum_lines(payload.labor_lines)
        base = material_cost + process_cost + labor_cost
        subtotal = self._money(base * (Decimal("1") + payload.loss_rate) * (Decimal("1") + payload.overhead_rate))
        if payload.margin_rate >= Decimal("1"):
            raise ValueError("margin_rate must be lower than 1")
        final_quote = self._money(subtotal / (Decimal("1") - payload.margin_rate))

        record = QuoteRecord(
            user_id=payload.user_id,
            project_id=payload.project_id,
            workflow_id=payload.workflow_id,
            pricing_source=payload.pricing_source,
            currency=payload.currency,
            quantity=payload.quantity,
            material_cost=material_cost,
            process_cost=process_cost,
            labor_cost=labor_cost,
            loss_rate=payload.loss_rate,
            overhead_rate=payload.overhead_rate,
            margin_rate=payload.margin_rate,
            subtotal=subtotal,
            final_quote=final_quote,
            input_snapshot=payload.input_snapshot,
        )
        db.add(record)
        db.flush()

        for line in (*payload.material_lines, *payload.process_lines, *payload.labor_lines):
            db.add(self._to_model(payload, record.id, line))
        db.flush()
        return record

    def _to_model(
        self,
        payload: QuoteInput,
        quote_id: UUID,
        line: QuoteLineInput,
    ) -> QuoteLineItem:
        quantity = self._decimal(line.quantity)
        unit_price = self._decimal(line.unit_price)
        return QuoteLineItem(
            quote_id=quote_id,
            user_id=payload.user_id,
            project_id=payload.project_id,
            workflow_id=payload.workflow_id,
            category=line.category,
            name=line.name,
            unit=line.unit,
            quantity=quantity,
            unit_price=unit_price,
            total_price=self._money(quantity * unit_price),
            note=line.note,
            item_metadata=line.item_metadata or {},
        )

    @staticmethod
    def _sum_lines(lines: tuple[QuoteLineInput, ...]) -> Decimal:
        total = Decimal("0")
        for line in lines:
            total += QuoteService._decimal(line.quantity) * QuoteService._decimal(line.unit_price)
        return QuoteService._money(total)

    @staticmethod
    def _decimal(value: Decimal | int | float) -> Decimal:
        return Decimal(str(value))

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return value.quantize(MONEY, rounding=ROUND_HALF_UP)


quote_service = QuoteService()
