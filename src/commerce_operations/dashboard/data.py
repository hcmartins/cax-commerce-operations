import uuid
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce_operations.persistence.enums import (
    ApprovalStatus,
    OrderStatus,
    ProcurementStatus,
    RefundStatus,
    RunStatus,
)
from commerce_operations.persistence.models import (
    AgentRun,
    Approval,
    AuditEvent,
    CustomerMessage,
    DomainEvent,
    InventoryItem,
    ListingDraft,
    MarketplaceListing,
    Order,
    OrderItem,
    PricingDecision,
    ProcurementRequest,
    Product,
    Refund,
    WorkflowRun,
)


def _value(value):
    return value.value if hasattr(value, "value") else value


def _rows(records: Iterable, *fields: str) -> list[dict]:
    return [{field: _value(getattr(record, field)) for field in fields} for record in records]


def overview(session: Session, currency: str = "GBP") -> dict[str, Decimal | int]:
    currency = currency.upper()
    inventory = session.scalars(select(InventoryItem)).all()
    orders = session.scalars(select(Order)).all()
    revenue_statuses = {
        OrderStatus.PAID,
        OrderStatus.PROCESSING,
        OrderStatus.DISPATCHED,
        OrderStatus.DELIVERED,
        OrderStatus.RETURNED,
        OrderStatus.REFUNDED,
    }
    revenue = sum(
        (
            order.total_amount
            for order in orders
            if order.status in revenue_statuses and order.currency == currency
        ),
        Decimal(0),
    )
    realised_cost = sum(
        (
            item.quantity * item.inventory_item.cost_basis
            for order in orders
            if order.status in revenue_statuses and order.currency == currency
            for item in order.items
        ),
        Decimal(0),
    )
    refunds = session.scalar(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(
            Refund.currency == currency,
            Refund.status.in_(
                [RefundStatus.APPROVED, RefundStatus.PROCESSING, RefundStatus.COMPLETED]
            ),
        )
    )
    estimated_profit = Decimal(0)
    for item in inventory:
        latest = session.scalar(
            select(PricingDecision)
            .where(
                PricingDecision.inventory_item_id == item.id, PricingDecision.currency == currency
            )
            .order_by(PricingDecision.created_at.desc())
            .limit(1)
        )
        if latest:
            estimated_profit += latest.contribution_profit * item.available_quantity
    return {
        "stock_value": sum(
            (
                item.cost_basis * item.quantity_on_hand
                for item in inventory
                if item.currency == currency
            ),
            Decimal(0),
        ),
        "pending_procurements": session.scalar(
            select(func.count())
            .select_from(ProcurementRequest)
            .where(
                ProcurementRequest.status.in_(
                    [ProcurementStatus.PROPOSED, ProcurementStatus.AWAITING_APPROVAL]
                )
            )
        ),
        "pending_approvals": session.scalar(
            select(func.count())
            .select_from(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
        ),
        "listings_awaiting_publication": session.scalar(
            select(func.count())
            .select_from(ListingDraft)
            .where(
                ListingDraft.approval_status == ApprovalStatus.APPROVED,
                ~ListingDraft.marketplace_listing.has(),
            )
        ),
        "orders": len(orders),
        "revenue": revenue,
        "estimated_profit": estimated_profit,
        "realised_profit": revenue - realised_cost - Decimal(refunds),
        "low_stock_products": sum(
            item.available_quantity <= item.low_stock_threshold for item in inventory
        ),
        "failed_workflows": session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.status == RunStatus.FAILED)
        ),
    }


def navigation_counts(session: Session) -> dict[str, int]:
    """Return live counts used by navigation labels and attention cues."""
    return {
        "pending_approvals": session.scalar(
            select(func.count())
            .select_from(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
        ),
        "listing_drafts": session.scalar(select(func.count()).select_from(ListingDraft)),
        "failures": session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.status == RunStatus.FAILED)
        ),
    }


def automation_activity(session: Session, limit: int = 15) -> list[dict]:
    """Build a client-readable activity feed from durable events, workflows, and agent runs."""
    activity: list[dict] = []
    for event in session.scalars(
        select(DomainEvent).order_by(DomainEvent.created_at.desc()).limit(limit)
    ):
        failed = bool(event.last_error)
        activity.append(
            {
                "time": event.created_at,
                "action": event.event_type.replace("_", " ").title(),
                "product": event.payload.get("product_name", "—"),
                "agent": event.producer.replace("-", " ").title(),
                "status": "FAILED" if failed else "SUCCESS",
                "reference": event.aggregate_id,
            }
        )
    for agent in session.scalars(
        select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    ):
        status = "FAILED" if agent.status == RunStatus.FAILED else "SUCCESS"
        activity.append(
            {
                "time": agent.created_at,
                "action": f"{agent.agent_type.replace('_', ' ').title()} completed",
                "product": agent.input_reference or "—",
                "agent": agent.provider or agent.agent_type,
                "status": status,
                "reference": agent.id,
            }
        )
    for run in session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.status.in_([RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.FAILED]))
        .order_by(WorkflowRun.updated_at.desc())
        .limit(limit)
    ):
        status = {
            RunStatus.FAILED: "FAILED",
            RunStatus.RETRYING: "ACTION REQUIRED",
        }.get(run.status, "PENDING")
        activity.append(
            {
                "time": run.updated_at,
                "action": run.current_step or run.workflow_name,
                "product": "—",
                "agent": "Workflow engine",
                "status": status,
                "reference": run.id,
            }
        )
    return sorted(activity, key=lambda row: row["time"].timestamp(), reverse=True)[:limit]


def action_required(session: Session) -> list[dict]:
    """Return persisted decisions and exceptions that need human attention."""
    actions = [
        {
            "priority": "ACTION REQUIRED",
            "action": approval.requested_reason,
            "type": approval.action_type,
            "reference": approval.id,
            "destination": "Pending approvals",
            "created": approval.created_at,
        }
        for approval in session.scalars(
            select(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.created_at.desc())
        )
    ]
    actions.extend(
        {
            "priority": "FAILED",
            "action": f"Review failed {run.workflow_name.replace('_', ' ')} workflow",
            "type": run.current_step or "workflow_failure",
            "reference": run.id,
            "destination": "Failures / exceptions",
            "created": run.updated_at,
        }
        for run in session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.status == RunStatus.FAILED)
            .order_by(WorkflowRun.updated_at.desc())
        )
    )
    return sorted(actions, key=lambda row: row["created"].timestamp(), reverse=True)


def commercial_trends(session: Session, currency: str = "GBP") -> dict[str, list[dict]]:
    """Return compact chart series derived from persisted orders and inventory."""
    orders = session.scalars(
        select(Order).where(Order.currency == currency).order_by(Order.ordered_at)
    ).all()
    by_day: dict = {}
    for order in orders:
        day = order.ordered_at.date()
        row = by_day.setdefault(day, {"date": day, "revenue": Decimal(0), "orders": 0})
        row["revenue"] += order.total_amount
        row["orders"] += 1
    inventory_rows = [
        {
            "product": item.product.name,
            "available": item.available_quantity,
            "threshold": item.low_stock_threshold,
        }
        for item in session.scalars(select(InventoryItem).order_by(InventoryItem.sku))
    ]
    daily = [
        {"date": row["date"], "revenue": float(row["revenue"]), "orders": row["orders"]}
        for row in by_day.values()
    ]
    return {"daily": daily, "inventory": inventory_rows}


def product_performance(session: Session, product_id: uuid.UUID) -> dict | None:
    """Compare stored recommendation economics with realised order performance."""
    product = session.get(Product, product_id)
    if product is None or not product.procurement_requests:
        return None
    request = product.procurement_requests[0]
    predicted_roi = Decimal(str(request.recommendation_context.get("predicted_roi", 0)))
    predicted_margin = Decimal(str(request.recommendation_context.get("predicted_margin", 0)))
    items = product.inventory_items
    lines = (
        session.scalars(
            select(OrderItem).where(OrderItem.inventory_item_id.in_([item.id for item in items]))
        ).all()
        if items
        else []
    )
    units = sum(line.quantity for line in lines)
    revenue = sum((line.unit_price * line.quantity for line in lines), Decimal(0))
    cost = sum((line.inventory_item.cost_basis * line.quantity for line in lines), Decimal(0))
    realised_profit = revenue - cost
    average_price = revenue / units if units else Decimal(0)
    realised_margin = realised_profit / revenue * 100 if revenue else Decimal(0)
    realised_roi = realised_profit / cost * 100 if cost else Decimal(0)
    expected_price = max(
        (
            decision.recommended_price
            for item in items
            for decision in session.scalars(
                select(PricingDecision).where(PricingDecision.inventory_item_id == item.id)
            )
        ),
        default=Decimal(0),
    )
    return {
        "predicted_roi": predicted_roi,
        "realised_roi": realised_roi,
        "predicted_margin": predicted_margin,
        "realised_margin": realised_margin,
        "expected_price": expected_price,
        "average_price": average_price,
        "units_sold": units,
        "revenue": revenue,
        "realised_profit": realised_profit,
        "supplier": request.selected_quote.supplier.name,
        "quantity": request.requested_quantity,
        "landed_cost": request.estimated_landed_cost,
    }


def procurements(session: Session) -> list[dict]:
    records = session.scalars(
        select(ProcurementRequest).order_by(ProcurementRequest.created_at.desc())
    ).all()
    return [
        {
            "id": record.id,
            "product": record.product.name,
            "supplier": record.selected_quote.supplier.name,
            "quantity": record.requested_quantity,
            "landed cost": record.estimated_landed_cost,
            "currency": record.currency,
            "status": record.status.value,
            "expected arrival": record.expected_arrival_at,
            "created": record.created_at,
        }
        for record in records
    ]


def approvals(session: Session) -> list[dict]:
    return _rows(
        session.scalars(
            select(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.created_at.desc())
        ).all(),
        "id",
        "action_type",
        "resource_type",
        "resource_id",
        "risk_level",
        "status",
        "requested_by",
        "requested_reason",
        "expires_at",
        "created_at",
    )


def inventory(session: Session) -> list[dict]:
    return [
        {
            "id": item.id,
            "product": item.product.name,
            "SKU": item.sku,
            "location": item.storage_location,
            "on hand": item.quantity_on_hand,
            "reserved": item.reserved_quantity,
            "available": item.available_quantity,
            "unit cost": item.cost_basis,
            "stock value": item.cost_basis * item.quantity_on_hand,
            "currency": item.currency,
            "low-stock threshold": item.low_stock_threshold,
        }
        for item in session.scalars(select(InventoryItem).order_by(InventoryItem.sku)).all()
    ]


def listing_drafts(session: Session) -> list[dict]:
    return _rows(
        session.scalars(select(ListingDraft).order_by(ListingDraft.created_at.desc())).all(),
        "id",
        "marketplace",
        "sku",
        "title",
        "listing_version",
        "proposed_price",
        "currency",
        "approval_status",
        "ai_provider",
        "ai_model",
        "created_at",
    )


def published_listings(session: Session) -> list[dict]:
    return _rows(
        session.scalars(
            select(MarketplaceListing).order_by(MarketplaceListing.created_at.desc())
        ).all(),
        "id",
        "marketplace",
        "sku",
        "external_listing_id",
        "publication_status",
        "published_at",
        "synchronised_at",
        "last_error",
    )


def orders(session: Session) -> list[dict]:
    return [
        {
            "id": order.id,
            "marketplace": order.marketplace,
            "external order": order.external_order_id,
            "status": order.status.value,
            "items": sum(item.quantity for item in order.items),
            "total": order.total_amount,
            "currency": order.currency,
            "ordered": order.ordered_at,
            "dispatched": order.dispatched_at,
            "delivered": order.delivered_at,
        }
        for order in session.scalars(select(Order).order_by(Order.ordered_at.desc())).all()
    ]


def customer_messages(session: Session) -> list[dict]:
    records = session.scalars(
        select(CustomerMessage).order_by(CustomerMessage.created_at.desc())
    ).all()
    return [
        {
            "id": message.id,
            "marketplace": message.conversation.marketplace,
            "direction": message.direction.value,
            "channel": message.channel,
            "message": message.content,
            "intent": _value(message.intent),
            "classification": _value(message.classification),
            "risk": message.risk_level,
            "status": message.status.value,
            "created": message.created_at,
        }
        for message in records
    ]


def workflow_runs(session: Session) -> list[dict]:
    return _rows(
        session.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at.desc())).all(),
        "id",
        "workflow_name",
        "status",
        "current_step",
        "attempts",
        "max_attempts",
        "next_retry_at",
        "deadline_at",
        "cost_amount",
        "cost_currency",
        "created_at",
        "completed_at",
    )


def failures(session: Session) -> list[dict]:
    rows = []
    for run in session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.status == RunStatus.FAILED)
        .order_by(WorkflowRun.updated_at.desc())
    ):
        rows.append(
            {
                "time": run.updated_at,
                "source": "workflow",
                "id": run.id,
                "name": run.workflow_name,
                "error": run.error,
            }
        )
    for event in session.scalars(
        select(DomainEvent)
        .where(DomainEvent.last_error.is_not(None))
        .order_by(DomainEvent.updated_at.desc())
    ):
        rows.append(
            {
                "time": event.updated_at,
                "source": "event",
                "id": event.id,
                "name": event.event_type,
                "error": event.last_error,
            }
        )
    return sorted(rows, key=lambda row: row["time"], reverse=True)


def product_options(session: Session) -> list[tuple[str, uuid.UUID]]:
    return [
        (f"{product.name} · {product.external_product_id}", product.id)
        for product in session.scalars(select(Product).order_by(Product.name)).all()
    ]


def product_history(session: Session, product_id: uuid.UUID) -> list[dict]:
    product = session.get(Product, product_id)
    if product is None:
        return []
    entries = [
        {
            "time": product.created_at,
            "stage": "Product",
            "event": "Approved",
            "reference": product.id,
            "details": product.name,
        }
    ]
    requests = session.scalars(
        select(ProcurementRequest).where(ProcurementRequest.product_id == product_id)
    ).all()
    inventories = session.scalars(
        select(InventoryItem).where(InventoryItem.product_id == product_id)
    ).all()
    resource_ids = {product.id}
    seen_order_ids: set[uuid.UUID] = set()
    for request in requests:
        resource_ids.add(request.id)
        entries.append(
            {
                "time": request.created_at,
                "stage": "Procurement",
                "event": request.status.value,
                "reference": request.id,
                "details": f"{request.requested_quantity} units",
            }
        )
        if request.purchase_order:
            po = request.purchase_order
            resource_ids.add(po.id)
            entries.append(
                {
                    "time": po.created_at,
                    "stage": "Purchase order",
                    "event": po.status.value,
                    "reference": po.id,
                    "details": po.po_number,
                }
            )
        if request.workflow_run:
            run = request.workflow_run
            resource_ids.add(run.id)
            entries.append(
                {
                    "time": run.created_at,
                    "stage": "Workflow",
                    "event": run.status.value,
                    "reference": run.id,
                    "details": run.workflow_name,
                }
            )
    for item in inventories:
        resource_ids.add(item.id)
        for movement in item.movements:
            resource_ids.add(movement.id)
            entries.append(
                {
                    "time": movement.created_at,
                    "stage": "Inventory",
                    "event": movement.movement_type.value,
                    "reference": movement.id,
                    "details": f"{movement.quantity_delta:+d} {item.sku} — {movement.reason}",
                }
            )
        for decision in session.scalars(
            select(PricingDecision).where(PricingDecision.inventory_item_id == item.id)
        ):
            resource_ids.add(decision.id)
            entries.append(
                {
                    "time": decision.created_at,
                    "stage": "Pricing",
                    "event": decision.policy_result,
                    "reference": decision.id,
                    "details": (
                        f"{decision.currency} {decision.recommended_price} recommended · "
                        f"{decision.contribution_profit} contribution profit"
                    ),
                }
            )
        for draft in item.listing_drafts:
            resource_ids.add(draft.id)
            entries.append(
                {
                    "time": draft.created_at,
                    "stage": "Listing draft",
                    "event": draft.approval_status.value,
                    "reference": draft.id,
                    "details": f"{draft.marketplace}: {draft.title}",
                }
            )
            if draft.workflow_run:
                run = draft.workflow_run
                resource_ids.add(run.id)
                entries.append(
                    {
                        "time": run.created_at,
                        "stage": "Listing workflow",
                        "event": run.status.value,
                        "reference": run.id,
                        "details": run.workflow_name,
                    }
                )
            if draft.agent_run:
                agent = draft.agent_run
                resource_ids.add(agent.id)
                entries.append(
                    {
                        "time": agent.created_at,
                        "stage": "Listing agent",
                        "event": agent.status.value,
                        "reference": agent.id,
                        "details": (
                            f"{agent.provider or 'provider pending'} · "
                            f"{agent.model or 'model pending'}"
                        ),
                    }
                )
            if draft.marketplace_listing:
                listing = draft.marketplace_listing
                resource_ids.add(listing.id)
                entries.append(
                    {
                        "time": listing.created_at,
                        "stage": "Published listing",
                        "event": listing.publication_status.value,
                        "reference": listing.id,
                        "details": listing.external_listing_id or listing.sku,
                    }
                )
        for order_item in session.scalars(
            select(OrderItem).where(OrderItem.inventory_item_id == item.id)
        ):
            order = order_item.order
            if order.id in seen_order_ids:
                continue
            seen_order_ids.add(order.id)
            resource_ids.update({order_item.id, order.id})
            entries.append(
                {
                    "time": order.ordered_at,
                    "stage": "Sale",
                    "event": order.status.value,
                    "reference": order.id,
                    "details": (
                        f"{order_item.quantity} × {order_item.sku} · "
                        f"{order.currency} {order.total_amount}"
                    ),
                }
            )
            if order.workflow_run:
                resource_ids.add(order.workflow_run.id)
                entries.append(
                    {
                        "time": order.workflow_run.created_at,
                        "stage": "Fulfilment workflow",
                        "event": order.workflow_run.status.value,
                        "reference": order.workflow_run.id,
                        "details": order.workflow_run.current_step
                        or order.workflow_run.workflow_name,
                    }
                )
            for returned in order.returns:
                resource_ids.add(returned.id)
                entries.append(
                    {
                        "time": returned.created_at,
                        "stage": "Return",
                        "event": returned.status.value,
                        "reference": returned.id,
                        "details": f"{returned.quantity} units · {returned.reason}",
                    }
                )
            for refund in order.refunds:
                resource_ids.add(refund.id)
                entries.append(
                    {
                        "time": refund.created_at,
                        "stage": "Refund",
                        "event": refund.status.value,
                        "reference": refund.id,
                        "details": f"{refund.currency} {refund.amount} · {refund.reason}",
                    }
                )
    for audit in session.scalars(
        select(AuditEvent).where(AuditEvent.resource_id.in_(resource_ids))
    ):
        entries.append(
            {
                "time": audit.created_at,
                "stage": "Audit",
                "event": audit.action,
                "reference": audit.resource_id,
                "details": audit.reason or audit.actor_id,
            }
        )
    for event in session.scalars(
        select(DomainEvent).where(DomainEvent.aggregate_id.in_(resource_ids))
    ):
        entries.append(
            {
                "time": event.created_at,
                "stage": "Domain event",
                "event": event.event_type,
                "reference": event.id,
                "details": event.publication_status.value,
            }
        )
    return sorted(entries, key=lambda entry: entry["time"].timestamp())
