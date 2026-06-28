from datetime import datetime
from pathlib import Path
from core.status import RecoveryStatus
from modules.echo import log_info
from core.session import RecoverySession
from services.session_registry import SessionRegistry


def create_recovery_folder(session_id: str) -> str:
    """
    Create the recovery folder for a session.
    """

    runtime_root = Path(__file__).resolve().parent.parent
    recovery_path = runtime_root / "Recoveries" / session_id
    recovery_path.mkdir(parents=True, exist_ok=True)
    
    (recovery_path / "images").mkdir(exist_ok=True)
    (recovery_path / "recovered").mkdir(exist_ok=True)
    (recovery_path / "exports").mkdir(exist_ok=True)
    (recovery_path / "notes").mkdir(exist_ok=True)
    (recovery_path / "reports").mkdir(exist_ok=True)

    return str(recovery_path)


def create_session():
    """
    Create a new recovery session.
    """

    registry = SessionRegistry()

    session_id = registry.next_session_id()
    recovery_path = create_recovery_folder(session_id)

    session = RecoverySession(
    session_id=session_id,
    created_at=datetime.now(),
    status=RecoveryStatus.NEW,
    recovery_path=recovery_path,
    case_name=""
    )
    log_info(
    session,
    "ARCHIVE",
    "Recovery session created."
    )

    return session