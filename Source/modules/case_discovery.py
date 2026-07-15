import json
import re
import shutil
from pathlib import Path

from modules.manifest import read_case_manifest

CASE_ID_PATTERN = re.compile(r"^REC-\d{4}-\d{6}$")


def get_runtime_recoveries_root():
    return Path(__file__).resolve().parent.parent / "Recoveries"


def get_runtime_archive_root():
    return Path(__file__).resolve().parent.parent / "Archive"


def _path_under_root(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()

    try:
        return path.is_relative_to(root)
    except AttributeError:
        return str(path).startswith(str(root))


def enumerate_permitted_roots(devices):
    """
    Return permitted Recovery Case roots: local runtime and mounted volumes.
    """

    roots = []

    local_root = get_runtime_recoveries_root()
    if local_root.is_dir():
        roots.append(
            {
                "path": local_root.resolve(),
                "location_label": "local",
                "is_local": True,
            }
        )

    seen_mount_points = set()

    for device in devices:
        if device.role == "RECOVERY ENGINE":
            continue

        mount_point = device.mount_point
        if not mount_point:
            continue

        mount_path = Path(mount_point).resolve()
        if mount_path in seen_mount_points:
            continue

        recoveries_root = mount_path / "Recoveries"
        if not recoveries_root.is_dir():
            continue

        seen_mount_points.add(mount_path)
        roots.append(
            {
                "path": recoveries_root.resolve(),
                "location_label": mount_path.name,
                "is_local": False,
            }
        )

    return roots


def enumerate_archive_roots(devices):
    """
    Return permitted Archive roots: local runtime and mounted volumes.
    """

    roots = []

    local_root = get_runtime_archive_root()
    if local_root.is_dir():
        roots.append(
            {
                "path": local_root.resolve(),
                "location_label": "local",
                "is_local": True,
            }
        )

    seen_mount_points = set()

    for device in devices:
        if device.role == "RECOVERY ENGINE":
            continue

        mount_point = device.mount_point
        if not mount_point:
            continue

        mount_path = Path(mount_point).resolve()
        if mount_path in seen_mount_points:
            continue

        archive_root = mount_path / "Archive"
        if not archive_root.is_dir():
            continue

        seen_mount_points.add(mount_path)
        roots.append(
            {
                "path": archive_root.resolve(),
                "location_label": mount_path.name,
                "is_local": False,
            }
        )

    return roots


def enumerate_all_permitted_roots(devices):
    """
    Return active Recoveries roots and Archive roots for case loading.
    """

    seen_paths = set()
    roots = []

    for root_info in (
        enumerate_permitted_roots(devices) + enumerate_archive_roots(devices)
    ):
        root_path = root_info["path"]
        if root_path in seen_paths:
            continue

        seen_paths.add(root_path)
        roots.append(root_info)

    return roots


def is_archived_case_path(case_path, devices):
    case_path = Path(case_path).resolve()

    for root_info in enumerate_archive_roots(devices):
        if _path_under_root(case_path, root_info["path"]):
            return True

    return False


def archive_case(session):
    """
    Move a recovery case from Recoveries/ to Archive/ on the same storage.

    Preserves every file unchanged. Does not modify case.json or session_id.
    """

    result = {
        "success": False,
        "message": "",
    }

    case_path = Path(session.recovery_path).resolve()
    recoveries_root = case_path.parent
    archive_root = recoveries_root.parent / "Archive"
    dest_path = archive_root / session.session_id

    if not case_path.is_dir():
        result["code"] = "CASE_NOT_FOUND"
        result["message"] = f"Recovery case folder not found: {case_path}"
        result["display_args"] = {"case_path": str(case_path)}
        return result

    if dest_path.exists():
        result["code"] = "ARCHIVE_EXISTS"
        result["message"] = (
            f"Archive destination already exists: {dest_path}"
        )
        result["display_args"] = {"dest_path": str(dest_path)}
        return result

    try:
        archive_root.mkdir(parents=False, exist_ok=True)
    except OSError as error:
        result["code"] = "ARCHIVE_FAILED"
        result["message"] = f"Case archive failed: {error}"
        result["display_args"] = {"error": str(error)}
        return result

    try:
        shutil.move(str(case_path), str(dest_path))
    except OSError as error:
        result["code"] = "ARCHIVE_FAILED"
        result["message"] = f"Case archive failed: {error}"
        result["display_args"] = {"error": str(error)}
        return result

    session.recovery_path = str(dest_path.resolve())
    result["success"] = True
    result["code"] = "ARCHIVED_SUCCESS"
    result["message"] = f"Case archived to {dest_path}"
    result["display_args"] = {"dest_path": str(dest_path)}
    return result


def reopen_case(session):
    """
    Move a recovery case from Archive/ back to Recoveries/ on the same storage.

    Preserves every file unchanged. Does not modify case.json or session_id.
    """

    result = {
        "success": False,
        "message": "",
    }

    case_path = Path(session.recovery_path).resolve()
    archive_root = case_path.parent
    recoveries_root = archive_root.parent / "Recoveries"
    dest_path = recoveries_root / session.session_id

    if not case_path.is_dir():
        result["code"] = "ARCHIVED_NOT_FOUND"
        result["message"] = f"Archived case folder not found: {case_path}"
        result["display_args"] = {"case_path": str(case_path)}
        return result

    if dest_path.exists():
        result["code"] = "REOPEN_EXISTS"
        result["message"] = (
            f"Recoveries destination already exists: {dest_path}"
        )
        result["display_args"] = {"dest_path": str(dest_path)}
        return result

    try:
        recoveries_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(case_path), str(dest_path))
    except OSError as error:
        result["code"] = "REOPEN_FAILED"
        result["message"] = f"Case reopen failed: {error}"
        result["display_args"] = {"error": str(error)}
        return result

    session.recovery_path = str(dest_path.resolve())
    result["success"] = True
    result["code"] = "REOPENED_SUCCESS"
    result["message"] = f"Case reopened to {dest_path}"
    result["display_args"] = {"dest_path": str(dest_path)}
    return result


def _scan_case_directory(case_dir, root_info, warnings):
    session_id = case_dir.name

    if not CASE_ID_PATTERN.match(session_id):
        warnings.append(
            f"Skipped non-conforming case directory: {case_dir}"
        )
        return None

    manifest_path = case_dir / "case.json"
    if not manifest_path.is_file():
        return None

    try:
        manifest = read_case_manifest(
            case_dir,
            permitted_roots=[root_info["path"]],
        )
    except Exception as error:
        warnings.append(
            f"Skipped malformed case at {case_dir}: {error}"
        )
        return None

    record_warnings = []

    return {
        "session_id": manifest["session_id"],
        "case_name": manifest.get("case_name", ""),
        "status": manifest["status"],
        "created_at": manifest["created_at"],
        "recovery_path": str(case_dir.resolve()),
        "location_label": root_info["location_label"],
        "is_local": root_info["is_local"],
        "warnings": record_warnings,
    }


def _deduplicate_records(records, warnings):
    by_session_id = {}

    for record in records:
        session_id = record["session_id"]
        existing = by_session_id.get(session_id)

        if existing is None:
            by_session_id[session_id] = record
            continue

        prefer_new = record
        prefer_existing = existing

        if existing["is_local"] and not record["is_local"]:
            prefer_new = record
            prefer_existing = existing
        elif record["is_local"] and not existing["is_local"]:
            prefer_new = existing
            prefer_existing = record
        else:
            warnings.append(
                f"Duplicate case {session_id} at {existing['recovery_path']} "
                f"and {record['recovery_path']}; keeping "
                f"{existing['recovery_path']}."
            )
            continue

        warnings.append(
            f"Duplicate case {session_id} at {prefer_existing['recovery_path']} "
            f"and {record['recovery_path']}; preferring "
            f"{prefer_new['recovery_path']}."
        )
        prefer_new.setdefault("warnings", []).append(
            "Duplicate case ID resolved in favour of this path."
        )
        by_session_id[session_id] = prefer_new

    return list(by_session_id.values())


def discover_cases(devices, *, archived=False):
    """
    Scan permitted recovery roots and return discoverable case records.

    Active cases are discovered under Recoveries/ only.
    Archived cases are discovered under Archive/ only.

    Read-only. Does not prompt, log to case audit logs, or modify cases.
    """

    warnings = []
    records = []

    if archived:
        permitted_roots = enumerate_archive_roots(devices)
    else:
        permitted_roots = enumerate_permitted_roots(devices)

    for root_info in permitted_roots:
        root_path = root_info["path"]

        try:
            child_dirs = sorted(
                path for path in root_path.iterdir() if path.is_dir()
            )
        except OSError as error:
            warnings.append(
                f"Could not scan recovery root {root_path}: {error}"
            )
            continue

        for case_dir in child_dirs:
            record = _scan_case_directory(case_dir, root_info, warnings)
            if record is not None:
                records.append(record)

    records = _deduplicate_records(records, warnings)
    records.sort(
        key=lambda item: (item["created_at"], item["session_id"]),
        reverse=True,
    )

    return {
        "records": records,
        "warnings": warnings,
    }
