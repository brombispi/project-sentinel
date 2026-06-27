from core.session import RecoverySession


def create_session():
    """
    Create a new recovery session.

    At this stage, a session only contains
    its unique identifier.
    """

    return RecoverySession(
        session_id="TEMP"
    )