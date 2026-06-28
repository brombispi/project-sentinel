from modules.manifest import write_case_manifest
from modules.echo import log_info


def save_case(session, device, assessment, intake=None):
    """
    Persist the current recovery case state.
    """

    write_case_manifest(session, device, assessment, intake=intake)


def update_status(session, status, device, assessment, intake=None):
    """
    Update the recovery session status and persist the case.
    """

    previous_status = session.status

    session.status = status

    save_case(session, device, assessment, intake=intake)

    log_info(
    session,
    "SESSION",
    f"Status changed: {previous_status} -> {status}"
)