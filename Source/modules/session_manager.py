from modules.manifest import write_case_manifest


def save_case(session, device, assessment):
    """
    Persist the current recovery case state.
    """

    write_case_manifest(session, device, assessment)


def update_status(session, status, device, assessment):
    """
    Update the recovery session status and persist the case.
    """

    session.status = status
    save_case(session, device, assessment)