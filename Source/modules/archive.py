import hashlib
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
    (recovery_path / "evidence").mkdir(exist_ok=True)

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


def relocate_recovery_case(session, mount_point):
    """
    Move the recovery case workspace to the approved destination filesystem.
    """

    result = {
        "success": False,
        "status": "failed",
        "message": "",
    }

    local_path = Path(session.recovery_path)
    dest_path = Path(mount_point) / "Recoveries" / session.session_id

    if not local_path.is_dir():
        result["message"] = "Local recovery case folder not found."
        log_error(session, "ARCHIVE", result["message"])
        return result

    if not Path(mount_point).is_dir():
        result["message"] = f"Destination mount point not found: {mount_point}"
        log_error(session, "ARCHIVE", result["message"])
        return result

    if dest_path.exists():
        result["message"] = f"Destination case folder already exists: {dest_path}"
        log_error(session, "ARCHIVE", result["message"])
        return result

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(local_path), str(dest_path))
    except OSError as error:
        result["message"] = f"Recovery case relocation failed: {error}"
        log_error(session, "ARCHIVE", result["message"])
        return result

    session.recovery_path = str(dest_path)
    result["success"] = True
    result["status"] = "completed"
    result["message"] = f"Recovery case relocated to {dest_path}"
    log_info(session, "ARCHIVE", result["message"])
    return result


def execute_forensic_image(session):
    """
    Create a forensic image of the source device using ddrescue.

    ARCHIVE executes the operation and returns the result.
    ARCHIVE does not interact with the technician.
    """

    images_dir = Path(session.recovery_path) / "images"
    image_path = images_dir / "source.img"
    map_path = images_dir / "source.map"

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
    )

    if completed.returncode == 0:
        result["success"] = True
        result["status"] = "completed"
        result["artifacts"] = [
            str(image_path),
            str(map_path),
        ]
        result["message"] = "Forensic image created successfully."
        log_info(session, "ARCHIVE", "Forensic imaging completed.")
    else:
        result["message"] = (
            f"ddrescue exited with code {completed.returncode}."
        )
        log_error(
            session,
            "ARCHIVE",
            f"Forensic imaging failed: {result['message']}"
        )

    return result


CHUNK_SIZE = 1024 * 1024
IMAGE_FILENAME = "source.img"


def _compute_sha256_digest(file_path, image_size):
    """
    Compute SHA-256 digest using chunked read-only access.
    Prints percentage progress when the whole percent changes.
    """

    digest = hashlib.sha256()
    bytes_read = 0
    last_percent = -1
    progress_started = False

    try:
        with open(file_path, "rb") as image_file:
            if image_size == 0:
                print("Fingerprinting: 100%", end="", flush=True)
                progress_started = True
            else:
                while True:
                    chunk = image_file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
                    bytes_read += len(chunk)

                    percent = (bytes_read * 100) // image_size
                    if percent != last_percent:
                        print(
                            f"\rFingerprinting: {percent}%",
                            end="",
                            flush=True,
                        )
                        last_percent = percent
                        progress_started = True

        print()
        return digest.hexdigest()
    except OSError:
        if progress_started:
            print()
        raise


def verify_forensic_image(session):
    """
    Record a SHA-256 fingerprint of the forensic image as evidence.

    ARCHIVE executes the operation and returns the result.
    ARCHIVE does not interact with the technician.
    The image file is read only; it is never modified.
    """

    image_path = Path(session.recovery_path) / "images" / IMAGE_FILENAME
    evidence_path = Path(session.recovery_path) / "evidence" / "source.sha256"

    result = {
        "success": False,
        "status": "failed",
        "artifacts": [],
        "message": "",
        "algorithm": "SHA-256",
        "digest": "",
        "image_filename": IMAGE_FILENAME,
        "image_size": 0,
        "evidence_path": str(evidence_path),
    }

    if not image_path.is_file():
        result["message"] = (
            "Forensic image not found. The image was not deleted."
        )
        log_error(
            session,
            "ARCHIVE",
            f"Forensic image fingerprint failed: {result['message']}",
        )
        return result

    try:
        image_size = image_path.stat().st_size
    except OSError as error:
        result["message"] = (
            "Forensic image size could not be determined."
        )
        log_error(
            session,
            "ARCHIVE",
            f"Forensic image fingerprint failed: {error}",
        )
        return result

    result["image_size"] = image_size

    log_info(
        session,
        "ARCHIVE",
        f"Forensic image fingerprint calculation started: {image_path}",
    )

    try:
        digest_hex = _compute_sha256_digest(image_path, image_size)
    except OSError as error:
        result["message"] = (
            "Forensic image could not be read completely."
        )
        log_error(
            session,
            "ARCHIVE",
            f"Forensic image fingerprint failed: {error}",
        )
        return result

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    evidence_content = (
        f"algorithm=SHA-256\n"
        f"digest={digest_hex}\n"
        f"image={IMAGE_FILENAME}\n"
        f"size_bytes={image_size}\n"
        f"timestamp={timestamp}\n"
    )

    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = evidence_path.with_name(f"{evidence_path.name}.tmp")

    try:
        temp_path.write_text(evidence_content, encoding="utf-8")
        temp_path.replace(evidence_path)
    except OSError as error:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        result["message"] = (
            "Fingerprint evidence could not be saved."
        )
        log_error(
            session,
            "ARCHIVE",
            f"Forensic image fingerprint failed: {error}",
        )
        return result

    result["success"] = True
    result["status"] = "completed"
    result["digest"] = digest_hex
    result["artifacts"] = [str(evidence_path)]
    result["message"] = "Hash recorded."

    log_info(
        session,
        "ARCHIVE",
        f"Forensic image fingerprint recorded: SHA-256 {digest_hex}",
    )

    return result


def _count_recovered_artifacts(recovered_dir):
    """
    Count observable recovery artifacts under recovered/recup.* directories.
    """

    import os

    recup_dirs = []
    file_count = 0
    total_bytes = 0

    try:
        candidate_paths = sorted(Path(recovered_dir).glob("recup.*"))
    except OSError:
        candidate_paths = []

    for recup_dir in candidate_paths:
        try:
            if not recup_dir.is_dir():
                continue
        except (FileNotFoundError, PermissionError, OSError):
            continue

        recup_dirs.append(str(recup_dir))

        for dirpath, _dirnames, filenames in os.walk(
            recup_dir,
            onerror=lambda _error: None,
        ):
            for filename in filenames:
                file_path = Path(dirpath) / filename
                try:
                    if not file_path.is_file():
                        continue
                    file_count += 1
                    total_bytes += file_path.stat().st_size
                except (FileNotFoundError, PermissionError, OSError):
                    continue

    return (
        len(recup_dirs),
        file_count,
        total_bytes,
        recup_dirs,
    )


def execute_photorec_recovery(session):
    """
    Recover files from the forensic image using PhotoRec.

    ARCHIVE executes the operation and returns the result.
    ARCHIVE does not interact with the technician.
    PhotoRec runs interactively; the technician controls recovery options.
    """

    image_path = Path(session.recovery_path) / "images" / "source.img"
    output_prefix = Path(session.recovery_path) / "recovered" / "recup"

    result = {
        "success": False,
        "status": "failed",
        "artifacts": [],
        "message": "",
        "recovered_directory_count": 0,
        "recovered_file_count": 0,
        "recovered_total_bytes": 0,
    }

    if not shutil.which("photorec"):
        result["message"] = "PhotoRec is not installed."
        log_error(session, "ARCHIVE", result["message"])
        return result

    if not image_path.is_file():
        result["message"] = f"Forensic image not found: {image_path}"
        log_error(session, "ARCHIVE", result["message"])
        return result

    if session.source_device and str(image_path) == session.source_device.path:
        result["message"] = "Refusing to run PhotoRec on the original device path."
        log_error(session, "ARCHIVE", result["message"])
        return result

    log_info(
        session,
        "ARCHIVE",
        f"PhotoRec session started: {image_path}"
    )

    completed = subprocess.run(
        [
            "photorec",
            "/d",
            str(output_prefix),
            str(image_path),
        ],
    )

    recovered_dir = Path(session.recovery_path) / "recovered"
    (
        result["recovered_directory_count"],
        result["recovered_file_count"],
        result["recovered_total_bytes"],
        result["artifacts"],
    ) = _count_recovered_artifacts(recovered_dir)

    if completed.returncode == 0:
        result["success"] = True
        result["status"] = "ended"
        result["message"] = "PhotoRec session ended normally."
        log_info(session, "ARCHIVE", result["message"])
    else:
        result["message"] = (
            f"PhotoRec session failed with exit code "
            f"{completed.returncode}."
        )
        log_error(session, "ARCHIVE", result["message"])

    return result