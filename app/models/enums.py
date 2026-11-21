from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    TRESORIER = "tresorier"
    MEMBRE = "membre"
    ENCADRANT = "encadrant"


class MediaCategory(str, Enum):
    PHOTO = "photo"
    LOGO = "logo"
    JUSTIFICATIF = "justificatif"
    DOCUMENT = "document"
    AUTRE = "autre"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class SubscriptionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"


class DonationStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class DonationFrequency(str, Enum):
    PONCTUEL = "ponctuel"
    MENSUEL = "mensuel"


class PaymentMethod(str, Enum):
    CARTE = "carte"
    BANCONTACT = "bancontact"
    MOBILE_MONEY = "mobile_money"
    ESPECES = "especes"
    VIREMENT = "virement"


class Currency(str, Enum):
    EUR = "EUR"
    FCFA = "FCFA"


class LedgerDirection(str, Enum):
    ENTREE = "entree"
    SORTIE = "sortie"


class LedgerStatus(str, Enum):
    A_CONTRESIGNER = "a_contresigner"
    VALIDEE = "validee"
    REJETEE = "rejetee"


class LedgerEntryType(StrEnum):
    OPERATION = "operation"
    VALIDATION = "validation"
    REJET = "rejet"


class LedgerEntryType(str, Enum):
    OPERATION = "operation"
    VALIDATION = "validation"
    REJET = "rejet"


class TaskStatus(str, Enum):
    CANDIDATE = "candidate"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ARCHIVED = "archived"
