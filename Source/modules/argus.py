#!/usr/bin/env python3

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from core.device import Device
from core.codex import Codex
from modules.aegis import evaluate
from modules.storage_query import (
    find_mounted_descendants,
    get_block_device_size_bytes,
    get_logical_sector_size,
    get_physical_sector_size,
)


def run_command(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()


def get_system_drive():
    root_source = run_command(["findmnt", "-n", "-o", "SOURCE", "/"])
    parent_disk = run_command(["lsblk", "-no", "PKNAME", root_source])
    return parent_disk


def is_mounted(device_name):
    result = subprocess.run(
        ["lsblk", "-n", "-o", "MOUNTPOINT", f"/dev/{device_name}"],
        capture_output=True,
        text=True
    )
    return result.stdout.strip() != ""


def get_filesystem(device_name):
    result = subprocess.run(
        ["lsblk", "-n", "-o", "FSTYPE", f"/dev/{device_name}"],
        capture_output=True,
        text=True
    )

    filesystems = []

    for line in result.stdout.strip().splitlines():
        filesystem = line.strip()
        if filesystem != "" and filesystem not in filesystems:
            filesystems.append(filesystem)

    if not filesystems:
        return "Unknown"

    return ", ".join(filesystems)


def get_mount_point(device_name):
    result = subprocess.run(
        ["lsblk", "-nr", "-o", "NAME,PKNAME,PATH", f"/dev/{device_name}"],
        capture_output=True,
        text=True
    )

    children = {}
    paths = {}

    for line in result.stdout.strip().splitlines():
        parts = line.split()

        if len(parts) < 3:
            continue

        name = parts[0]
        parent_name = parts[1]
        path = parts[2]

        paths[name] = path

        if parent_name not in children:
            children[parent_name] = []
        children[parent_name].append(name)

    for child in children.get(device_name, []):
        stack = [child]

        while stack:
            name = stack.pop()
            source = paths.get(name)

            if source:
                mount_result = subprocess.run(
                    ["findmnt", "-rn", "-S", source, "-o", "TARGET,OPTIONS"],
                    capture_output=True,
                    text=True
                )

                mount_line = mount_result.stdout.strip()

                if mount_line:
                    mount_parts = mount_line.split(None, 1)

                    if len(mount_parts) >= 2:
                        target, options = mount_parts[0], mount_parts[1]

                        if "rw" in options.split(","):
                            return target

            for descendant in reversed(children.get(name, [])):
                stack.append(descendant)

    return None


def get_access_mode(device_name):
    result = subprocess.run(
        ["lsblk", "-nr", "-o", "NAME,PKNAME", f"/dev/{device_name}"],
        capture_output=True,
        text=True
    )

    found_read_only = False

    for line in result.stdout.strip().splitlines():
        parts = line.split()

        if len(parts) < 2:
            continue

        partition_name = parts[0]
        parent_name = parts[1]

        if parent_name != device_name:
            continue

        source = f"/dev/{partition_name}"

        mount_result = subprocess.run(
            ["findmnt", "-rn", "-S", source, "-o", "OPTIONS"],
            capture_output=True,
            text=True
        )

        options = mount_result.stdout.strip()

        if options == "":
            continue

        option_list = options.split(",")

        if "rw" in option_list:
            return "READ_WRITE"

        if "ro" in option_list:
            found_read_only = True

    if found_read_only:
        return "READ_ONLY"

    return "UNKNOWN"


def get_filesystem_knowledge(codex, filesystem_text):
    if not filesystem_text or filesystem_text == "Unknown":
        return []

    knowledge_items = []

    for filesystem in filesystem_text.split(","):
        key = filesystem.strip().lower()
        knowledge = codex.lookup("filesystem", key)

        if knowledge:
            knowledge_items.append({
                "filesystem": key,
                "knowledge": knowledge
            })

    return knowledge_items


OVERALL_HEALTH_PATTERN = re.compile(
    r"SMART overall-health self-assessment test result:\s*(\w+)",
    re.IGNORECASE,
)


def _combine_smartctl_output(stdout, stderr):
    parts = []

    if stdout:
        parts.append(stdout)

    if stderr:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        parts.append(stderr)

    return "".join(parts)


def _parse_overall_health(combined_output):
    match = OVERALL_HEALTH_PATTERN.search(combined_output)

    if not match:
        return None

    value = match.group(1).upper()

    if value in ("PASSED", "FAILED"):
        return value

    return None


def _smartctl_execution_failed(returncode):
    """
    True when exit code bits 0-1 indicate smartctl could not run properly.

    Bit 0: command-line parse failure.
    Bit 1: device open/access failure.
    Higher bits report SMART status without implying the command did not run.
    """
    return (returncode & 3) != 0


def _smartctl_execution_warning(returncode, device_path):
    parse_failed = bool(returncode & 1)
    access_failed = bool(returncode & 2)

    if parse_failed and access_failed:
        return (
            f"smartctl command-line or parse failure and device open/access "
            f"failure for {device_path} (exit code {returncode})."
        )

    if parse_failed:
        return (
            f"smartctl command-line or parse failure "
            f"(exit code {returncode})."
        )

    return (
        f"smartctl device open/access failure for {device_path} "
        f"(exit code {returncode})."
    )


def observe_mounted_descendants(
    device_path,
    exclude_mount_targets=None,
    session=None,
):
    """
    Observe mounted descendants of a source disk for SENTINEL presentation.

    Returns observation facts only. Does not unmount or decide workflow.
    """

    from modules.echo import log_info

    mounted_descendants = find_mounted_descendants(
        device_path,
        exclude_mount_targets=exclude_mount_targets,
    )

    if session is not None:
        if mounted_descendants:
            mount_summary = ", ".join(
                f"{item['device_path']} -> {item['mount_target']}"
                for item in mounted_descendants
            )
            log_info(
                session,
                "ARGUS",
                f"Mounted descendants observed: {mount_summary}",
            )
        else:
            log_info(
                session,
                "ARGUS",
                f"No mounted descendants observed for {device_path}.",
            )

    return mounted_descendants


def observe_source_storage_identity(device_path):
    """
    Observe exact source storage identity facts for acquisition evidence.
    """

    return {
        "path": device_path,
        "size_bytes": get_block_device_size_bytes(device_path),
        "logical_sector_size": get_logical_sector_size(device_path),
        "physical_sector_size": get_physical_sector_size(device_path),
    }


def collect_smart_report(device, output_path):
    """
    Run smartctl against a device and save the raw report.

    Returns observation facts only. Does not interpret SMART attributes.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "available": False,
        "health": "not reported",
        "output_path": str(output_path),
        "warning": None,
    }

    if not shutil.which("smartctl"):
        result["warning"] = "smartctl is not installed."
        output_path.write_text(result["warning"] + "\n", encoding="utf-8")
        return result

    completed = subprocess.run(
        ["smartctl", "-a", device.path],
        capture_output=True,
        text=True,
    )

    combined_output = _combine_smartctl_output(
        completed.stdout,
        completed.stderr,
    )
    output_path.write_text(combined_output, encoding="utf-8")

    if _smartctl_execution_failed(completed.returncode):
        result["warning"] = _smartctl_execution_warning(
            completed.returncode,
            device.path,
        )
        return result

    health = _parse_overall_health(combined_output)

    if health in ("PASSED", "FAILED"):
        result["available"] = True
        result["health"] = health
        return result

    if combined_output.strip():
        result["available"] = True
        result["health"] = "not reported"
        return result

    result["warning"] = "smartctl produced no output."
    return result


def detect_devices():
    system_drive = get_system_drive()
    codex = Codex()
    devices = []

    result = subprocess.run(
        ["lsblk", "-d", "-J", "-o", "NAME,MODEL,SERIAL,SIZE,TRAN"],
        capture_output=True,
        text=True,
        check=True
    )

    data = json.loads(result.stdout)

    for item in data["blockdevices"]:
        name = item.get("name", "")
        model = item.get("model", "") or "Unknown"
        serial = item.get("serial", "") or "Unknown"
        size = item.get("size", "") or "Unknown"
        transport = item.get("tran", "") or "Unknown"

        if name == "":
            continue

        if name == system_drive:
            role = "RECOVERY ENGINE"
            protected = True
        else:
            role = "EXTERNAL DEVICE"
            protected = False

        mount_point = get_mount_point(name)
        mounted = is_mounted(name)
        filesystem = get_filesystem(name)
        access_mode = get_access_mode(name)
        knowledge_items = get_filesystem_knowledge(codex, filesystem)

        device = Device(
            name=name,
            model=model,
            serial=serial,
            size=size,
            transport=transport,
            role=role,
            protected=protected,
            mounted=mounted,
            filesystem=filesystem,
            access_mode=access_mode,
            mount_point=mount_point,
        )

        device.knowledge = knowledge_items
        devices.append(device)

    return devices


if __name__ == "__main__":

    print("=" * 50)
    print("ARGUS")
    print("Device Detection Engine")
    print("=" * 50)
    print()

    for device in detect_devices():
        assessment = evaluate(device)
        decision = assessment.decision

        print(f"Device      : {device.path}")
        print(f"Model       : {device.model}")
        print(f"Serial      : {device.serial}")
        print(f"Size        : {device.size}")
        print(f"Transport   : {device.transport}")
        print(f"Filesystem  : {device.filesystem}")
        print(f"Access Mode : {device.access_mode}")
        print(f"Role        : {device.role}")
        print(f"Protected   : {'YES' if device.protected else 'NO'}")
        print(f"Mounted     : {'YES' if device.mounted else 'NO'}")

        if getattr(device, "knowledge", []):
            print()
            print("CODEX")
            for item in device.knowledge:
                knowledge = item["knowledge"]
                filesystem = item["filesystem"]

                print(f"- Filesystem: {filesystem}")

                if "warning" in knowledge:
                    print(f"  Warning: {knowledge['warning']}")

                if "risk" in knowledge:
                    print(f"  Risk: {knowledge['risk']}")

                if "recommended_action" in knowledge:
                    print(f"  Recommended Action: {knowledge['recommended_action']}")

        print()

        print("AEGIS")
        print(f"Decision    : {decision.status}")
        print(f"Reason      : {decision.reason}")
        print(f"Evidence    : {decision.evidence}")
        print(f"Risk        : {decision.risk}")
        print(f"Confidence  : {decision.confidence}%")
        print(f"Next Step   : {decision.recommendation}")

        if decision.law:
            print(f"Law         : {decision.law}")

        if assessment.warnings:
            print()
            print("Warnings")
            for warning in assessment.warnings:
                print(f"- {warning}")

        if assessment.information:
            print()
            print("Information")
            for info in assessment.information:
                print(f"- {info}")

        if assessment.recommendations:
            print()
            print("Recommended Actions")
            for recommendation in assessment.recommendations:
                print(f"- {recommendation}")

        print("-" * 50)