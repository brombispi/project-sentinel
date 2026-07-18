import json
from datetime import datetime
from pathlib import Path

from core.assessment import Assessment
from core.decision import Decision
from core.session import RecoverySession
from core.status import RecoveryStatus
from modules.archive import (
    IDENTITY_MATCHES,
    classify_acquisition_state,
    compare_source_identity,
)
from modules.case_discovery import (
    enumerate_all_permitted_roots,
    enumerate_permitted_roots,
    get_runtime_recoveries_root,
)
from modules.manifest import ManifestError, read_case_manifest
from modules.storage_query import get_block_device_size_bytes

# Stable load-result codes for workflow branching. Not displayed to operators.
CODE_MANIFEST_ERROR = "MANIFEST_ERROR"
CODE_CASE_PATH_NOT_ACCESSIBLE = "CASE_PATH_NOT_ACCESSIBLE"
CODE_INVALID_CREATED_AT = "INVALID_CREATED_AT"
CODE_SOURCE_SIZE_BYTES_NOT_RECORDED = "SOURCE_SIZE_BYTES_NOT_RECORDED"
CODE_SOURCE_NOT_CONNECTED = "SOURCE_NOT_CONNECTED"
CODE_AMBIGUOUS_SOURCE = "AMBIGUOUS_SOURCE"
CODE_DESTINATION_NOT_CONNECTED = "DESTINATION_NOT_CONNECTED"
CODE_AMBIGUOUS_DESTINATION = "AMBIGUOUS_DESTINATION"
CODE_DESTINATION_NOT_MOUNTED = "DESTINATION_NOT_MOUNTED"
CODE_CASE_LOADED = "CASE_LOADED"


def _normalize_identity_text(value):
    if value is None:
        return ""

    return str(value).strip()


def _path_under_root(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()

    try:
        return path.is_relative_to(root)
    except AttributeError:
        return str(path).startswith(str(root))


def _is_on_recovery_storage(case_path, devices):
    case_path = Path(case_path).resolve()
    local_root = get_runtime_recoveries_root().resolve()

    if _path_under_root(case_path, local_root):
        return False

    for root_info in enumerate_permitted_roots(devices):
        if root_info["is_local"]:
            continue

        if _path_under_root(case_path, root_info["path"]):
            return True

    return False


def _destination_reidentify_failure_blocks_open(status, case_path, devices):
    if _is_on_recovery_storage(case_path, devices):
        return False

    if status in (
        RecoveryStatus.READY_FOR_RECOVERY,
        RecoveryStatus.RECOVERING,
    ):
        return False

    return True


def _load_acquisition_source(recovery_path):
    acquisition_source_path = (
        Path(recovery_path) / "evidence" / "acquisition_source.json"
    )

    if not acquisition_source_path.is_file():
        return None

    try:
        return json.loads(
            acquisition_source_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None


def _identity_matches_device(device, identity):
    current_size = get_block_device_size_bytes(device.path)

    return (
        compare_source_identity(
            recorded_serial=identity.get("serial"),
            current_serial=device.serial,
            recorded_model=identity.get("model"),
            current_model=device.model,
            recorded_size=identity.get("size_bytes"),
            current_size=current_size,
        )
        == IDENTITY_MATCHES
    )


def _find_matching_devices(devices, identity):
    matches = []

    for device in devices:
        if device.role == "RECOVERY ENGINE":
            continue

        if _identity_matches_device(device, identity):
            matches.append(device)

    return matches


def _build_identity_from_manifest_device(device_data):
    return {
        "serial": device_data.get("serial"),
        "model": device_data.get("model"),
        "size_bytes": device_data.get("size_bytes"),
        "path": device_data.get("path"),
    }


def _build_identity_from_acquisition_source(acquisition_source):
    return {
        "serial": acquisition_source.get("serial"),
        "model": acquisition_source.get("model"),
        "size_bytes": acquisition_source.get("size_bytes"),
        "path": acquisition_source.get("path"),
    }


def _reidentify_source_device(recovery_path, manifest, devices, warnings):
    acquisition_source = _load_acquisition_source(recovery_path)
    device_data = manifest.get("device")

    if acquisition_source is not None:
        identity = _build_identity_from_acquisition_source(acquisition_source)
        identity_source = "acquisition_source.json"
    elif device_data:
        if device_data.get("size_bytes") is None:
            return {
                "success": False,
                "device": None,
                "code": CODE_SOURCE_SIZE_BYTES_NOT_RECORDED,
                "message": (
                    "Pre-acquisition source match refused: exact "
                    "size_bytes is not recorded in case.json."
                ),
            }

        identity = _build_identity_from_manifest_device(device_data)
        identity_source = "case.json"
    else:
        return {
            "success": True,
            "device": None,
            "message": "No persisted source device identity.",
        }

    matches = _find_matching_devices(devices, identity)

    if not matches:
        return {
            "success": False,
            "device": None,
            "code": CODE_SOURCE_NOT_CONNECTED,
            "message": (
                "Source device is not connected or could not be matched "
                f"using {identity_source}."
            ),
            "display_args": {"identity_source": identity_source},
        }

    if len(matches) > 1:
        candidate_paths = ", ".join(device.path for device in matches)
        return {
            "success": False,
            "device": None,
            "code": CODE_AMBIGUOUS_SOURCE,
            "message": (
                "Ambiguous source device match. Multiple candidates: "
                f"{candidate_paths}"
            ),
            "display_args": {"candidate_paths": candidate_paths},
        }

    device = matches[0]
    recorded_path = _normalize_identity_text(identity.get("path"))
    current_path = _normalize_identity_text(device.path)

    if recorded_path and current_path and recorded_path != current_path:
        warnings.append(
            f"Source path changed from {recorded_path} to {current_path}; "
            "serial and model match."
        )

    return {
        "success": True,
        "device": device,
        "message": "Source device re-identified.",
    }


def _reidentify_destination_device(manifest, devices, warnings):
    destination_data = manifest.get("destination")
    if not destination_data:
        return {
            "success": True,
            "device": None,
            "message": "No persisted destination device.",
        }

    identity = _build_identity_from_manifest_device(destination_data)
    matches = _find_matching_devices(devices, identity)

    if not matches:
        return {
            "success": False,
            "device": None,
            "code": CODE_DESTINATION_NOT_CONNECTED,
            "message": (
                "Destination Recovery Storage is not mounted or could not "
                "be matched to the persisted destination device."
            ),
        }

    if len(matches) > 1:
        candidate_paths = ", ".join(device.path for device in matches)
        return {
            "success": False,
            "device": None,
            "code": CODE_AMBIGUOUS_DESTINATION,
            "message": (
                "Ambiguous destination device match. Multiple candidates: "
                f"{candidate_paths}"
            ),
            "display_args": {"candidate_paths": candidate_paths},
        }

    device = matches[0]

    if not device.mount_point:
        return {
            "success": False,
            "device": None,
            "code": CODE_DESTINATION_NOT_MOUNTED,
            "message": (
                "Matched destination device is present but not mounted."
            ),
        }

    recorded_path = _normalize_identity_text(identity.get("path"))
    current_path = _normalize_identity_text(device.path)

    if recorded_path and current_path and recorded_path != current_path:
        warnings.append(
            f"Destination path changed from {recorded_path} to "
            f"{current_path}; serial and model match."
        )

    return {
        "success": True,
        "device": device,
        "message": "Destination device re-identified.",
    }


def _reconstruct_assessment(manifest, source_device):
    assessment_data = manifest.get("assessment")
    if not assessment_data:
        return None

    decision = Decision(
        status=assessment_data["decision"],
        reason=assessment_data.get("reason", ""),
        evidence="Loaded from persisted case.",
        law=None,
        risk=assessment_data.get("risk", "UNKNOWN"),
        confidence=assessment_data.get("confidence", 0),
        recommendation="Loaded from persisted case.",
    )

    return Assessment(
        device=source_device,
        decision=decision,
    )


def _reconstruct_intake(manifest):
    return {
        "case_contact": manifest.get("case_contact", {}),
        "intake": manifest.get("intake", {}),
    }


def _status_requires_source_device(status):
    return status not in (
        RecoveryStatus.NEW,
        RecoveryStatus.COMPLETED,
        RecoveryStatus.CANCELLED,
    )


def _is_recovery_engine_hold(manifest):
    """
    Identify ON_HOLD cases paused because the operator selected the
    Recovery Engine as source.
    """

    if manifest.get("status") != RecoveryStatus.ON_HOLD:
        return False

    assessment_data = manifest.get("assessment")
    if not assessment_data:
        return False

    if assessment_data.get("decision") != "STOP":
        return False

    device_data = manifest.get("device")
    if device_data and device_data.get("role") == "RECOVERY ENGINE":
        return True

    return (
        assessment_data.get("reason") == "Target is the Recovery Engine."
    )


def _artifact_status_warning(status, recovery_path):
    acquisition_state = classify_acquisition_state(recovery_path)
    artifact_state = acquisition_state["state"]

    if artifact_state == "completed_canonical" and status not in (
        RecoveryStatus.READY_FOR_RECOVERY,
        RecoveryStatus.RECOVERING,
        RecoveryStatus.COMPLETED,
        RecoveryStatus.CANCELLED,
    ):
        return (
            f"Persisted status is {status}, but canonical acquisition "
            "artifacts indicate READY_FOR_RECOVERY."
        )

    if artifact_state in (
        "incomplete_ddrescue",
        "imaging_complete_fingerprint_missing",
        "invalid_map",
        "inconsistent_artifacts",
    ) and status in (
        RecoveryStatus.READY_FOR_RECOVERY,
        RecoveryStatus.RECOVERING,
        RecoveryStatus.COMPLETED,
    ):
        return (
            f"Persisted status is {status}, but acquisition artifacts "
            f"indicate {artifact_state}."
        )

    return None


def _resolve_resume_status(session):
    acquisition_state = classify_acquisition_state(session.recovery_path)

    if acquisition_state["state"] == "completed_canonical":
        return RecoveryStatus.READY_FOR_RECOVERY

    if acquisition_state["state"] in (
        "no_acquisition",
        "incomplete_ddrescue",
        "imaging_complete_fingerprint_missing",
        "invalid_map",
        "inconsistent_artifacts",
    ):
        return RecoveryStatus.READY_FOR_IMAGING

    return RecoveryStatus.ASSESSING


def load_case(recovery_path, devices):
    """
    Load a persisted Recovery Case into runtime objects and re-identify devices.
    """

    warnings = []
    permitted_roots = [
        root_info["path"]
        for root_info in enumerate_all_permitted_roots(devices)
    ]

    try:
        case_path = Path(recovery_path).resolve()
        manifest = read_case_manifest(
            case_path,
            permitted_roots=permitted_roots,
        )
    except ManifestError as error:
        return {
            "success": False,
            "session": None,
            "intake": {"case_contact": {}, "intake": {}},
            "assessment": None,
            "devices": devices,
            "warnings": warnings,
            "code": CODE_MANIFEST_ERROR,
            "message": str(error),
            "display_args": {"message": str(error)},
        }

    if not case_path.is_dir():
        return {
            "success": False,
            "session": None,
            "intake": {"case_contact": {}, "intake": {}},
            "assessment": None,
            "devices": devices,
            "warnings": warnings,
            "code": CODE_CASE_PATH_NOT_ACCESSIBLE,
            "message": f"Recovery case path is not accessible: {case_path}",
            "display_args": {"case_path": str(case_path)},
        }

    try:
        created_at = datetime.fromisoformat(manifest["created_at"])
    except ValueError:
        return {
            "success": False,
            "session": None,
            "intake": {"case_contact": {}, "intake": {}},
            "assessment": None,
            "devices": devices,
            "warnings": warnings,
            "code": CODE_INVALID_CREATED_AT,
            "message": (
                f"case.json created_at is invalid: {manifest['created_at']}"
            ),
            "display_args": {"created_at": manifest["created_at"]},
        }

    status = manifest["status"]
    session = RecoverySession(
        session_id=manifest["session_id"],
        created_at=created_at,
        status=status,
        recovery_path=str(case_path),
        case_name=manifest.get("case_name", ""),
    )
    session.completed_at = manifest.get("completed_at")
    session.recovery_outcome = manifest.get("recovery_outcome")

    intake = _reconstruct_intake(manifest)

    source_result = {"success": True, "device": None, "message": ""}
    if _is_recovery_engine_hold(manifest):
        source_result = {
            "success": True,
            "device": None,
            "message": (
                "Source re-identification skipped: case is ON_HOLD because "
                "the Recovery Engine was selected as source."
            ),
        }
        warnings.append(source_result["message"])
    elif _status_requires_source_device(status) or manifest.get("device"):
        source_result = _reidentify_source_device(
            case_path,
            manifest,
            devices,
            warnings,
        )
        if not source_result["success"] and _status_requires_source_device(status):
            return {
                "success": False,
                "session": session,
                "intake": intake,
                "assessment": None,
                "devices": devices,
                "warnings": warnings,
                "code": source_result.get("code"),
                "message": source_result["message"],
                "display_args": source_result.get("display_args"),
            }

    session.source_device = source_result["device"]

    destination_result = _reidentify_destination_device(
        manifest,
        devices,
        warnings,
    )
    if destination_result["success"]:
        session.destination_device = destination_result["device"]
    elif manifest.get("destination"):
        warnings.append(destination_result["message"])
        if _destination_reidentify_failure_blocks_open(
            status,
            case_path,
            devices,
        ):
            return {
                "success": False,
                "session": session,
                "intake": intake,
                "assessment": _reconstruct_assessment(
                    manifest,
                    session.source_device,
                ),
                "devices": devices,
                "warnings": warnings,
                "code": destination_result.get("code"),
                "message": destination_result["message"],
                "display_args": destination_result.get("display_args"),
            }

    assessment = _reconstruct_assessment(manifest, session.source_device)
    session.assessment = assessment

    artifact_warning = _artifact_status_warning(status, case_path)
    if artifact_warning:
        warnings.append(artifact_warning)

    return {
        "success": True,
        "session": session,
        "intake": intake,
        "assessment": assessment,
        "devices": devices,
        "warnings": warnings,
        "code": CODE_CASE_LOADED,
        "message": "Recovery case loaded.",
    }


def resolve_resume_status(session):
    """
    Determine the workflow status to use when resuming a held or terminal case.
    """

    return _resolve_resume_status(session)
