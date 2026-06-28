#!/usr/bin/env python3

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from core.device import Device
from core.codex import Codex
from modules.aegis import evaluate


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
            access_mode=access_mode
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