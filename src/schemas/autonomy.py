from enum import Enum


class AutonomyState(str, Enum):
    PROPOSE_ONLY = "propose_only"
    AUTO_WITHIN_BOUNDS = "auto_within_bounds"


class AutonomyEventType(str, Enum):
    GRANTED = "granted"
    REVOKED = "revoked"
    DEMOTED = "demoted"
    PROMOTION_PROPOSED = "promotion_proposed"
    EXECUTOR_REJECTED = "executor_rejected"