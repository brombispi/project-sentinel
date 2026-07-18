import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from core.status import RecoveryStatus
from modules.echo import log_info, log_error, log_warning, log_operator
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
        result["code"] = "RELOCATE_LOCAL_NOT_FOUND"
        result["message"] = "Local recovery case folder not found."
        log_error(session, "ARCHIVE", result["message"])
        return result

    if not Path(mount_point).is_dir():
        result["code"] = "RELOCATE_MOUNT_NOT_FOUND"
        result["display_args"] = {"mount_point": mount_point}
        result["message"] = f"Destination mount point not found: {mount_point}"
        log_error(session, "ARCHIVE", result["message"])
        return result

    if dest_path.exists():
        result["code"] = "RELOCATE_DESTINATION_EXISTS"
        result["display_args"] = {"dest_path": str(dest_path)}
        result["message"] = f"Destination case folder already exists: {dest_path}"
        log_error(session, "ARCHIVE", result["message"])
        return result

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(local_path), str(dest_path))
    except OSError as error:
        result["code"] = "RELOCATE_FAILED"
        result["display_args"] = {"error": str(error)}
        result["message"] = f"Recovery case relocation failed: {error}"
        log_error(session, "ARCHIVE", result["message"])
        return result

    session.recovery_path = str(dest_path)
    result["success"] = True
    result["status"] = "completed"
    result["code"] = "RELOCATE_SUCCESS"
    result["display_args"] = {"dest_path": str(dest_path)}
    result["message"] = f"Recovery case relocated to {dest_path}"
    log_info(session, "ARCHIVE", result["message"])
    return result


IMAGE_FILENAME = "source.img"
MAP_FILENAME = "source.map"
SHA256_FILENAME = "source.sha256"
ACQUISITION_SOURCE_FILENAME = "acquisition_source.json"


class AcquisitionSourceError(Exception):
    """Raised when acquisition_source.json cannot be read or validated."""


class FingerprintEvidenceError(Exception):
    """Raised when source.sha256 cannot be read or validated."""


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
        result["code"] = "ACQUISITION_NO_ARTIFACTS"
        result["message"] = "No acquisition artifacts present."
        return result

    if image_exists != map_exists:
        missing = MAP_FILENAME if image_exists else IMAGE_FILENAME
        present = IMAGE_FILENAME if image_exists else MAP_FILENAME
        result["state"] = "inconsistent_artifacts"
        result["code"] = "ACQUISITION_INCONSISTENT"
        result["display_args"] = {"present": present, "missing": missing}
        result["message"] = (
            f"Inconsistent acquisition artifacts: {present} exists "
            f"but {missing} is missing."
        )
        return result

    if sha_exists:
        result["state"] = "completed_canonical"
        result["code"] = "ACQUISITION_COMPLETED_CANONICAL"
        result["message"] = (
            "Canonical acquisition is complete and fingerprint exists."
        )
        return result

    map_classification = classify_ddrescue_map_status(paths["map_path"])
    result["map_status"] = map_classification["status"]
    result["current_status"] = map_classification["current_status"]

    if map_classification["status"] == "unreadable":
        result["state"] = "invalid_map"
        result["code"] = "ACQUISITION_INVALID_MAP"
        result["message"] = "ddrescue map state is unreadable."
        return result

    if map_classification["status"] == "finished":
        result["state"] = "imaging_complete_fingerprint_missing"
        result["code"] = "ACQUISITION_FINGERPRINT_MISSING"
        result["message"] = (
            "Imaging is complete but SHA-256 fingerprint is missing."
        )
        return result

    result["state"] = "incomplete_ddrescue"
    result["code"] = "ACQUISITION_INCOMPLETE_DDRESCUE"
    result["message"] = "Incomplete ddrescue acquisition may be resumed."
    return result


def _normalize_identity_text(value):
    if value is None:
        return ""

    return str(value).strip()


def _serial_is_trustworthy(serial):
    normalized = _normalize_identity_text(serial)
    return normalized not in ("", "Unknown", "unknown", "N/A", "n/a")


# Stable source-identity comparison codes (single identity authority).
IDENTITY_MATCHES = "IDENTITY_MATCHES"
IDENTITY_SIZE_UNDETERMINED = "IDENTITY_SIZE_UNDETERMINED"
IDENTITY_SIZE_MISMATCH = "IDENTITY_SIZE_MISMATCH"
IDENTITY_SERIAL_MISMATCH = "IDENTITY_SERIAL_MISMATCH"
IDENTITY_SERIAL_UNSTABLE = "IDENTITY_SERIAL_UNSTABLE"
IDENTITY_SERIAL_UNAVAILABLE = "IDENTITY_SERIAL_UNAVAILABLE"
IDENTITY_MODEL_MISMATCH = "IDENTITY_MODEL_MISMATCH"


def compare_source_identity(
    *,
    recorded_serial,
    current_serial,
    recorded_model,
    current_model,
    recorded_size,
    current_size,
):
    """
    Single identity authority: decide whether a recorded source identity
    matches a current device using exact size, serial-trust and model rules.

    Returns a stable IDENTITY_* code. IDENTITY_MATCHES means the identities
    match. Path is intentionally not part of matching; callers handle any
    path-change signalling themselves.
    """

    if current_size is None:
        return IDENTITY_SIZE_UNDETERMINED

    if recorded_size != current_size:
        return IDENTITY_SIZE_MISMATCH

    recorded_serial_trustworthy = _serial_is_trustworthy(recorded_serial)
    current_serial_trustworthy = _serial_is_trustworthy(current_serial)

    if recorded_serial_trustworthy and current_serial_trustworthy:
        if _normalize_identity_text(recorded_serial) != _normalize_identity_text(
            current_serial
        ):
            return IDENTITY_SERIAL_MISMATCH
    elif recorded_serial_trustworthy != current_serial_trustworthy:
        return IDENTITY_SERIAL_UNSTABLE
    else:
        return IDENTITY_SERIAL_UNAVAILABLE

    if _normalize_identity_text(recorded_model) != _normalize_identity_text(
        current_model
    ):
        return IDENTITY_MODEL_MISMATCH

    return IDENTITY_MATCHES


def _parse_acquisition_source_payload(acquisition_source_path):
    acquisition_source_path = Path(acquisition_source_path)

    try:
        payload = json.loads(
            acquisition_source_path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise AcquisitionSourceError(
            f"acquisition_source.json is malformed: {acquisition_source_path}"
        ) from error
    except OSError as error:
        raise AcquisitionSourceError(
            f"acquisition_source.json could not be read: {acquisition_source_path}"
        ) from error

    if not isinstance(payload, dict):
        raise AcquisitionSourceError(
            "acquisition_source.json must contain a JSON object: "
            f"{acquisition_source_path}"
        )

    return payload


def read_acquisition_source(recovery_path):
    """
    Read persisted acquisition-source evidence for a recovery case.

    Read-only. Does not repair or rewrite evidence.
    """

    acquisition_source_path = _artifact_paths(recovery_path)[
        "acquisition_source_path"
    ]

    if not acquisition_source_path.is_file():
        return None

    return _parse_acquisition_source_payload(acquisition_source_path)


def _load_acquisition_source(acquisition_source_path):
    if not acquisition_source_path.is_file():
        return None

    try:
        return _parse_acquisition_source_payload(acquisition_source_path)
    except AcquisitionSourceError:
        return None


def _format_fingerprint_evidence_content(
    *,
    algorithm,
    digest,
    image_filename,
    image_size_bytes,
    timestamp,
):
    return (
        f"algorithm={algorithm}\n"
        f"digest={digest}\n"
        f"image={image_filename}\n"
        f"size_bytes={image_size_bytes}\n"
        f"timestamp={timestamp}\n"
    )


def _parse_fingerprint_evidence_payload(fingerprint_evidence_path):
    fingerprint_evidence_path = Path(fingerprint_evidence_path)

    try:
        content = fingerprint_evidence_path.read_text(encoding="utf-8")
    except OSError as error:
        raise FingerprintEvidenceError(
            f"source.sha256 could not be read: {fingerprint_evidence_path}"
        ) from error

    fields = {}

    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()

        if not stripped:
            continue

        if "=" not in stripped:
            raise FingerprintEvidenceError(
                f"source.sha256 is malformed: {fingerprint_evidence_path} "
                f"(line {line_number})"
            )

        key, value = stripped.split("=", 1)

        if key in fields:
            raise FingerprintEvidenceError(
                f"source.sha256 contains duplicate field {key!r}: "
                f"{fingerprint_evidence_path}"
            )

        fields[key] = value

    required_fields = ("algorithm", "digest", "image", "size_bytes", "timestamp")
    missing_fields = [
        field
        for field in required_fields
        if field not in fields or not str(fields[field]).strip()
    ]

    if missing_fields:
        raise FingerprintEvidenceError(
            "source.sha256 is missing required fields: "
            f"{', '.join(missing_fields)} ({fingerprint_evidence_path})"
        )

    try:
        image_size_bytes = int(fields["size_bytes"])
    except ValueError as error:
        raise FingerprintEvidenceError(
            f"source.sha256 size_bytes is invalid: {fingerprint_evidence_path}"
        ) from error

    return {
        "algorithm": fields["algorithm"],
        "digest": fields["digest"],
        "image_filename": fields["image"],
        "image_size_bytes": image_size_bytes,
        "timestamp": fields["timestamp"],
    }


def read_fingerprint_evidence(recovery_path):
    """
    Read persisted fingerprint evidence for a recovery case.

    Read-only. Does not repair or rewrite evidence.
    """

    fingerprint_evidence_path = _artifact_paths(recovery_path)["sha256_path"]

    if not fingerprint_evidence_path.is_file():
        return None

    return _parse_fingerprint_evidence_payload(fingerprint_evidence_path)


_RESUME_IDENTITY_MESSAGES = {
    IDENTITY_SIZE_UNDETERMINED: (
        "Resume refused: current source size could not be determined."
    ),
    IDENTITY_SIZE_MISMATCH: (
        "Resume refused: source size_bytes does not match "
        "acquisition_source.json."
    ),
    IDENTITY_SERIAL_MISMATCH: (
        "Resume refused: source serial does not match "
        "acquisition_source.json."
    ),
    IDENTITY_SERIAL_UNSTABLE: (
        "Resume refused: serial identity is missing or unstable."
    ),
    IDENTITY_SERIAL_UNAVAILABLE: (
        "Resume refused: trustworthy serial identity is unavailable."
    ),
    IDENTITY_MODEL_MISMATCH: (
        "Resume refused: source model does not match "
        "acquisition_source.json."
    ),
}


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
        result["code"] = "IDENTITY_SOURCE_MISSING"
        result["message"] = (
            "Resume refused: acquisition_source.json is missing."
        )
        return result

    current_size = result["current"]["size_bytes"]
    recorded_path = _normalize_identity_text(recorded.get("path"))
    current_path = _normalize_identity_text(session.source_device.path)

    code = compare_source_identity(
        recorded_serial=recorded.get("serial"),
        current_serial=session.source_device.serial,
        recorded_model=recorded.get("model"),
        current_model=session.source_device.model,
        recorded_size=recorded.get("size_bytes"),
        current_size=current_size,
    )

    if code != IDENTITY_MATCHES:
        result["code"] = code
        result["message"] = _RESUME_IDENTITY_MESSAGES[code]
        return result

    if recorded_path and current_path and recorded_path != current_path:
        result["warnings"].append(
            {
                "code": "IDENTITY_PATH_CHANGED",
                "display_args": {
                    "recorded_path": recorded_path,
                    "current_path": current_path,
                },
                "message": (
                    f"Source path changed from {recorded_path} to {current_path}; "
                    "serial and size_bytes match."
                ),
            }
        )

    result["valid"] = True
    result["code"] = "IDENTITY_MATCHES"
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
        result["code"] = "ACQUISITION_SOURCE_EXISTS"
        result["message"] = (
            "acquisition_source.json already exists and is immutable."
        )
        return result

    size_bytes = get_block_device_size_bytes(session.source_device.path)

    if size_bytes is None:
        result["code"] = "ACQUISITION_SOURCE_SIZE_UNDETERMINED"
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
        result["code"] = "ACQUISITION_SOURCE_WRITE_FAILED"
        result["display_args"] = {"error": str(error)}
        result["message"] = (
            f"acquisition_source.json could not be written: {error}"
        )
        return result

    result["success"] = True
    result["code"] = "ACQUISITION_SOURCE_RECORDED"
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
        result["code"] = "UNMOUNT_NONE_REQUIRED"
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
        result["code"] = "UNMOUNT_FAILED"
        result["message"] = (
            "One or more descendant filesystems could not be unmounted."
        )
        return result

    result["success"] = True
    result["code"] = "UNMOUNT_SUCCESS"
    result["message"] = "All requested descendant filesystems were unmounted."
    return result


def _refuse_imaging_result(message, *, code=None, display_args=None):
    result = {
        "success": False,
        "status": "refused",
        "artifacts": [],
        "message": message,
    }
    if code is not None:
        result["code"] = code
    if display_args is not None:
        result["display_args"] = display_args
    return result


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
        result["code"] = "IMAGING_DDRESCUE_NOT_INSTALLED"
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
                return _refuse_imaging_result(
                    message,
                    code="IMAGING_RESUME_REFUSED_CANONICAL",
                )

            if acquisition_state["state"] == (
                "imaging_complete_fingerprint_missing"
            ):
                message = (
                    "Resume refused: imaging is already complete."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(
                    message,
                    code="IMAGING_RESUME_REFUSED_COMPLETE",
                )

            if acquisition_state["state"] == "invalid_map":
                message = "Resume refused: ddrescue map is unreadable."
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(
                    message,
                    code="IMAGING_RESUME_REFUSED_MAP_UNREADABLE",
                )

            if acquisition_state["state"] == "inconsistent_artifacts":
                message = (
                    "Resume refused: acquisition artifacts are inconsistent."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(
                    message,
                    code="IMAGING_RESUME_REFUSED_INCONSISTENT",
                )

            message = (
                f"Resume refused: acquisition state is "
                f"{acquisition_state['state']}."
            )
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(
                message,
                code="IMAGING_RESUME_REFUSED_STATE",
                display_args={"state": acquisition_state["state"]},
            )

        identity_result = validate_source_identity_for_resume(session)

        for warning in identity_result["warnings"]:
            log_warning(session, "ARCHIVE", warning["message"])

        if not identity_result["valid"]:
            log_error(session, "ARCHIVE", identity_result["message"])
            return _refuse_imaging_result(
                identity_result["message"],
                code=identity_result.get("code"),
                display_args=identity_result.get("display_args"),
            )
    else:
        if acquisition_state["state"] != "no_acquisition":
            if acquisition_state["state"] == "completed_canonical":
                message = (
                    "Imaging refused: canonical acquisition is complete."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(
                    message,
                    code="IMAGING_REFUSED_CANONICAL",
                )

            if acquisition_state["state"] == "invalid_map":
                message = "Imaging refused: ddrescue map is unreadable."
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(
                    message,
                    code="IMAGING_REFUSED_MAP_UNREADABLE",
                )

            if acquisition_state["state"] == "inconsistent_artifacts":
                message = (
                    "Imaging refused: acquisition artifacts are inconsistent."
                )
                log_error(session, "ARCHIVE", message)
                return _refuse_imaging_result(
                    message,
                    code="IMAGING_REFUSED_INCONSISTENT",
                )

            message = (
                f"Imaging refused: acquisition state is "
                f"{acquisition_state['state']}."
            )
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(
                message,
                code="IMAGING_REFUSED_STATE",
                display_args={"state": acquisition_state["state"]},
            )

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
        return _refuse_imaging_result(
            message,
            code="IMAGING_REFUSED_MOUNTED_DESCENDANTS",
            display_args={"mount_summary": mount_summary},
        )

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
            return _refuse_imaging_result(
                message,
                code="IMAGING_RESUME_ARTIFACTS_MISSING",
            )

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
            return _refuse_imaging_result(
                message,
                code="IMAGING_RESUME_MAP_NOT_RESUMABLE",
            )

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
            return _refuse_imaging_result(
                message,
                code="IMAGING_REFUSED_ARTIFACTS_PRESENT",
            )

        acquisition_record = write_acquisition_source(session)

        if not acquisition_record["success"]:
            message = acquisition_record["message"]
            log_error(session, "ARCHIVE", message)
            return _refuse_imaging_result(
                message,
                code=acquisition_record.get("code"),
                display_args=acquisition_record.get("display_args"),
            )

        log_info(
            session,
            "ARCHIVE",
            f"New forensic imaging started: {session.source_device.path}",
        )

    try:
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
    except KeyboardInterrupt:
        artifacts = []

        if image_path.is_file():
            artifacts.append(str(image_path))

        if map_path.is_file():
            artifacts.append(str(map_path))

        result["status"] = "interrupted"
        result["interrupted"] = True
        result["code"] = "IMAGING_INTERRUPTED"
        result["message"] = "Forensic imaging interrupted by operator."
        result["artifacts"] = artifacts
        log_operator(
            session,
            "ARCHIVE",
            "Forensic imaging interrupted by operator.",
        )
        return result

    if completed.returncode == 0:
        result["success"] = True
        result["status"] = "completed"
        result["artifacts"] = [
            str(image_path),
            str(map_path),
        ]
        result["code"] = (
            "IMAGING_RESUMED_SUCCESS"
            if resume
            else "IMAGING_CREATED_SUCCESS"
        )
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
        result["code"] = "IMAGING_DDRESCUE_EXIT"
        result["display_args"] = {"exit_code": completed.returncode}
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

    from i18n import tr

    digest = hashlib.sha256()
    bytes_read = 0
    last_percent = -1
    progress_started = False

    try:
        with open(file_path, "rb") as image_file:
            if image_size == 0:
                print(
                    tr("imaging.fingerprint.progress", percent=100),
                    end="",
                    flush=True,
                )
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
                            "\r"
                            + tr("imaging.fingerprint.progress", percent=percent),
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
        result["code"] = "INTEGRITY_IMAGE_NOT_FOUND"
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
        result["code"] = "INTEGRITY_SIZE_UNDETERMINED"
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
        result["code"] = "INTEGRITY_READ_FAILED"
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
    evidence_content = _format_fingerprint_evidence_content(
        algorithm="SHA-256",
        digest=digest_hex,
        image_filename=IMAGE_FILENAME,
        image_size_bytes=image_size,
        timestamp=timestamp,
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
        result["code"] = "INTEGRITY_SAVE_FAILED"
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
    result["code"] = "INTEGRITY_HASH_RECORDED"
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


def _empty_recovered_summary():
    return {
        "recovered_file_count": 0,
        "recovered_directory_count": 0,
        "recovered_size_bytes": 0,
        "recup_directories": [],
        "recovery_present": False,
    }


def summarize_recovered_artifacts(recovery_path):
    """
    Summarize observable recovery artifacts under recovered/recup.*.

    Read-only. Does not modify recovery outputs.
    """

    recovery_path = Path(recovery_path)
    recovered_dir = recovery_path / "recovered"

    if not recovered_dir.is_dir():
        return _empty_recovered_summary()

    (
        recovered_directory_count,
        recovered_file_count,
        recovered_size_bytes,
        recup_dirs,
    ) = _count_recovered_artifacts(recovered_dir)

    recup_directories = []

    for recup_dir in recup_dirs:
        recup_path = Path(recup_dir)
        recup_directories.append(
            recup_path.relative_to(recovery_path).as_posix()
        )

    return {
        "recovered_file_count": recovered_file_count,
        "recovered_directory_count": recovered_directory_count,
        "recovered_size_bytes": recovered_size_bytes,
        "recup_directories": recup_directories,
        "recovery_present": recovered_directory_count > 0 or recovered_file_count > 0,
    }


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
        result["code"] = "PHOTOREC_NOT_INSTALLED"
        result["message"] = "PhotoRec is not installed."
        log_error(session, "ARCHIVE", result["message"])
        return result

    if not image_path.is_file():
        result["code"] = "PHOTOREC_IMAGE_NOT_FOUND"
        result["display_args"] = {"image_path": str(image_path)}
        result["message"] = f"Forensic image not found: {image_path}"
        log_error(session, "ARCHIVE", result["message"])
        return result

    if session.source_device and str(image_path) == session.source_device.path:
        result["code"] = "PHOTOREC_REFUSED_ORIGINAL"
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
        result["code"] = "PHOTOREC_ENDED_NORMALLY"
        result["message"] = "PhotoRec session ended normally."
        log_info(session, "ARCHIVE", result["message"])
    else:
        result["code"] = "PHOTOREC_EXIT_CODE"
        result["display_args"] = {"exit_code": completed.returncode}
        result["message"] = (
            f"PhotoRec session failed with exit code "
            f"{completed.returncode}."
        )
        log_error(session, "ARCHIVE", result["message"])

    return result