from enum import StrEnum


class ProductStatus(StrEnum):
    APPROVED = "approved"
    ARCHIVED = "archived"


class SupplierStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ProcurementStatus(StrEnum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ORDERED = "ordered"
    SHIPPED = "shipped"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    SHIPPED = "shipped"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class InventoryMovementType(StrEnum):
    RECEIPT = "receipt"
    RESERVATION = "reservation"
    RELEASE = "release"
    SHIPMENT = "shipment"
    RETURN = "return"
    ADJUSTMENT = "adjustment"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    REQUESTED = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    ARCHIVED = "archived"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    PROCESSING = "processing"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"
    REFUNDED = "refunded"


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    RECEIVED = "received"
    REJECTED = "rejected"
    COMPLETED = "completed"


class RefundStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConversationStatus(StrEnum):
    OPEN = "open"
    AWAITING_APPROVAL = "awaiting_approval"
    ESCALATED = "escalated"
    CLOSED = "closed"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    FAILED = "failed"


class CustomerServiceDecision(StrEnum):
    AUTO_RESPOND = "AUTO_RESPOND"
    DRAFT_FOR_APPROVAL = "DRAFT_FOR_APPROVAL"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"


class CustomerIntent(StrEnum):
    PRODUCT_ENQUIRY = "product_enquiry"
    ORDER_STATUS = "order_status"
    DELIVERY_QUESTION = "delivery_question"
    RETURN_REQUEST = "return_request"
    REFUND_ENQUIRY = "refund_enquiry"
    COMPLAINT = "complaint"
    COMMON_MARKETPLACE_MESSAGE = "common_marketplace_message"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class EventStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class HandlerReceiptStatus(StrEnum):
    COMPLETED = "completed"
