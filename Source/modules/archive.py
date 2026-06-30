import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from core.status import RecoveryStatus
from modules.echo import log_info, log_error
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


def execute_forensic_image(session):
    """
    Create a forensic image of the source device using ddrescue.

    ARCHIVE executes the operation and returns the result.
    ARCHIVE does not interact with the technician.
    """

    images_dir = Path(session.recovery_path) / "images"
    image_path = images_dir / "source.img"
    map_path = images_dir / "source.map"
    log_path = images_dir / "ddrescue.log"

    result = {
        "success": False,
        "status": "failed",
        "artifacts": [],
        "message": "",
    }

    if not shutil.which("ddrescue"):
        result["message"] = "ddrescue is not installed."
        log_error(session, "ARCHIVE", result["message"])
        return result

    log_info(
        session,
        "ARCHIVE",
        f"Forensic imaging started: {session.source_device.path}"
    )

    completed = subprocess.run(
        [
            "ddrescue",
            "-f",
            "-n",
            session.source_device.path,
            str(image_path),
            str(map_path),
        ],
        capture_output=True,
        text=True,
    )

    with open(log_path, "w") as log_file:
        if completed.stdout:
            log_file.write(completed.stdout)
        if completed.stderr:
            log_file.write(completed.stderr)

    if completed.returncode == 0:
        result["success"] = True
        result["status"] = "completed"
        result["artifacts"] = [
            str(image_path),
            str(map_path),
            str(log_path),
        ]
        result["message"] = "Forensic image created successfully."
        log_info(session, "ARCHIVE", "Forensic imaging completed.")
    else:
        result["message"] = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "ddrescue exited with an error."
        )
        log_error(
            session,
            "ARCHIVE",
            f"Forensic imaging failed: {result['message']}"
        )

    return result