import json
import re
from pathlib import Path

from modules.manifest import read_case_manifest

CASE_ID_PATTERN = re.compile(r"^REC-\d{4}-\d{6}$")


def get_runtime_recoveries_root():
    return Path(__file__).resolve().parent.parent / "Recoveries"


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


def discover_cases(devices):
    """
    Scan permitted recovery roots and return discoverable case records.

    Read-only. Does not prompt, log to case audit logs, or modify cases.
    """

    warnings = []
    records = []

    for root_info in enumerate_permitted_roots(devices):
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
