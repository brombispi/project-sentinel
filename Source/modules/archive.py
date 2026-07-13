import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from core.status import RecoveryStatus
from modules.echo import log_info, log_error, log_warning
from modules.manifest import write_initial_case_manifest
from core.session import RecoverySession
from services.session_registry import SessionRegistry
from modules.storage_query import (
    classify_ddrescue_map_status,
    find_mounted_descendants,
    get_block_device_size_bytes,
    get_logical_sector_size,
    get_physical_sector_size,
)


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
        case_name="",
    )
    write_initial_case_manifest(session)
    log_info(
        session,
        "ARCHIVE",
        "Recovery session created.",
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


IMAGE_FILENAME = "source.img"
MAP_FILENAME = "source.map"
SHA256_FILENAME = "source.sha256"
ACQUISITION_SOURCE_FILENAME = "acquisition_source.json"


def _artifact_paths(recovery_path):
    recovery_path = Path(recovery_path)
    images_dir = recovery_path / "images"
    evidence_dir = recovery_path / "evidence"

    return {
        "image_path": images_dir / IMAGE_FILENAME,
        "map_path": images_dir / MAP_FILENAME,
        "sha256_path": evidence_dir / SHA256_FILENAME,
        "acquisition_source_path": evidence_dir / ACQUISITION_SOURCE_FILENAME,
    }


def classify_acquisition_state(recovery_path):
    """
    Classify acquisition state from artifact presence and map completion.
    """

    paths = _artifact_paths(recovery_path)
    image_exists = paths["image_path"].is_file()
    map_exists = paths["map_path"].is_file()
    sha_exists = paths["sha256_path"].is_file()

    result = {
        "state": None,
        "image_exists": image_exists,
        "map_exists": map_exists,
        "sha256_exists": sha_exists,
        "map_status": None,
        "current_status": None,
        "message": "",
    }

    if not image_exists and not map_exists:
        result["state"] = "no_acquisition"
        result["message"] = "No acquisition artifacts present."
        return result

    if image_exists != map_exists:
        missing = MAP_FILENAME if image_exists else IMAGE_FILENAME
        present = IMAGE_FILENAME if image_exists else MAP_FILENAME
        result["state"] = "inconsistent_artifacts"
        result["message"] = (
            f"Inconsistent acquisition artifacts: {present} exists "
            f"but {missing} is missing."
        )
        return result

    if sha_exists:
        result["state"] = "completed_canonical"
        result["message"] = (
            "Canonical acquisition is complete and fingerprint exists."
        )
        return result

    map_classification = classify_ddrescue_map_status(paths["map_path"])
    result["map_status"] = map_classification["status"]
    result["current_status"] = map_classification["current_status"]

    if map_classification["status"] == "unreadable":
        result["state"] = "invalid_map"
        result["message"] = "ddrescue map state is unreadable."
        return result

    if map_classification["status"] == "finished":
        result["state"] = "imaging_complete_fingerprint_missing"
        result["message"] = (
            "Imaging is complete but SHA-256 fingerprint is missing."
        )
        return result

    result["state"] = "incomplete_ddrescue"
    result["message"] = "Incomplete ddrescue acquisition may be resumed."
    return result


def _normalize_identity_text(value):
    if value is None:
        return ""

    return str(value).strip()


def _serial_is_trustworthy(serial):
    normalized = _normalize_identity_text(serial)
    return normalized not in ("", "Unknown", "unknown", "N/A", "n/a")


def _load_acquisition_source(acquisition_source_path):
    if not acquisition_source_path.is_file():
        return None

    try:
        return json.loads(acquisition_source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def validate_source_identity_for_resume(session):
    """
    Validate recorded acquisition identity against the current source device.
    """

    paths = _artifact_paths(session.recovery_path)
    recorded = _load_acquisition_source(paths["acquisition_source_path"])

    result = {
        "valid": False,
        "message": "",
        "warnings": [],
        "recorded": recorded,
        "current": {
            "serial": session.source_device.serial,
            "model": session.source_device.model,
            "size_bytes": get_block_device_size_bytes(session.source_device.path),
            "path": session.source_device.path,
        },
    }

    if recorded is None:
        result["message"] = (
            "Resume refused: acquisition_source.json is missing."
        )
        return result

    recorded_serial = _normalize_identity_text(recorded.get("serial"))
    current_serial = _normalize_identity_text(session.source_device.serial)
    recorded_model = _normalize_identity_text(recorded.get("model"))
    current_model = _normalize_identity_text(session.source_device.model)
    recorded_size = recorded.get("size_bytes")
    current_size = result["current"]["size_bytes"]
    recorded_path = _normalize_identity_text(recorded.get("path"))
    current_path = _normalize_identity_text(session.source_device.path)

    if current_size is None:
        result["message"] = (
            "Resume refused: current source size could not be determined."
        )
        return result

    if recorded_size != current_size:
        result["message"] = (
            "Resume refused: source size_bytes does not match "
            "acquisition_source.json."
        )
        return result

    recorded_serial_trustworthy = _serial_is_trustworthy(recorded_serial)
    current_serial_trustworthy = _serial_is_trustworthy(current_serial)

    if recorded_serial_trustworthy and current_serial_trustworthy:
        if recorded_serial != current_serial:
            result["message"] = (
                "Resume refused: source serial does not match "
                "acquisition_source.json."
            )
            return result
    elif recorded_serial_trustworthy != current_serial_trustworthy:
        result["message"] = (
            "Resume refused: serial identity is missing or unstable."
        )
        return result
    else:
        result["message"] = (
            "Resume refused: trustworthy serial identity is unavailable."
        )
        return result

    if recorded_model != current_model:
        result["message"] = (
            "Resume refused: source model does not match "
            "acquisition_source.json."
        )
        return result

    if recorded_path and current_path and recorded_path != current_path:
        result["warnings"].append(
            f"Source path changed from {recorded_path} to {current_path}; "
            "serial and size_bytes match."
        )

    result["valid"] = True
    result["message"] = "Source identity matches acquisition_source.json."
    return result


def write_acquisition_source(session):
    """
    Atomically write acquisition_source.json before the first ddrescue run.
    """

    paths = _artifact_paths(session.recovery_path)
    acquisition_source_path = paths["acquisition_source_path"]

    result = {
        "success": False,
        "message": "",
        "path": str(acquisition_source_path),
    }

    if acquisition_source_path.is_file():
        result["message"] = (
            "acquisition_source.json already exists and is immutable."
        )
        return result

    size_bytes = get_block_device_size_bytes(session.source_device.path)

    if size_bytes is None:
        result["message"] = (
            "Source size_bytes could not be determined for acquisition evidence."
        )
        return result

    payload = {
        "serial": session.source_device.serial,
        "model": session.source_device.model,
        "size_bytes": size_bytes,
        "logical_sector_size": get_logical_sector_size(
            session.source_device.path
        ),
        "physical_sector_size": get_physical_sector_size(
            session.source_device.path
        ),
        "path": session.source_device.path,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    acquisition_source_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = acquisition_source_path.with_name(
        f"{acquisition_source_path.name}.tmp"
    )

    try:
        temp_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(acquisition_source_path)
    except OSError as error:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        result["message"] = (
            f"acquisition_source.json could not be written: {error}"
        )
        return result

    result["success"] = True
    result["message"] = "acquisition_source.json recorded."
    log_info(
        session,
        "ARCHIVE",
        f"acquisition_source.json written: {acquisition_source_path}",
    )
    return result


def build_exclude_mount_targets(devices, destination_device=None):
    """
    Build mount targets that must never be unmounted during imaging safety.
    """

    exclude_targets = set()

    for device in devices:
        if device.role != "RECOVERY ENGINE":
            continue

        for item in find_mounted_descendants(device.path):
            exclude_targets.add(item["mount_target"])

    if destination_device and destination_device.mount_point:
        exclude_targets.add(destination_device.mount_point)

    return exclude_targets


def observe_source_mounted_descendants(session, exclude_mount_targets=None):
    """
    Independently observe mounted descendants for ARCHIVE enforcement.
    """

    return find_mounted_descendants(
        session.source_device.path,
        exclude_mount_targets=exclude_mount_targets,
    )


def _mount_target_depth(mount_target):
    normalized = mount_target.rstrip("/") or "/"
    if normalized == "/":
        return 0
    return normalized.count("/")


def unmount_source_descendants(session, mounted_descendants):
    """
    Unmount filesystem mount targets deepest-first.

    Does not close LUKS mappings.
    """

    result = {
        "success": False,
        "message": "",
        "results": [],
    }

    if not mounted_descendants:
        result["success"] = True
        result["message"] = "No mounted descendants required unmount."
        return result

    ordered_targets = sorted(
        {
            item["mount_target"]
            for item in mounted_descendants
            if item.get("mount_target")
        },
        key=_mount_target_depth,
        reverse=True,
    )

    for mount_target in ordered_targets:
        log_info(
            session,
            "ARCHIVE",
            f"Unmount started: {mount_target}",
        )

        completed = subprocess.run(
            ["umount", mount_target],
            capture_output=True,
            text=True,
        )

        entry = {
            "mount_target": mount_target,
            "success": completed.returncode == 0,
            "message": completed.stderr.strip() or completed.stdout.strip(),
        }
        result["results"].append(entry)

        if entry["success"]:
            log_info(
                session,
                "ARCHIVE",
                f"Unmount completed: {mount_target}",
            )
        else:
            log_error(
                session,
                "ARCHIVE",
                f"Unmount failed: {mount_target} ({entry['message']})",
            )

    failures = [item for item in result["results"] if not item["success"]]

    if failures:
        result["message"] = (
            "One or more descendant filesystems could not be unmounted."
        )
        return result

    result["success"] = True
    result["message"] = "All requested descendant filesystems were unmounted."
    return result


def _refuse_imaging_result(message):
    return {
        "success": False,
        "status": "refused",
        "artifacts": [],
        "message": message,
    }


def execute_forensic_image(session, *, resume=False, exclude_mount_targets=None):
    """
    Create or resume a forensic image of the source device using ddrescue.

    ARCHIVE independently enforces acquisition, identity, and mount safety
    immediately before ddrescue starts.
    """

    paths = _artifact_paths(session.recovery_path)
    image_path = paths["image_path"]
    map_path = paths["map_path"]

    result = {
        "success": False,
        "status": "failed",
        "artifacts": [],
        "message": "",
        "action": "resume" if resume else "new",
    }

    if not shutil.which("ddrescue"):
        result["message"] = "ddrescue is not installed."
        log_error(session, "ARCHIVE", result["message"])
        return result

    acquisition_state = classify_acquisition_state(session.recovery_path)
    log_info(
        session,
        "ARCHIVE",
        f"Acquisition state classified: {acquisition_state['state']}",
    )

    if resume:
        if acquisition_state["state"] != "incomplete_ddrescue":
            if acquisition_state["state"] == "completed_canonical":
                message = (
                    "Resume refused: canonical acquisition is complete."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(message)

            if acquisition_state["state"] == (
                "imaging_complete_fingerprint_missing"
            ):
                message = (
                    "Resume refused: imaging is already complete."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(message)

            if acquisition_state["state"] == "invalid_map":
                message = "Resume refused: ddrescue map is unreadable."
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(message)

            if acquisition_state["state"] == "inconsistent_artifacts":
                message = (
                    "Resume refused: acquisition artifacts are inconsistent."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(message)

            message = (
                f"Resume refused: acquisition state is "
                f"{acquisition_state['state']}."
            )
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(message)

        identity_result = validate_source_identity_for_resume(session)

        for warning in identity_result["warnings"]:
            log_warning(session, "ARCHIVE", warning)

        if not identity_result["valid"]:
            log_error(session, "ARCHIVE", identity_result["message"])
            return _refuse_imaging_result(identity_result["message"])
    else:
        if acquisition_state["state"] != "no_acquisition":
            if acquisition_state["state"] == "completed_canonical":
                message = (
                    "Imaging refused: canonical acquisition is complete."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(message)

            if acquisition_state["state"] == "invalid_map":
                message = "Imaging refused: ddrescue map is unreadable."
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(message)

            if acquisition_state["state"] == "inconsistent_artifacts":
                message = (
                    "Imaging refused: acquisition artifacts are inconsistent."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(message)

            message = (
                f"Imaging refused: acquisition state is "
                f"{acquisition_state['state']}."
            )
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(message)

    mounted_descendants = observe_source_mounted_descendants(
        session,
        exclude_mount_targets=exclude_mount_targets,
    )

    if mounted_descendants:
        mount_summary = ", ".join(
            f"{item['device_path']} -> {item['mount_target']}"
            for item in mounted_descendants
        )
        message = (
            "Imaging refused: mounted descendants remain on source disk: "
            f"{mount_summary}"
        )
        log_error(session, "ARCHIVE", message)
        log_error(
            session,
            "ARCHIVE",
            "Pre-ddrescue mount verification failed.",
        )
        return _refuse_imaging_result(message)

    log_info(
        session,
        "ARCHIVE",
        "Pre-ddrescue mount verification passed.",
    )

    if resume:
        if not image_path.is_file() or not map_path.is_file():
            message = (
                "Resume refused: source.img and source.map must both exist."
            )
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(message)

        map_classification = classify_ddrescue_map_status(map_path)
        log_info(
            session,
            "ARCHIVE",
            (
                "Map status before resume: "
                f"{map_classification['status']} "
                f"(current_status={map_classification['current_status']})"
            ),
        )

        if map_classification["status"] != "incomplete":
            message = "Resume refused: ddrescue map is not resumable."
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(message)

        log_info(
            session,
            "ARCHIVE",
            f"Forensic imaging resumed: {session.source_device.path}",
        )
    else:
        if image_path.exists() or map_path.exists():
            message = (
                "Imaging refused: source.img and source.map must both be absent."
            )
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(message)

        acquisition_record = write_acquisition_source(session)

        if not acquisition_record["success"]:
            message = acquisition_record["message"]
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(message)

        log_info(
            session,
            "ARCHIVE",
            f"New forensic imaging started: {session.source_device.path}",
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
        result["message"] = (
            "Forensic imaging resumed successfully."
            if resume
            else "Forensic image created successfully."
        )
        log_info(
            session,
            "ARCHIVE",
            (
                "Forensic imaging resumed successfully."
                if resume
                else "Forensic imaging completed."
            ),
        )
    else:
        result["message"] = (
            f"ddrescue exited with code {completed.returncode}."
        )
        log_error(
            session,
            "ARCHIVE",
            f"Forensic imaging failed: {result['message']}",
        )

    return result


CHUNK_SIZE = 1024 * 1024


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