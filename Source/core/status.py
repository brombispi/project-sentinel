from enum import Enum


class RecoveryStatus:
    NEW = "NEW"
    ASSESSING = "ASSESSING"
    AWAITING_CUSTOMER_RESPONSE = "AWAITING_CUSTOMER_RESPONSE"
    READY_FOR_IMAGING = "READY_FOR_IMAGING"
    IMAGING = "IMAGING"
    READY_FOR_RECOVERY = "READY_FOR_RECOVERY"
    RECOVERING = "RECOVERING"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RecoveryOutcome(str, Enum):
    """
    Operator-selected recovery outcome recorded at case finalization.

    This is an operator decision owned by the SENTINEL workflow. It is never
    derived from recovered file counts or statistics.
    """

    SUCCESSFUL = "SUCCESSFUL"
    PARTIAL = "PARTIAL"
    UNSUCCESSFUL = "UNSUCCESSFUL"