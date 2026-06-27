from dataclasses import dataclass


@dataclass
class RecoverySession:
    """
    Represents one recovery session.

    The session stores information about a recovery case.
    It does not perform recovery.
    """

    session_id: str