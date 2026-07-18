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


class RecoveryOperationType(str, Enum):
    """
    Supported recovery-operation types recorded in the append-only
    recovery_operations history (see RecoveryOperationRecord.md).

    PhotoRec is the signature-carving operation. TestDisk is the
    filesystem-aware operation that runs against a disposable working copy
    (see TestDiskIntegration.md). A member is added only when the operation
    is actually implemented (RecoveryOperationRecord.md §3.2).
    """

    PHOTOREC = "PHOTOREC"
    TESTDISK = "TESTDISK"


class RecoveryOperationState(str, Enum):
    """
    Execution-completion state of a single recovery operation attempt.

    Describes execution completion, never recovery effectiveness. Zero
    recovered items never determines COMPLETED or FAILED; the operator's
    case-level judgement lives separately in RecoveryOutcome.
    """

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"