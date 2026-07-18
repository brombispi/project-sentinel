from datetime import datetime

from core.status import (
    RecoveryOperationState,
    RecoveryOperationType,
    RecoveryStatus,
)
from modules.manifest import write_case_manifest
from modules.echo import log_info


class RecoveryOperationError(Exception):
    """Raised when a recovery_operations invariant would be violated."""


_TERMINAL_STATES = (
    RecoveryOperationState.COMPLETED.value,
    RecoveryOperationState.FAILED.value,
    RecoveryOperationState.INTERRUPTED.value,
)


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _trailing_operation(session):
    operations = getattr(session, "recovery_operations", None) or []
    if not operations:
        return None
    return operations[-1]


def has_active_running_operation(session):
    """
    Return True when the trailing recovery operation is still RUNNING.

    At most one RUNNING operation may exist and it is always the last entry,
    so inspecting the trailing entry is sufficient.
    """

    trailing = _trailing_operation(session)
    return bool(
        trailing
        and trailing.get("state") == RecoveryOperationState.RUNNING.value
    )


def append_running_recovery_operation(session, operation_type):
    """
    Append a new RUNNING recovery-operation record (in memory only).

    Enforces the append-only invariants: a new attempt may not start while a
    prior RUNNING record is unresolved. Persistence happens through the
    following update_status/save_case call, matching the approved lifecycle.
    """

    operation_type = RecoveryOperationType(operation_type).value

    if has_active_running_operation(session):
        raise RecoveryOperationError(
            "Cannot start a new recovery operation while a prior RUNNING "
            "operation is unresolved."
        )

    session.recovery_operations.append(
        {
            "type": operation_type,
            "state": RecoveryOperationState.RUNNING.value,
            "started_at": _now_iso(),
            "finished_at": None,
        }
    )


def _resolve_trailing_running(session, terminal_state):
    if terminal_state not in _TERMINAL_STATES:
        raise RecoveryOperationError(
            f"Invalid terminal recovery-operation state: {terminal_state}"
        )

    trailing = _trailing_operation(session)

    if trailing is None or trailing.get("state") != (
        RecoveryOperationState.RUNNING.value
    ):
        raise RecoveryOperationError(
            "No active RUNNING recovery operation to resolve."
        )

    trailing["state"] = terminal_state
    trailing["finished_at"] = _now_iso()
    return trailing


def complete_recovery_operation(session, *, success):
    """
    Resolve the trailing RUNNING operation to COMPLETED or FAILED.

    State reflects execution completion as reported by ARCHIVE, never recovery
    effectiveness. Mutates memory only; the following update_status persists it.
    """

    terminal_state = (
        RecoveryOperationState.COMPLETED.value
        if success
        else RecoveryOperationState.FAILED.value
    )
    return _resolve_trailing_running(session, terminal_state)


def resolve_interrupted_recovery_operation(
    session,
    device,
    assessment,
    intake=None,
):
    """
    Finalize a stale trailing RUNNING operation as INTERRUPTED and persist it.

    Called at the workflow point where an interrupted recovery session is
    detected and the operator is informed, before another attempt may start.
    Hydration never calls this; it is an explicit workflow write. Returns True
    when a record was resolved, False when there was nothing to resolve.
    """

    if not has_active_running_operation(session):
        return False

    _resolve_trailing_running(session, RecoveryOperationState.INTERRUPTED.value)
    write_case_manifest(session, device, assessment, intake=intake)
    log_info(
        session,
        "SENTINEL",
        "Interrupted recovery operation finalized as INTERRUPTED.",
    )
    return True


def save_case(session, intake=None):
    """
    Persist the current recovery case state.
    """

    write_case_manifest(
    session,
    session.source_device,
    session.assessment,
    intake=intake
)


def update_status(session, status, device, assessment, intake=None):
    """
    Update the recovery session status and persist the case.
    """

    previous_status = session.status

    session.status = status

    # Stamp completed_at on each genuine transition into COMPLETED (not on
    # idempotent re-saves while already COMPLETED). This keeps completed_at at
    # or after the finished_at of the final recovery operation, including when
    # a case is reopened, another operation is run, and the case is finalized
    # again.
    if status == RecoveryStatus.COMPLETED and previous_status != (
        RecoveryStatus.COMPLETED
    ):
        session.completed_at = datetime.now().isoformat()

    save_case(session, intake=intake)

    log_info(
    session,
    "SESSION",
    f"Status changed: {previous_status} -> {status}"
)