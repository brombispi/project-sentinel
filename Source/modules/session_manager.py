from datetime import datetime

from core.status import RecoveryStatus
from modules.manifest import write_case_manifest
from modules.echo import log_info


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

    if status == RecoveryStatus.COMPLETED and not session.completed_at:
        session.completed_at = datetime.now().isoformat()

    save_case(session, intake=intake)

    log_info(
    session,
    "SESSION",
    f"Status changed: {previous_status} -> {status}"
)