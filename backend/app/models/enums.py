import enum


class Role(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ORG_ADMIN = "org_admin"
    SALES_HEAD = "sales_head"
    REGIONAL_MANAGER = "regional_manager"
    SALES_MANAGER = "sales_manager"
    SALES_EXECUTIVE = "sales_executive"
    ANALYST = "analyst"


# Roles that may see every salesperson's data within their organisation.
ORG_WIDE_ROLES = frozenset({Role.SUPER_ADMIN, Role.ORG_ADMIN, Role.SALES_HEAD, Role.ANALYST})
ADMIN_ROLES = frozenset({Role.SUPER_ADMIN, Role.ORG_ADMIN})


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class CustomerStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class OpportunityStage(str, enum.Enum):
    PROSPECTING = "prospecting"
    QUALIFICATION = "qualification"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


OPEN_STAGES = (
    OpportunityStage.PROSPECTING,
    OpportunityStage.QUALIFICATION,
    OpportunityStage.PROPOSAL,
    OpportunityStage.NEGOTIATION,
)


class ActivityType(str, enum.Enum):
    VISIT = "visit"
    CALL = "call"
    MEETING = "meeting"
    WHATSAPP = "whatsapp"


class ActivityStatus(str, enum.Enum):
    DONE = "done"
    PLANNED = "planned"  # a follow-up; overdue once its date has passed


class ImportEntity(str, enum.Enum):
    CUSTOMERS = "customers"
    PRODUCTS = "products"
    ORDERS = "orders"
    TARGETS = "targets"
    OPPORTUNITIES = "opportunities"
    ACTIVITIES = "activities"


class ImportStatus(str, enum.Enum):
    PREVIEWED = "previewed"
    COMPLETED = "completed"
    FAILED = "failed"
