import hashlib
import json
import os
import shutil
import stat
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
    (recovery_path / "working").mkdir(exist_ok=True)
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


# Disjoint recovered-artifact roots. Each recovery tool owns a non-overlapping
# subtree of recovered/ so per-tool counts sum without double-counting
# (TestDiskIntegration.md §8, Decision A): PhotoRec -> recovered/recup.*,
# TestDisk -> recovered/testdisk/. The summary counts each root independently
# and never scans the shared recovered/ parent as a single tree.
TESTDISK_RECOVERED_DIRNAME = "testdisk"


def _count_files_in_tree(root_dir):
    """
    Count files and total bytes under a single recovered root directory.
    """

    import os

    file_count = 0
    total_bytes = 0

    for dirpath, _dirnames, filenames in os.walk(
        root_dir,
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

    return file_count, total_bytes


def _count_testdisk_artifacts(recovered_dir):
    """
    Count observable recovery artifacts under recovered/testdisk/.

    TestDisk owns exactly the single recovered/testdisk/ root, disjoint from
    PhotoRec's recovered/recup.* roots. Returns the root directory count (0 or
    1), file count, total bytes, and the root path when present.
    """

    testdisk_dir = Path(recovered_dir) / TESTDISK_RECOVERED_DIRNAME

    try:
        if not testdisk_dir.is_dir():
            return (0, 0, 0, [])
    except (FileNotFoundError, PermissionError, OSError):
        return (0, 0, 0, [])

    file_count, total_bytes = _count_files_in_tree(testdisk_dir)

    return (1, file_count, total_bytes, [str(testdisk_dir)])


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
    Summarize observable recovery artifacts under the disjoint recovered roots
    recovered/recup.* (PhotoRec) and recovered/testdisk/ (TestDisk).

    Each root is counted independently and summed, so a recup.* file is never
    double-counted and one tool never inflates the other's totals
    (TestDiskIntegration.md §8, Decision A). Read-only; does not modify
    recovery outputs.
    """

    recovery_path = Path(recovery_path)
    recovered_dir = recovery_path / "recovered"

    if not recovered_dir.is_dir():
        return _empty_recovered_summary()

    (
        photorec_directory_count,
        photorec_file_count,
        photorec_size_bytes,
        recup_dirs,
    ) = _count_recovered_artifacts(recovered_dir)

    (
        testdisk_directory_count,
        testdisk_file_count,
        testdisk_size_bytes,
        testdisk_dirs,
    ) = _count_testdisk_artifacts(recovered_dir)

    recovered_directory_count = photorec_directory_count + testdisk_directory_count
    recovered_file_count = photorec_file_count + testdisk_file_count
    recovered_size_bytes = photorec_size_bytes + testdisk_size_bytes

    recup_directories = []

    for root_dir in recup_dirs + testdisk_dirs:
        recup_directories.append(
            Path(root_dir).relative_to(recovery_path).as_posix()
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


# ---------------------------------------------------------------------------
# TestDisk execution preparation (foundational; non-executing).
#
# These helpers implement the fail-closed prerequisite checks, the conservative
# free-space precheck, the atomic working-copy preparation, and the
# canonical-immutability guards specified in TestDiskIntegration.md (§3, §6, §7,
# §7A). They deliberately do NOT: launch TestDisk or any subprocess, construct a
# TestDisk command, create or modify host accounts, transition status, touch the
# recovery-operations lifecycle, or read configuration. Every host interaction
# (identity lookup, PATH probe, stat, statvfs, create/copy/fsync/rename/chown) is
# taken through an injected provider so the checks are pure and unit-testable
# without root. All host-specific values (recovery account name, privileged group
# names, drop-mechanism name, execution mode) are supplied by the caller from
# configuration; nothing here hard-codes an account, uid/gid, drop tool, or mode.
#
# These are ARCHIVE-internal helpers (all underscore-prefixed): they are NOT a
# public ARCHIVE API and are consumed only inside this module (by the future
# execute_testdisk_recovery) and by the unit tests, which import the private
# names explicitly.
#
# IMPORTANT: the dicts returned here are *validation results*, not
# recovery-operation results. They intentionally do NOT carry the operation
# "artifacts" field used by execute_photorec_recovery / execute_forensic_image;
# no placeholder artifact field is added merely to imitate an operation result.
# They share only the structured shape success/status/code/message (+ optional
# display_args and helper-specific data). status is "ok" for a passed check,
# "refused" for a fail-closed safety refusal, "failed" for an I/O failure, and
# "completed" for a successful preparation.
# ---------------------------------------------------------------------------

_TESTDISK_WORKING_COPY_FILENAME = "testdisk.img"
_TESTDISK_WORKING_COPY_TMP_SUFFIX = ".tmp"
_TESTDISK_LOG_FILENAME = "testdisk.log"
# The only privilege-drop mechanism this slice can construct a command for. Kept
# in sync with translator._TESTDISK_SUPPORTED_DROP_MECHANISMS; the command
# builder refuses anything else.
_TESTDISK_DROP_MECHANISM_SETPRIV = "setpriv"
# Fixed, safe system PATH for the TestDisk child. A launch-time PATH inherited
# from the (root) Sentinel environment could let a writable earlier entry shadow
# `testdisk` when setpriv re-execs it as the confined identity; a fixed PATH plus
# absolute-path execution (below) removes that vector. Reference Linux layout.
_TESTDISK_SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
# Environment variables preserved (only when present and non-empty) for the
# interactive ncurses TUI and its locale/encoding. Everything else — notably
# PYTHONPATH, LD_PRELOAD, LD_LIBRARY_PATH, and arbitrary SENTINEL_* variables —
# is dropped. HOME is intentionally omitted: TestDisk does not require it for a
# /log run with an explicit cwd, and omitting it avoids leaking a home path.
_TESTDISK_PRESERVED_ENV_VARS = ("TERM", "LANG", "LC_ALL", "LC_CTYPE")
# Conservative headroom required on the working/ filesystem in addition to a
# full-size copy of the canonical image. Not host-specific; overridable per call.
_TESTDISK_WORKING_COPY_SAFETY_MARGIN_BYTES = 64 * 1024 * 1024
_TESTDISK_WORKING_COPY_MODE = 0o600
_TESTDISK_RECOVERED_DIR_MODE = 0o700
_TESTDISK_LOG_MODE = 0o640
# Semantic execution modes (§7A). "external" covers "another compatible
# privilege-separation mechanism" (alternate drop tool, an already-confined
# runtime identity, or a container/namespace boundary) and requires a configured
# drop mechanism; it is only structurally validated here and never invoked.
_TESTDISK_SUPPORTED_EXECUTION_MODES = ("root", "sudo", "external")


def _testdisk_result(success, code, message, *, status, display_args=None, **extra):
    result = {
        "success": success,
        "status": status,
        "code": code,
        "message": message,
    }
    if display_args is not None:
        result["display_args"] = display_args
    result.update(extra)
    return result


def _default_identity_resolver(account_name):
    """
    Resolve a host account to its uid, primary gid, and full group membership.

    Supplementary groups are enumerated through the host identity service via
    os.getgrouplist(), which consults nsswitch (files, LDAP/SSSD, …) — unlike
    grp.getgrall(), which only sees locally enumerable sources and can silently
    under-report membership. Under-reporting here would be fail-open for the
    device-access/privileged-group check, so getgrouplist() is required. Returned
    gids are resolved to names for the forbidden-group comparison; the raw gids
    are also returned. Raises KeyError if the account does not exist and OSError
    if group enumeration fails (both fail closed at the caller).
    """

    import grp
    import pwd

    entry = pwd.getpwnam(account_name)
    primary_gid = entry.pw_gid

    group_gids = list(os.getgrouplist(account_name, primary_gid))

    group_names = []
    for gid in group_gids:
        try:
            group_names.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            # A gid with no name entry: keep the numeric gid (returned below) but
            # it has no name to compare against forbidden group names.
            continue

    return {
        "account": account_name,
        "uid": entry.pw_uid,
        "gid": primary_gid,
        "groups": group_names,
        "group_gids": group_gids,
    }


def _default_command_exists(name):
    return shutil.which(name) is not None


def _default_geteuid():
    return os.geteuid()


def _default_stat_provider(path):
    return os.stat(path)


def _default_statvfs_provider(path):
    return os.statvfs(path)


def _default_command_resolver(name):
    # Resolve a command name to its absolute path via PATH (or None if absent).
    return shutil.which(name)


def _default_lstat_provider(path):
    # lstat (not stat) so a symlink is reported as a symlink and never followed.
    return os.lstat(path)


def _resolve_recovery_identity(
    account_name,
    *,
    identity_resolver=_default_identity_resolver,
):
    """
    Resolve the configured confined recovery identity by account name.

    Returns the resolved identity (account, uid, gid, groups, group_gids) on
    success. Fails closed when no identity is configured, the account cannot be
    resolved, or group enumeration fails.
    """

    normalized = _normalize_identity_text(account_name)

    if not normalized:
        return _testdisk_result(
            False,
            "TESTDISK_IDENTITY_UNCONFIGURED",
            "No confined recovery identity is configured.",
            status="refused",
        )

    try:
        identity = identity_resolver(normalized)
    except KeyError:
        return _testdisk_result(
            False,
            "TESTDISK_IDENTITY_MISSING",
            f"Recovery identity does not exist: {normalized}",
            status="refused",
            display_args={"account": normalized},
        )
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_IDENTITY_LOOKUP_FAILED",
            f"Recovery identity lookup failed: {error}",
            status="refused",
            display_args={"account": normalized, "error": str(error)},
        )

    return _testdisk_result(
        True,
        "TESTDISK_IDENTITY_RESOLVED",
        f"Recovery identity resolved: {normalized}",
        status="ok",
        identity=identity,
    )


def _reject_unsafe_recovery_identity(identity, forbidden_groups):
    """
    Reject a recovery identity that is unsafe to run TestDisk under (§7A). An
    identity is unsafe if it is root-equivalent (uid 0, primary gid 0, or a
    supplementary root group) or a member of any configured privileged /
    device-access group (e.g. disk, sudo). Distinct fail-closed codes are
    returned per reason. Fail-closed.
    """

    if identity.get("uid") == 0:
        return _testdisk_result(
            False,
            "TESTDISK_IDENTITY_ROOT_UID",
            "Recovery identity must not be root (uid 0).",
            status="refused",
        )

    group_gids = set(identity.get("group_gids", []))
    if identity.get("gid") == 0 or 0 in group_gids:
        return _testdisk_result(
            False,
            "TESTDISK_IDENTITY_ROOT_GID",
            "Recovery identity must not belong to the root group (gid 0).",
            status="refused",
        )

    identity_groups = set(identity.get("groups", []))
    forbidden = {name for name in (forbidden_groups or []) if name}
    intersection = sorted(identity_groups & forbidden)

    if intersection:
        return _testdisk_result(
            False,
            "TESTDISK_IDENTITY_PRIVILEGED_GROUP",
            "Recovery identity belongs to a privileged/device-access group: "
            f"{', '.join(intersection)}",
            status="refused",
            display_args={"groups": ", ".join(intersection)},
        )

    return _testdisk_result(
        True,
        "TESTDISK_IDENTITY_SAFE",
        "Recovery identity is non-root and has no privileged/device-access "
        "group membership.",
        status="ok",
    )


def _validate_privilege_drop_mechanism(
    mechanism,
    *,
    command_exists=_default_command_exists,
):
    """
    Validate that the configured privilege-drop mechanism exists on PATH.

    Fail-closed when unconfigured or not found. Does not invoke the mechanism.
    """

    normalized = _normalize_identity_text(mechanism)

    if not normalized:
        return _testdisk_result(
            False,
            "TESTDISK_DROP_MECHANISM_UNCONFIGURED",
            "No privilege-drop mechanism is configured.",
            status="refused",
        )

    if not command_exists(normalized):
        return _testdisk_result(
            False,
            "TESTDISK_DROP_MECHANISM_MISSING",
            f"Privilege-drop mechanism not found on PATH: {normalized}",
            status="refused",
            display_args={"mechanism": normalized},
        )

    return _testdisk_result(
        True,
        "TESTDISK_DROP_MECHANISM_AVAILABLE",
        f"Privilege-drop mechanism available: {normalized}",
        status="ok",
        mechanism=normalized,
    )


def _validate_execution_mode(
    mode,
    *,
    drop_mechanism=None,
    geteuid=_default_geteuid,
    command_exists=_default_command_exists,
    sudo_command="sudo",
):
    """
    Validate that the configured execution mode is structurally usable (§7A).

    Structural only: no subprocess is run. Recognised semantic modes:
      * "root"     — Sentinel runs as root and performs the drop directly;
                     requires the current process to be root.
      * "sudo"     — the drop is wrapped in sudo; requires sudo on PATH.
      * "external" — another compatible privilege-separation mechanism
                     (alternate drop tool, an already-confined runtime identity,
                     or a container/namespace boundary); requires a configured
                     drop mechanism that exists on PATH. It is only checked
                     structurally here and is never invoked in this slice.
    Any mode name outside this set is rejected (never silently accepted).
    Fail-closed.
    """

    normalized = _normalize_identity_text(mode).lower()

    if normalized not in _TESTDISK_SUPPORTED_EXECUTION_MODES:
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTION_MODE_INVALID",
            f"Unsupported execution mode: {mode}",
            status="refused",
            display_args={"mode": str(mode)},
        )

    if normalized == "root" and geteuid() != 0:
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTION_MODE_UNUSABLE",
            "Execution mode 'root' requires Sentinel to run as root.",
            status="refused",
            display_args={"mode": normalized},
        )

    if normalized == "sudo" and not command_exists(sudo_command):
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTION_MODE_UNUSABLE",
            "Execution mode 'sudo' requires sudo on PATH.",
            status="refused",
            display_args={"mode": normalized},
        )

    if normalized == "external":
        mechanism = _validate_privilege_drop_mechanism(
            drop_mechanism, command_exists=command_exists
        )
        if not mechanism["success"]:
            return _testdisk_result(
                False,
                "TESTDISK_EXECUTION_MODE_UNUSABLE",
                "Execution mode 'external' requires a configured, available "
                "privilege-drop mechanism.",
                status="refused",
                display_args={
                    "mode": normalized,
                    "mechanism_code": mechanism["code"],
                },
            )

    return _testdisk_result(
        True,
        "TESTDISK_EXECUTION_MODE_USABLE",
        f"Execution mode structurally usable: {normalized}",
        status="ok",
        mode=normalized,
    )


def _validate_canonical_protection(
    canonical_image_path,
    recovery_identity,
    *,
    lstat_provider=_default_lstat_provider,
):
    """
    Validate that the canonical image is present and inaccessible to the
    recovery identity. The image is inspected with lstat (never followed), so a
    symlink at the canonical path is rejected even if its target would satisfy
    the owner/permission checks; the object must be a regular file. It must also
    not be owned by the recovery uid/gid and must not grant any group/other
    permission bits. Fail-closed.
    """

    path = str(canonical_image_path)

    try:
        info = lstat_provider(path)
    except FileNotFoundError:
        return _testdisk_result(
            False,
            "TESTDISK_CANONICAL_MISSING",
            f"Canonical image not found: {path}",
            status="refused",
            display_args={"path": path},
        )
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_CANONICAL_STAT_FAILED",
            f"Canonical image could not be inspected: {error}",
            status="refused",
            display_args={"path": path, "error": str(error)},
        )

    if stat.S_ISLNK(info.st_mode):
        return _testdisk_result(
            False,
            "TESTDISK_CANONICAL_IS_SYMLINK",
            f"Canonical image is a symlink; refusing to use it: {path}",
            status="refused",
            display_args={"path": path},
        )

    if not stat.S_ISREG(info.st_mode):
        return _testdisk_result(
            False,
            "TESTDISK_CANONICAL_NOT_REGULAR",
            f"Canonical image is not a regular file: {path}",
            status="refused",
            display_args={"path": path},
        )

    if info.st_uid == recovery_identity["uid"] or (
        info.st_gid == recovery_identity["gid"]
    ):
        return _testdisk_result(
            False,
            "TESTDISK_CANONICAL_OWNED_BY_RECOVERY",
            "Canonical image is owned by the recovery identity.",
            status="refused",
            display_args={"path": path},
        )

    if info.st_mode & 0o077:
        return _testdisk_result(
            False,
            "TESTDISK_CANONICAL_PERMISSIVE",
            "Canonical image grants group/other access; must be owner-only.",
            status="refused",
            display_args={"path": path, "mode": oct(info.st_mode & 0o777)},
        )

    return _testdisk_result(
        True,
        "TESTDISK_CANONICAL_PROTECTED",
        "Canonical image is protected from the recovery identity.",
        status="ok",
    )


def _validate_recovery_target(
    path,
    recovery_identity,
    required_mode,
    *,
    stat_provider=_default_stat_provider,
):
    """
    Validate that a working/output/log target is owned by the recovery identity
    (uid and gid) and has exactly the required mode. Fail-closed.
    """

    target = str(path)

    try:
        info = stat_provider(target)
    except FileNotFoundError:
        return _testdisk_result(
            False,
            "TESTDISK_TARGET_MISSING",
            f"Recovery target not found: {target}",
            status="refused",
            display_args={"path": target},
        )
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_TARGET_STAT_FAILED",
            f"Recovery target could not be inspected: {error}",
            status="refused",
            display_args={"path": target, "error": str(error)},
        )

    if info.st_uid != recovery_identity["uid"] or (
        info.st_gid != recovery_identity["gid"]
    ):
        return _testdisk_result(
            False,
            "TESTDISK_TARGET_WRONG_OWNER",
            f"Recovery target is not owned by the recovery identity: {target}",
            status="refused",
            display_args={"path": target},
        )

    if (info.st_mode & 0o777) != required_mode:
        return _testdisk_result(
            False,
            "TESTDISK_TARGET_WRONG_MODE",
            f"Recovery target has an unexpected mode: {target}",
            status="refused",
            display_args={
                "path": target,
                "expected_mode": oct(required_mode),
                "actual_mode": oct(info.st_mode & 0o777),
            },
        )

    return _testdisk_result(
        True,
        "TESTDISK_TARGET_OK",
        f"Recovery target ownership and mode verified: {target}",
        status="ok",
    )


def _validate_ancestors_traversable(
    leaf_path,
    boundary_path,
    *,
    stat_provider=_default_stat_provider,
):
    """
    Verify every ancestor directory of leaf_path, up to and including
    boundary_path, grants 'others execute' (o+x) so the dropped identity can
    traverse to an owned leaf. Fail-closed. boundary_path must be an ancestor of
    leaf_path.
    """

    leaf = Path(leaf_path)
    boundary = Path(boundary_path)

    ancestors = []
    found_boundary = False
    for parent in leaf.parents:
        ancestors.append(parent)
        if parent == boundary:
            found_boundary = True
            break

    if not found_boundary:
        return _testdisk_result(
            False,
            "TESTDISK_TRAVERSAL_BOUNDARY_NOT_ANCESTOR",
            f"Boundary is not an ancestor of the target: {boundary}",
            status="refused",
            display_args={"path": str(leaf), "boundary": str(boundary)},
        )

    non_traversable = []
    for ancestor in ancestors:
        try:
            info = stat_provider(str(ancestor))
        except OSError:
            non_traversable.append(str(ancestor))
            continue
        if not (info.st_mode & 0o001):
            non_traversable.append(str(ancestor))

    if non_traversable:
        return _testdisk_result(
            False,
            "TESTDISK_PARENT_NOT_TRAVERSABLE",
            "One or more structural parents are not traversable by others: "
            f"{', '.join(non_traversable)}",
            status="refused",
            display_args={"paths": ", ".join(non_traversable)},
        )

    return _testdisk_result(
        True,
        "TESTDISK_TRAVERSAL_OK",
        "All structural parents are traversable by the recovery identity.",
        status="ok",
    )


def _check_working_free_space(
    source_image_path,
    working_dir,
    *,
    safety_margin_bytes=_TESTDISK_WORKING_COPY_SAFETY_MARGIN_BYTES,
    stat_provider=_default_stat_provider,
    statvfs_provider=_default_statvfs_provider,
):
    """
    Conservative free-space precheck: require at least a full-size copy of the
    canonical image plus an explicit non-negative safety margin available on the
    working/ filesystem. Fail-closed on a negative margin or any undetermined
    value.
    """

    if safety_margin_bytes < 0:
        return _testdisk_result(
            False,
            "TESTDISK_FREE_SPACE_INVALID_MARGIN",
            "Free-space safety margin must not be negative.",
            status="refused",
            display_args={"safety_margin_bytes": safety_margin_bytes},
        )

    source = str(source_image_path)

    try:
        source_size = stat_provider(source).st_size
    except FileNotFoundError:
        return _testdisk_result(
            False,
            "TESTDISK_SOURCE_IMAGE_MISSING",
            f"Canonical image not found: {source}",
            status="refused",
            display_args={"path": source},
        )
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_FREE_SPACE_UNDETERMINED",
            f"Canonical image size could not be determined: {error}",
            status="refused",
            display_args={"path": source, "error": str(error)},
        )

    try:
        vfs = statvfs_provider(str(working_dir))
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_FREE_SPACE_UNDETERMINED",
            f"Working filesystem free space could not be determined: {error}",
            status="refused",
            display_args={"path": str(working_dir), "error": str(error)},
        )

    available_bytes = vfs.f_bavail * vfs.f_frsize
    required_bytes = source_size + safety_margin_bytes

    if available_bytes < required_bytes:
        return _testdisk_result(
            False,
            "TESTDISK_INSUFFICIENT_FREE_SPACE",
            "Insufficient free space on the working filesystem for the "
            "working copy.",
            status="refused",
            display_args={
                "required_bytes": required_bytes,
                "available_bytes": available_bytes,
                "source_size_bytes": source_size,
                "safety_margin_bytes": safety_margin_bytes,
            },
            required_bytes=required_bytes,
            available_bytes=available_bytes,
        )

    return _testdisk_result(
        True,
        "TESTDISK_FREE_SPACE_OK",
        "Sufficient free space is available for the working copy.",
        status="ok",
        required_bytes=required_bytes,
        available_bytes=available_bytes,
    )


class _DefaultTestdiskFsOps:
    """
    Default filesystem operations for working-copy preparation. Injected in
    production; tests substitute a fake so no privileged operations run.
    """

    def exists(self, path):
        return Path(path).exists()

    def unlink(self, path):
        Path(path).unlink()

    def create_secure_file(self, path, mode):
        # Create the empty destination exclusively with restrictive permissions
        # BEFORE any image bytes are written, so the working copy is never
        # briefly world-readable. O_EXCL guarantees a fresh file (the stale .tmp
        # was already removed); fchmod overrides umask so the mode is exact.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)

    def copy(self, source, destination):
        # copyfile opens the (already-created, 0600) destination for writing and
        # truncates it; it does not alter the existing file mode.
        shutil.copyfile(source, destination)

    def size(self, path):
        return Path(path).stat().st_size

    def fsync_file(self, path):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def fsync_dir(self, path):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def rename(self, source, destination):
        os.replace(source, destination)

    def chown(self, path, uid, gid):
        os.chown(path, uid, gid)

    def chmod(self, path, mode):
        os.chmod(path, mode)

    def lstat(self, path):
        # lstat (not stat) so a symlink is reported as a symlink and never
        # silently followed to its target during output/log validation.
        return os.lstat(path)

    def mkdir(self, path, mode):
        # Fails (FileExistsError) if any object — including a symlink — already
        # exists at the path, so a pre-existing symlink is never followed.
        os.mkdir(path, mode)

    def create_regular_file(self, path, mode):
        # O_CREAT|O_EXCL creates a brand-new regular file and fails
        # (FileExistsError) if the path already exists, including when it is a
        # symlink; the symlink is therefore never followed or clobbered.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        os.close(descriptor)

    def rmdir(self, path):
        os.rmdir(path)


def _prepare_testdisk_working_copy(
    source_image_path,
    working_dir,
    *,
    owner_uid,
    owner_gid,
    file_mode=_TESTDISK_WORKING_COPY_MODE,
    fs_ops=None,
):
    """
    Prepare the disposable working copy working/testdisk.img with atomic,
    failure-safe completion (TestDiskIntegration.md §3):

        remove stale .tmp -> create restricted (0600) .tmp -> copy -> verify
        size -> fsync file -> chown/chmod .tmp -> atomic rename -> fsync
        directory

    The temporary file is created with restrictive permissions BEFORE any image
    bytes are written, so the working copy is never transiently world-readable.
    The file is fsync'd BEFORE the rename; ownership and mode are applied to the
    temporary file BEFORE the rename so the final file is never briefly owned by
    the privileged preparer; the containing directory is fsync'd AFTER the
    rename (so the new directory entry is durable). On any pre-rename failure the
    temporary file is removed and no final file is created; on a failure after
    the rename the final file is removed. All filesystem interactions go through
    the injected fs_ops so tests need no root and no real disk.
    """

    fs = fs_ops if fs_ops is not None else _DefaultTestdiskFsOps()

    source = Path(source_image_path)
    working_dir = Path(working_dir)
    final_path = working_dir / _TESTDISK_WORKING_COPY_FILENAME
    tmp_path = final_path.with_name(
        _TESTDISK_WORKING_COPY_FILENAME + _TESTDISK_WORKING_COPY_TMP_SUFFIX
    )

    def _cleanup(remove_final):
        candidates = [tmp_path, final_path] if remove_final else [tmp_path]
        for candidate in candidates:
            try:
                if fs.exists(str(candidate)):
                    fs.unlink(str(candidate))
            except OSError:
                pass

    try:
        source_size = fs.size(str(source))
    except OSError:
        return _testdisk_result(
            False,
            "TESTDISK_SOURCE_IMAGE_MISSING",
            f"Canonical image not found for working-copy preparation: {source}",
            status="refused",
            display_args={"path": str(source)},
        )

    try:
        if fs.exists(str(tmp_path)):
            fs.unlink(str(tmp_path))
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_WORKING_COPY_STALE_TMP_CLEANUP_FAILED",
            f"Could not remove a stale temporary working copy: {error}",
            status="failed",
            display_args={"path": str(tmp_path), "error": str(error)},
        )

    final_created = False
    try:
        try:
            fs.create_secure_file(str(tmp_path), file_mode)
        except OSError as error:
            _cleanup(False)
            return _testdisk_result(
                False,
                "TESTDISK_WORKING_COPY_CREATE_FAILED",
                f"Restricted temporary working copy could not be created: "
                f"{error}",
                status="failed",
                display_args={"path": str(tmp_path), "error": str(error)},
            )

        try:
            fs.copy(str(source), str(tmp_path))
        except OSError as error:
            _cleanup(False)
            return _testdisk_result(
                False,
                "TESTDISK_WORKING_COPY_COPY_FAILED",
                f"Working-copy byte copy failed: {error}",
                status="failed",
                display_args={"error": str(error)},
            )

        try:
            copied_size = fs.size(str(tmp_path))
        except OSError as error:
            _cleanup(False)
            return _testdisk_result(
                False,
                "TESTDISK_WORKING_COPY_VERIFY_FAILED",
                f"Working-copy size could not be verified: {error}",
                status="failed",
                display_args={"error": str(error)},
            )

        if copied_size != source_size:
            _cleanup(False)
            return _testdisk_result(
                False,
                "TESTDISK_WORKING_COPY_SIZE_MISMATCH",
                "Working-copy size does not match the canonical image.",
                status="failed",
                display_args={
                    "expected_bytes": source_size,
                    "actual_bytes": copied_size,
                },
            )

        try:
            fs.fsync_file(str(tmp_path))
        except OSError as error:
            _cleanup(False)
            return _testdisk_result(
                False,
                "TESTDISK_WORKING_COPY_FSYNC_FAILED",
                f"Working-copy file fsync failed: {error}",
                status="failed",
                display_args={"error": str(error)},
            )

        # Apply ownership/mode to the temporary file BEFORE the atomic rename so
        # that working/testdisk.img already has the recovery identity and mode
        # the instant it appears under its final name. Doing this after the
        # rename would leave a brief window where the final file is owned by the
        # privileged (root) preparer; ordering it here removes that window
        # entirely (TestDiskIntegration.md §3/§7A). Failure here is a pre-rename
        # failure: only the temporary file is cleaned up and no final file is
        # ever created.
        try:
            fs.chown(str(tmp_path), owner_uid, owner_gid)
            fs.chmod(str(tmp_path), file_mode)
        except OSError as error:
            _cleanup(False)
            return _testdisk_result(
                False,
                "TESTDISK_WORKING_COPY_OWNERSHIP_FAILED",
                f"Working-copy ownership/mode could not be applied: {error}",
                status="failed",
                display_args={"error": str(error)},
            )

        try:
            fs.rename(str(tmp_path), str(final_path))
            final_created = True
        except OSError as error:
            _cleanup(False)
            return _testdisk_result(
                False,
                "TESTDISK_WORKING_COPY_RENAME_FAILED",
                f"Working-copy atomic rename failed: {error}",
                status="failed",
                display_args={"error": str(error)},
            )

        try:
            fs.fsync_dir(str(working_dir))
        except OSError as error:
            _cleanup(True)
            return _testdisk_result(
                False,
                "TESTDISK_WORKING_COPY_FSYNC_FAILED",
                f"Working directory fsync failed: {error}",
                status="failed",
                display_args={"error": str(error)},
            )
    except BaseException:
        _cleanup(final_created)
        raise

    return _testdisk_result(
        True,
        "TESTDISK_WORKING_COPY_PREPARED",
        f"Working copy prepared: {final_path}",
        status="completed",
        path=str(final_path),
        size_bytes=source_size,
    )


def _validate_execution_target(
    target_path,
    *,
    canonical_image_path,
    source_device_path=None,
    path_resolver=os.path.realpath,
):
    """
    Canonical-immutability guard: the TestDisk execution target must be neither
    the canonical image nor the original device. Paths are normalized through
    path_resolver (default os.path.realpath) before comparison. Fail-closed.
    """

    target = path_resolver(str(target_path))
    canonical = path_resolver(str(canonical_image_path))

    if target == canonical:
        return _testdisk_result(
            False,
            "TESTDISK_TARGET_IS_CANONICAL",
            "Refusing to use the canonical image as the TestDisk target.",
            status="refused",
            display_args={"target": target},
        )

    if source_device_path:
        device = path_resolver(str(source_device_path))
        if target == device:
            return _testdisk_result(
                False,
                "TESTDISK_TARGET_IS_ORIGINAL_DEVICE",
                "Refusing to use the original device as the TestDisk target.",
                status="refused",
                display_args={"target": target},
            )

    return _testdisk_result(
        True,
        "TESTDISK_TARGET_SAFE",
        "TestDisk target is neither the canonical image nor the original device.",
        status="ok",
        target=target,
    )


# ---------------------------------------------------------------------------
# Protected output/log target preparation.
#
# Prepares recovered/testdisk/ (directory, recovery-owned, exactly 0700) and
# evidence/testdisk.log (regular file, recovery-owned, exactly 0640). Both are
# prepared fail-closed and without ever following a pre-existing symlink: a
# missing target is created exclusively (mkdir / O_CREAT|O_EXCL), owned and
# moded, then re-validated; a pre-existing target is accepted only if it is a
# real object of the expected type with the exact required owner and mode. A
# structurally wrong pre-existing target is refused but NEVER deleted (it may be
# legitimate operator/evidence data); only objects this attempt created are
# cleaned up on failure. Structural parents are never chowned.
# ---------------------------------------------------------------------------


def _object_matches_kind(mode, kind):
    if kind == "dir":
        return stat.S_ISDIR(mode)
    return stat.S_ISREG(mode)


def _cleanup_created_target(path, kind, fs_ops):
    try:
        if kind == "dir":
            fs_ops.rmdir(str(path))
        else:
            fs_ops.unlink(str(path))
    except OSError:
        pass


def _prepare_protected_target(
    path,
    *,
    kind,
    owner_uid,
    owner_gid,
    required_mode,
    fs_ops,
):
    """
    Prepare a single protected output target (a directory or a regular file).

    Returns a structured result carrying an extra "created" flag: True when this
    call created the object (and is therefore responsible for cleaning it up on a
    later failure), False when a valid target already existed. Fail-closed on any
    stat/create/chown/chmod/validation failure; a pre-existing symlink or a
    wrong-typed/wrong-owner/wrong-mode pre-existing object is refused and left
    untouched.
    """

    target = str(path)

    try:
        info = fs_ops.lstat(target)
        existed = True
    except FileNotFoundError:
        existed = False
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_OUTPUT_STAT_FAILED",
            f"Output target could not be inspected: {error}",
            status="failed",
            display_args={"path": target, "error": str(error)},
            created=False,
        )

    if existed:
        if stat.S_ISLNK(info.st_mode):
            return _testdisk_result(
                False,
                "TESTDISK_OUTPUT_IS_SYMLINK",
                f"Output target is a symlink; refusing to use it: {target}",
                status="refused",
                display_args={"path": target},
                created=False,
            )
        if not _object_matches_kind(info.st_mode, kind):
            return _testdisk_result(
                False,
                "TESTDISK_OUTPUT_WRONG_TYPE",
                f"Output target is not a {kind}: {target}",
                status="refused",
                display_args={"path": target, "expected_kind": kind},
                created=False,
            )
        if info.st_uid != owner_uid or info.st_gid != owner_gid:
            return _testdisk_result(
                False,
                "TESTDISK_OUTPUT_WRONG_OWNER",
                f"Output target is not owned by the recovery identity: {target}",
                status="refused",
                display_args={"path": target},
                created=False,
            )
        if stat.S_IMODE(info.st_mode) != required_mode:
            return _testdisk_result(
                False,
                "TESTDISK_OUTPUT_WRONG_MODE",
                f"Output target has an unexpected mode: {target}",
                status="refused",
                display_args={
                    "path": target,
                    "expected_mode": oct(required_mode),
                    "actual_mode": oct(stat.S_IMODE(info.st_mode)),
                },
                created=False,
            )
        return _testdisk_result(
            True,
            "TESTDISK_OUTPUT_TARGET_OK",
            f"Existing output target accepted: {target}",
            status="ok",
            path=target,
            created=False,
        )

    try:
        if kind == "dir":
            fs_ops.mkdir(target, required_mode)
        else:
            fs_ops.create_regular_file(target, required_mode)
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_OUTPUT_CREATE_FAILED",
            f"Output target could not be created: {error}",
            status="failed",
            display_args={"path": target, "error": str(error)},
            created=False,
        )

    try:
        fs_ops.chown(target, owner_uid, owner_gid)
        fs_ops.chmod(target, required_mode)
    except OSError as error:
        _cleanup_created_target(target, kind, fs_ops)
        return _testdisk_result(
            False,
            "TESTDISK_OUTPUT_OWNERSHIP_FAILED",
            f"Output target ownership/mode could not be applied: {error}",
            status="failed",
            display_args={"path": target, "error": str(error)},
            created=False,
        )

    try:
        info = fs_ops.lstat(target)
    except OSError as error:
        _cleanup_created_target(target, kind, fs_ops)
        return _testdisk_result(
            False,
            "TESTDISK_OUTPUT_STAT_FAILED",
            f"Output target could not be re-inspected: {error}",
            status="failed",
            display_args={"path": target, "error": str(error)},
            created=False,
        )

    if (
        stat.S_ISLNK(info.st_mode)
        or not _object_matches_kind(info.st_mode, kind)
        or info.st_uid != owner_uid
        or info.st_gid != owner_gid
        or stat.S_IMODE(info.st_mode) != required_mode
    ):
        _cleanup_created_target(target, kind, fs_ops)
        return _testdisk_result(
            False,
            "TESTDISK_OUTPUT_VALIDATION_FAILED",
            f"Freshly created output target failed validation: {target}",
            status="failed",
            display_args={"path": target},
            created=False,
        )

    return _testdisk_result(
        True,
        "TESTDISK_OUTPUT_TARGET_PREPARED",
        f"Output target prepared: {target}",
        status="completed",
        path=target,
        created=True,
    )


def _prepare_testdisk_output_targets(
    recovered_testdisk_dir,
    log_path,
    *,
    owner_uid,
    owner_gid,
    fs_ops,
):
    """
    Prepare recovered/testdisk/ (0700 dir) and evidence/testdisk.log (0640 file),
    both recovery-owned. If the log preparation fails after this attempt created
    the recovered/testdisk/ directory, that directory is rolled back so no
    partial preparation is left behind; a pre-existing (accepted) directory is
    never removed. Fail-closed.
    """

    dir_result = _prepare_protected_target(
        recovered_testdisk_dir,
        kind="dir",
        owner_uid=owner_uid,
        owner_gid=owner_gid,
        required_mode=_TESTDISK_RECOVERED_DIR_MODE,
        fs_ops=fs_ops,
    )
    if not dir_result["success"]:
        return dir_result

    log_result = _prepare_protected_target(
        log_path,
        kind="file",
        owner_uid=owner_uid,
        owner_gid=owner_gid,
        required_mode=_TESTDISK_LOG_MODE,
        fs_ops=fs_ops,
    )
    if not log_result["success"]:
        if dir_result.get("created"):
            _cleanup_created_target(recovered_testdisk_dir, "dir", fs_ops)
        return log_result

    return _testdisk_result(
        True,
        "TESTDISK_OUTPUT_TARGETS_PREPARED",
        "Recovered directory and log target prepared.",
        status="completed",
        recovered_directory=str(recovered_testdisk_dir),
        log_path=str(log_path),
        recovered_directory_created=dir_result.get("created", False),
        log_created=log_result.get("created", False),
    )


# ---------------------------------------------------------------------------
# Root-mode command construction and execution preparation/orchestration.
#
# This slice supports execution_mode == "root" only: Sentinel must already be
# root (geteuid() == 0) and drops to the confined recovery identity with setpriv
# to run TestDisk. "sudo" and "external" remain accepted *configuration* modes
# but are refused at runtime with distinct "not executable yet" codes. No shell
# is ever used and no command template comes from configuration.
# ---------------------------------------------------------------------------


def _resolve_executable(
    name,
    *,
    command_resolver=_default_command_resolver,
    lstat_provider=_default_lstat_provider,
):
    """
    Resolve a required executable to a validated absolute path so execution never
    performs a PATH lookup on a bare name (closing the setpriv/testdisk PATH-swap
    and prep→exec TOCTOU vectors).

    The lookup result must be a non-empty, absolute path; lstat (never followed)
    must show a regular file with at least one executable bit. A symlinked
    executable is rejected — deployments must provide real setpriv/testdisk
    binaries (the reference host does, §11); that symlink rejection is the exact
    trust boundary. Fail-closed on every other outcome. command_resolver stays
    injectable for unit tests.
    """

    resolved = command_resolver(name)

    if not resolved or not isinstance(resolved, str):
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTABLE_NOT_FOUND",
            f"Required executable not found on PATH: {name}",
            status="refused",
            display_args={"name": name},
        )

    if not os.path.isabs(resolved):
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTABLE_NOT_ABSOLUTE",
            f"Resolved executable path is not absolute: {resolved}",
            status="refused",
            display_args={"name": name, "path": resolved},
        )

    try:
        info = lstat_provider(resolved)
    except OSError as error:
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTABLE_STAT_FAILED",
            f"Resolved executable could not be inspected: {error}",
            status="refused",
            display_args={"name": name, "path": resolved, "error": str(error)},
        )

    if stat.S_ISLNK(info.st_mode):
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTABLE_IS_SYMLINK",
            f"Resolved executable is a symlink; refusing it: {resolved}",
            status="refused",
            display_args={"name": name, "path": resolved},
        )

    if not stat.S_ISREG(info.st_mode):
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTABLE_NOT_REGULAR",
            f"Resolved executable is not a regular file: {resolved}",
            status="refused",
            display_args={"name": name, "path": resolved},
        )

    if not (stat.S_IMODE(info.st_mode) & 0o111):
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTABLE_NOT_EXECUTABLE",
            f"Resolved executable is not executable: {resolved}",
            status="refused",
            display_args={"name": name, "path": resolved},
        )

    return _testdisk_result(
        True,
        "TESTDISK_EXECUTABLE_RESOLVED",
        f"Executable resolved: {resolved}",
        status="ok",
        path=resolved,
    )


def _build_testdisk_child_env(source_env):
    """
    Build an explicit, minimal environment for the TestDisk child from an
    injected source environment, without mutating the source.

    PATH is forced to a fixed safe system path (_TESTDISK_SAFE_PATH). Only the
    interactive-TUI/locale variables in _TESTDISK_PRESERVED_ENV_VARS are carried
    over, and only when present and non-empty. Everything else (PYTHONPATH,
    LD_PRELOAD, LD_LIBRARY_PATH, arbitrary SENTINEL_* variables, HOME, …) is
    dropped. Any retained value (or its name) containing a NUL byte fails closed
    with ValueError, before the child could ever be launched.
    """

    child = {"PATH": _TESTDISK_SAFE_PATH}

    for name in _TESTDISK_PRESERVED_ENV_VARS:
        value = source_env.get(name)
        if not value:
            continue
        if "\x00" in name or "\x00" in value:
            raise ValueError(
                f"Environment variable {name!r} contains a NUL byte"
            )
        child[name] = value

    return child


def _build_testdisk_root_command(
    setpriv_path,
    testdisk_path,
    recovery_uid,
    recovery_gid,
    working_image_path,
):
    """
    Build the exact root-mode TestDisk argv (list form, no shell) from already
    resolved ABSOLUTE executable paths. setpriv drops to the confined recovery
    identity, clears supplementary groups, and re-execs TestDisk:

        <abs setpriv> --reuid=<uid> --regid=<gid> --clear-groups -- \
            <abs testdisk> /log <working-image-path>

    Defensive pure helper: the caller must have already resolved/validated both
    executables (via _resolve_executable), the identity (non-root integer
    uid/gid), and the working target (via _validate_execution_target). Raises
    ValueError on any invalid input so a malformed or non-absolute command can
    never be constructed, and so bare executable names can never be executed.
    """

    setpriv = str(setpriv_path)
    testdisk = str(testdisk_path)
    if not os.path.isabs(setpriv):
        raise ValueError("setpriv path must be absolute")
    if not os.path.isabs(testdisk):
        raise ValueError("testdisk path must be absolute")

    if isinstance(recovery_uid, bool) or not isinstance(recovery_uid, int):
        raise ValueError("recovery uid must be an integer")
    if isinstance(recovery_gid, bool) or not isinstance(recovery_gid, int):
        raise ValueError("recovery gid must be an integer")
    if recovery_uid == 0 or recovery_gid == 0:
        raise ValueError("recovery identity must not be root (uid/gid 0)")

    working = str(working_image_path)
    if not working.strip():
        raise ValueError("working image path must be provided")

    return [
        setpriv,
        f"--reuid={recovery_uid}",
        f"--regid={recovery_gid}",
        "--clear-groups",
        "--",
        testdisk,
        "/log",
        working,
    ]


def _validate_testdisk_config_structure(testdisk_config):
    """
    Defensively validate the normalized TestDisk config structure the caller
    passes in (as produced by translator.read_testdisk_config()["config"]).
    Returns None when the structure is usable, or a fail-closed result.
    """

    if not isinstance(testdisk_config, dict):
        return _testdisk_result(
            False,
            "TESTDISK_CONFIG_STRUCTURE_INVALID",
            "TestDisk configuration structure is missing or malformed.",
            status="refused",
        )

    account = testdisk_config.get("recovery_account")
    if not isinstance(account, str) or not account.strip():
        return _testdisk_result(
            False,
            "TESTDISK_CONFIG_STRUCTURE_INVALID",
            "TestDisk configuration is missing a valid recovery_account.",
            status="refused",
        )

    groups = testdisk_config.get("forbidden_groups")
    if not isinstance(groups, list) or not all(
        isinstance(name, str) for name in groups
    ):
        return _testdisk_result(
            False,
            "TESTDISK_CONFIG_STRUCTURE_INVALID",
            "TestDisk configuration is missing valid forbidden_groups.",
            status="refused",
        )

    mechanism = testdisk_config.get("privilege_drop_mechanism")
    if not isinstance(mechanism, str) or not mechanism.strip():
        return _testdisk_result(
            False,
            "TESTDISK_CONFIG_STRUCTURE_INVALID",
            "TestDisk configuration is missing a valid "
            "privilege_drop_mechanism.",
            status="refused",
        )

    mode = testdisk_config.get("execution_mode")
    if not isinstance(mode, str) or not mode.strip():
        return _testdisk_result(
            False,
            "TESTDISK_CONFIG_STRUCTURE_INVALID",
            "TestDisk configuration is missing a valid execution_mode.",
            status="refused",
        )

    margin = testdisk_config.get(
        "working_copy_safety_margin_bytes",
        _TESTDISK_WORKING_COPY_SAFETY_MARGIN_BYTES,
    )
    if isinstance(margin, bool) or not isinstance(margin, int) or margin < 0:
        return _testdisk_result(
            False,
            "TESTDISK_CONFIG_STRUCTURE_INVALID",
            "TestDisk configuration has an invalid "
            "working_copy_safety_margin_bytes.",
            status="refused",
        )

    return None


def prepare_testdisk_execution(
    session,
    testdisk_config,
    *,
    identity_resolver=_default_identity_resolver,
    command_resolver=_default_command_resolver,
    geteuid=_default_geteuid,
    stat_provider=_default_stat_provider,
    statvfs_provider=_default_statvfs_provider,
    lstat_provider=_default_lstat_provider,
    source_environ=None,
    fs_ops=None,
):
    """
    Perform all fail-closed prerequisite validation and privileged preparation
    for a root-mode TestDisk run, WITHOUT executing TestDisk, changing session
    status, creating a recovery-operations record, persisting anything, or
    touching the canonical image.

    On success returns a structured result (status "prepared") carrying the
    normalized data execute_testdisk_recovery() needs: recovery uid/gid, the
    resolved ABSOLUTE setpriv/testdisk paths, the exact argv built from them, the
    minimal child environment, the cwd (evidence dir), the working image path,
    the recovered directory path, and the log path. Any failure returns a
    fail-closed result with a distinct code and performs no partial, unsafe
    state.
    """

    fs = fs_ops if fs_ops is not None else _DefaultTestdiskFsOps()
    if source_environ is None:
        source_environ = os.environ

    structure_error = _validate_testdisk_config_structure(testdisk_config)
    if structure_error is not None:
        return structure_error

    mode = testdisk_config["execution_mode"].strip().lower()
    mechanism = testdisk_config["privilege_drop_mechanism"].strip()
    account = testdisk_config["recovery_account"].strip()
    forbidden_groups = list(testdisk_config.get("forbidden_groups", []))
    safety_margin_bytes = testdisk_config.get(
        "working_copy_safety_margin_bytes",
        _TESTDISK_WORKING_COPY_SAFETY_MARGIN_BYTES,
    )

    if mode == "sudo":
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTION_MODE_SUDO_NOT_EXECUTABLE_YET",
            "Execution mode 'sudo' is accepted in configuration but is not "
            "executable yet; only root mode runs in this slice.",
            status="refused",
            display_args={"mode": mode},
        )

    if mode == "external":
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTION_MODE_EXTERNAL_NOT_EXECUTABLE_YET",
            "Execution mode 'external' is accepted in configuration but is not "
            "executable yet; only root mode runs in this slice.",
            status="refused",
            display_args={"mode": mode},
        )

    if mode != "root":
        return _testdisk_result(
            False,
            "TESTDISK_EXECUTION_MODE_INVALID",
            f"Unsupported execution mode: {mode}",
            status="refused",
            display_args={"mode": mode},
        )

    if geteuid() != 0:
        return _testdisk_result(
            False,
            "TESTDISK_REQUIRES_ROOT",
            "Root execution mode requires Sentinel to run as root "
            "(geteuid() == 0).",
            status="refused",
        )

    if mechanism != _TESTDISK_DROP_MECHANISM_SETPRIV:
        return _testdisk_result(
            False,
            "TESTDISK_DROP_MECHANISM_UNSUPPORTED",
            f"Unsupported privilege-drop mechanism: {mechanism}",
            status="refused",
            display_args={"mechanism": mechanism},
        )

    setpriv_result = _resolve_executable(
        _TESTDISK_DROP_MECHANISM_SETPRIV,
        command_resolver=command_resolver,
        lstat_provider=lstat_provider,
    )
    if not setpriv_result["success"]:
        return setpriv_result
    setpriv_path = setpriv_result["path"]

    testdisk_result = _resolve_executable(
        "testdisk",
        command_resolver=command_resolver,
        lstat_provider=lstat_provider,
    )
    if not testdisk_result["success"]:
        return testdisk_result
    testdisk_path = testdisk_result["path"]

    identity_result = _resolve_recovery_identity(
        account, identity_resolver=identity_resolver
    )
    if not identity_result["success"]:
        return identity_result
    identity = identity_result["identity"]

    safe_result = _reject_unsafe_recovery_identity(identity, forbidden_groups)
    if not safe_result["success"]:
        return safe_result

    recovery_path = Path(session.recovery_path)
    canonical_image = recovery_path / "images" / "source.img"
    working_dir = recovery_path / "working"
    working_image = working_dir / _TESTDISK_WORKING_COPY_FILENAME
    recovered_dir = recovery_path / "recovered"
    recovered_testdisk = recovered_dir / TESTDISK_RECOVERED_DIRNAME
    evidence_dir = recovery_path / "evidence"
    log_path = evidence_dir / _TESTDISK_LOG_FILENAME

    canonical_result = _validate_canonical_protection(
        canonical_image, identity, lstat_provider=lstat_provider
    )
    if not canonical_result["success"]:
        return canonical_result

    source_device_path = (
        session.source_device.path if session.source_device else None
    )
    target_result = _validate_execution_target(
        working_image,
        canonical_image_path=canonical_image,
        source_device_path=source_device_path,
    )
    if not target_result["success"]:
        return target_result

    free_space_result = _check_working_free_space(
        canonical_image,
        working_dir,
        safety_margin_bytes=safety_margin_bytes,
        stat_provider=stat_provider,
        statvfs_provider=statvfs_provider,
    )
    if not free_space_result["success"]:
        return free_space_result

    working_copy_result = _prepare_testdisk_working_copy(
        canonical_image,
        working_dir,
        owner_uid=identity["uid"],
        owner_gid=identity["gid"],
        fs_ops=fs,
    )
    if not working_copy_result["success"]:
        return working_copy_result

    output_result = _prepare_testdisk_output_targets(
        recovered_testdisk,
        log_path,
        owner_uid=identity["uid"],
        owner_gid=identity["gid"],
        fs_ops=fs,
    )
    if not output_result["success"]:
        return output_result

    for target_path, required_mode in (
        (working_image, _TESTDISK_WORKING_COPY_MODE),
        (recovered_testdisk, _TESTDISK_RECOVERED_DIR_MODE),
        (log_path, _TESTDISK_LOG_MODE),
    ):
        ownership_result = _validate_recovery_target(
            target_path, identity, required_mode, stat_provider=stat_provider
        )
        if not ownership_result["success"]:
            return ownership_result

        traversal_result = _validate_ancestors_traversable(
            target_path, recovery_path, stat_provider=stat_provider
        )
        if not traversal_result["success"]:
            return traversal_result

    argv = _build_testdisk_root_command(
        setpriv_path,
        testdisk_path,
        identity["uid"],
        identity["gid"],
        working_image,
    )

    try:
        child_env = _build_testdisk_child_env(source_environ)
    except ValueError as error:
        return _testdisk_result(
            False,
            "TESTDISK_ENV_INVALID",
            f"TestDisk child environment could not be built: {error}",
            status="refused",
            display_args={"error": str(error)},
        )

    return _testdisk_result(
        True,
        "TESTDISK_PREPARED",
        "TestDisk root-mode execution prepared.",
        status="prepared",
        recovery_uid=identity["uid"],
        recovery_gid=identity["gid"],
        setpriv_path=setpriv_path,
        testdisk_path=testdisk_path,
        argv=argv,
        env=child_env,
        cwd=str(evidence_dir),
        working_image_path=str(working_image),
        recovered_directory=str(recovered_testdisk),
        log_path=str(log_path),
    )


def execute_testdisk_recovery(preparation, *, runner=subprocess.run):
    """
    Execute TestDisk from an already-successful prepare_testdisk_execution()
    result, then summarize recovered/testdisk/.

    The runner (subprocess.run by default) is invoked exactly once with the
    prepared argv, the prepared minimal child environment, and cwd set to the
    evidence directory. The argv and environment are taken verbatim from the
    preparation result — execution performs NO PATH lookup and NEVER rebuilds
    the environment from the live process. The interactive terminal is
    preserved: no stdout/stderr/stdin capture, no shell. Only the zero/non-zero
    exit distinction is interpreted; TestDisk's interactive choices are never
    inspected. Returns the same recovery-result fields the existing PhotoRec
    caller consumes.
    """

    result = {
        "success": False,
        "status": "failed",
        "artifacts": [],
        "message": "",
        "recovered_directory_count": 0,
        "recovered_file_count": 0,
        "recovered_total_bytes": 0,
    }

    if (
        not isinstance(preparation, dict)
        or not preparation.get("success")
        or preparation.get("status") != "prepared"
        or not isinstance(preparation.get("argv"), list)
        or not preparation.get("argv")
        or not preparation.get("cwd")
        or not preparation.get("recovered_directory")
        or not isinstance(preparation.get("env"), dict)
    ):
        result["code"] = "TESTDISK_PREPARATION_INVALID"
        result["message"] = (
            "TestDisk execution requires a successful preparation result."
        )
        return result

    argv = preparation["argv"]
    cwd = preparation["cwd"]
    child_env = preparation["env"]
    recovered_testdisk = Path(preparation["recovered_directory"])

    try:
        completed = runner(argv, cwd=cwd, env=child_env)
    except OSError as error:
        result["code"] = "TESTDISK_LAUNCH_FAILED"
        result["display_args"] = {"error": str(error)}
        result["message"] = f"TestDisk could not be launched: {error}"
        return result

    (
        result["recovered_directory_count"],
        result["recovered_file_count"],
        result["recovered_total_bytes"],
        result["artifacts"],
    ) = _count_testdisk_artifacts(recovered_testdisk.parent)

    if completed.returncode == 0:
        result["success"] = True
        result["status"] = "ended"
        result["code"] = "TESTDISK_ENDED_NORMALLY"
        result["message"] = "TestDisk session ended normally."
    else:
        result["code"] = "TESTDISK_EXIT_CODE"
        result["display_args"] = {"exit_code": completed.returncode}
        result["message"] = (
            f"TestDisk session failed with exit code {completed.returncode}."
        )

    return result