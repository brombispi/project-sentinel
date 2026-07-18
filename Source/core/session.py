from dataclasses import dataclass
from datetime import datetime


@dataclass
class RecoverySession:
    """
    Represents one recovery session.

    The session stores information about a recovery case.
    It does not perform recovery.
    """

    session_id: str
    created_at: datetime
    status: str
    recovery_path: str
    case_name: str = ""

    completed_at: str | None = None

    source_device: object | None = None
    destination_device: object | None = None
    assessment: object | None = None