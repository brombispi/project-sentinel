"""
Pure storage query helpers for forensic imaging safety.

No printing, logging, questions, or workflow decisions.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path


def get_block_device_size_bytes(device_path):
    """
    Return exact block-device size in bytes, or None on failure.
    """

    result = subprocess.run(
        ["blockdev", "--getsize64", device_path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def get_logical_sector_size(device_path):
    """
    Return logical sector size in bytes, or None on failure.
    """

    result = subprocess.run(
        ["blockdev", "--getss", device_path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def get_physical_sector_size(device_path):
    """
    Return physical sector size in bytes, or None on failure.
    """

    result = subprocess.run(
        ["blockdev", "--getpbsz", device_path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def query_lsblk_tree(device_path):
    """
    Return parsed lsblk JSON for a device and its descendants.
    """

    result = subprocess.run(
        [
            "lsblk",
            "-J",
            "-o",
            "NAME,PATH,TYPE,MOUNTPOINT,MOUNTPOINTS,FSTYPE,OPTIONS,PKNAME",
            device_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _mount_targets_from_node(node):
    """
    Collect mount targets from MOUNTPOINTS with MOUNTPOINT fallback.
    """

    targets = []

    mountpoints = node.get("mountpoints")
    if isinstance(mountpoints, list):
        for mountpoint in mountpoints:
            if mountpoint:
                targets.append(mountpoint)

    mountpoint = node.get("mountpoint")
    if mountpoint:
        targets.append(mountpoint)

    return targets


def _query_findmnt_for_source(device_path):
    """
    Return findmnt rows for a block device source path.
    """

    result = subprocess.run(
        [
            "findmnt",
            "-rn",
            "-S",
            device_path,
            "-o",
            "TARGET,SOURCE,OPTIONS",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return []

    rows = []

    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 2)

        if len(parts) < 2:
            continue

        target = parts[0]
        options = parts[2] if len(parts) >= 3 else ""

        rows.append(
            {
                "mount_target": target,
                "options": options,
            }
        )

    return rows


def _iter_descendant_nodes(node):
    """
    Yield a block-device node and all descendants from an lsblk tree.
    """

    yield node

    for child in node.get("children") or []:
        yield from _iter_descendant_nodes(child)


def _is_excluded_mount_target(mount_target, exclude_mount_targets):
    """
    True when a mount target must never be observed or unmounted.
    """

    if not mount_target:
        return True

    normalized_target = mount_target.rstrip("/") or "/"

    for excluded in exclude_mount_targets:
        if not excluded:
            continue

        normalized_excluded = excluded.rstrip("/") or "/"

        if normalized_target == normalized_excluded:
            return True

        if normalized_target.startswith(normalized_excluded + "/"):
            return True

    return False


def find_mounted_descendants(device_path, exclude_mount_targets=None):
    """
    Return a deduplicated list of mounted descendants for a source disk.

    Each item contains:
    - device_path
    - mount_target
    - options
    - type
    - filesystem
    """

    exclude_mount_targets = set(exclude_mount_targets or [])
    data = query_lsblk_tree(device_path)

    if not data:
        return []

    blockdevices = data.get("blockdevices") or []
    if not blockdevices:
        return []

    root = blockdevices[0]
    seen_targets = set()
    mounted = []

    for node in _iter_descendant_nodes(root):
        device = node.get("path") or ""
        node_type = node.get("type") or ""
        filesystem = node.get("fstype") or ""

        mount_targets = _mount_targets_from_node(node)
        findmnt_rows = _query_findmnt_for_source(device) if device else []

        if findmnt_rows:
            candidates = [
                {
                    "mount_target": row["mount_target"],
                    "options": row["options"],
                }
                for row in findmnt_rows
            ]
        else:
            candidates = [
                {
                    "mount_target": target,
                    "options": node.get("options") or "",
                }
                for target in mount_targets
            ]

        for candidate in candidates:
            mount_target = candidate["mount_target"]

            if not mount_target:
                continue

            if _is_excluded_mount_target(mount_target, exclude_mount_targets):
                continue

            if mount_target in seen_targets:
                continue

            seen_targets.add(mount_target)
            mounted.append(
                {
                    "device_path": device,
                    "mount_target": mount_target,
                    "options": candidate["options"],
                    "type": node_type,
                    "filesystem": filesystem,
                }
            )

    return mounted


def validate_ddrescue_map(map_path):
    """
    Validate a ddrescue map using ddrescuelog -t.

    Returns:
    - valid: True when map is readable
    - exit_code: ddrescuelog exit code, or None when unavailable
    """

    map_path = str(map_path)

    if not Path(map_path).is_file():
        return {
            "valid": False,
            "exit_code": None,
        }

    if not shutil.which("ddrescuelog"):
        return {
            "valid": False,
            "exit_code": None,
        }

    completed = subprocess.run(
        ["ddrescuelog", "-t", map_path],
        capture_output=True,
        text=True,
    )

    return {
        "valid": completed.returncode != 2,
        "exit_code": completed.returncode,
    }


_STATUS_CHAR_PATTERN = re.compile(r"^[?*/\-+]$")


def read_ddrescue_map_current_status(map_path):
    """
    Read current_status from the first GNU ddrescue mapfile data line.

    The current-status line has two fields: current position and status.
    Later three-field rows are block-status entries and are ignored here.

    Returns the status character, or None when it cannot be interpreted.
    """

    map_path = Path(map_path)

    if not map_path.is_file():
        return None

    try:
        with map_path.open("r", encoding="utf-8", errors="replace") as map_file:
            for line in map_file:
                stripped = line.strip()

                if not stripped or stripped.startswith("#"):
                    continue

                parts = stripped.split()

                if len(parts) == 2 and _STATUS_CHAR_PATTERN.match(parts[1]):
                    return parts[1]

                return None
    except OSError:
        return None

    return None


def classify_ddrescue_map_status(map_path):
    """
    Classify ddrescue map completion from validation and current_status.

    Returns:
    - status: unreadable | incomplete | finished
    - current_status: mapfile status character when known
    - validation: validate_ddrescue_map() result
    """

    validation = validate_ddrescue_map(map_path)
    current_status = read_ddrescue_map_current_status(map_path)

    if not validation["valid"]:
        return {
            "status": "unreadable",
            "current_status": current_status,
            "validation": validation,
        }

    if current_status is None:
        return {
            "status": "unreadable",
            "current_status": None,
            "validation": validation,
        }

    if current_status == "+":
        return {
            "status": "finished",
            "current_status": current_status,
            "validation": validation,
        }

    return {
        "status": "incomplete",
        "current_status": current_status,
        "validation": validation,
    }


def _run_map_status_checks():
    """
    Local checks for GNU ddrescue map current-status parsing.
    """

    import tempfile

    incomplete_map = (
        "# Mapfile. Created by GNU ddrescue\n"
        "# current_pos  current_status  current_pass\n"
        "0x00000000     ?\n"
        "0x00000000  0x00100000  ?\n"
    )
    finished_map = (
        "# Mapfile. Created by GNU ddrescue\n"
        "# current_pos  current_status  current_pass\n"
        "0x01000000     +\n"
        "0x00000000  0x01000000  +\n"
    )
    block_only_map = (
        "# Mapfile. Created by GNU ddrescue\n"
        "0x00000000  0x00100000  ?\n"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        incomplete_path = Path(tmpdir) / "incomplete.map"
        finished_path = Path(tmpdir) / "finished.map"
        block_only_path = Path(tmpdir) / "block_only.map"

        incomplete_path.write_text(incomplete_map, encoding="utf-8")
        finished_path.write_text(finished_map, encoding="utf-8")
        block_only_path.write_text(block_only_map, encoding="utf-8")

        checks = [
            (
                "incomplete current-status line",
                read_ddrescue_map_current_status(incomplete_path),
                "?",
            ),
            (
                "finished current-status line",
                read_ddrescue_map_current_status(finished_path),
                "+",
            ),
            (
                "block row without current-status line",
                read_ddrescue_map_current_status(block_only_path),
                None,
            ),
        ]

        failures = []

        for label, actual, expected in checks:
            if actual != expected:
                failures.append(f"{label}: expected {expected!r}, got {actual!r}")

        if failures:
            raise AssertionError("; ".join(failures))

    return checks


if __name__ == "__main__":
    for label, actual, expected in _run_map_status_checks():
        print(f"{label}: {actual!r} (expected {expected!r})")
    print("map status checks passed")
