from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    TRESORIER = "tresorier"
    MEMBRE = "membre"
    ENCADRANT = "encadrant"


class MediaCategory(StrEnum):
    PHOTO = "photo"
    LOGO = "logo"
    JUSTIFICATIF = "justificatif"
    DOCUMENT = "document"
    AUTRE = "autre"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class SubscriptionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"


class DonationStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class DonationFrequency(StrEnum):
    PONCTUEL = "ponctuel"
    MENSUEL = "mensuel"


class PaymentMethod(StrEnum):
    CARTE = "carte"
    BANCONTACT = "bancontact"
    MOBILE_MONEY = "mobile_money"
    ESPECES = "especes"
    VIREMENT = "virement"


class Currency(StrEnum):
    EUR = "EUR"
    FCFA = "FCFA"


class LedgerDirection(StrEnum):
    ENTREE = "entree"
    SORTIE = "sortie"


class LedgerStatus(StrEnum):
    A_CONTRESIGNER = "a_contresigner"
    VALIDEE = "validee"
    REJETEE = "rejetee"


class TaskStatus(StrEnum):
    CANDIDATE = "candidate"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ARCHIVED = "archived"

