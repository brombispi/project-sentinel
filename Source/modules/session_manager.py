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

    save_case(session, intake=intake)

    log_info(
    session,
    "SESSION",
    f"Status changed: {previous_status} -> {status}"
)