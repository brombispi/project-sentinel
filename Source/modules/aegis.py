from core.device import Device
from core.decision import Decision
from core.assessment import Assessment
from core.codex import Codex


def evaluate(device: Device):

    codex = Codex()

    information = []
    warnings = []
    recommendations = []

    for filesystem in device.filesystem.split(","):

        key = filesystem.strip().lower()

        knowledge = codex.lookup("filesystem", key)

        if not knowledge:
            continue

        if "warning" in knowledge:
            warnings.append(knowledge["warning"])

        if "recommended_action" in knowledge:
            recommendations.append(
                knowledge["recommended_action"]
            )

        if "risk" in knowledge:
            information.append(
                f"{key.upper()}: {knowledge['risk']}"
            )

    if device.is_protected():

        decision = Decision(
            status="STOP",
            reason="Target is the Recovery Engine.",
            evidence=f"Selected device: {device.path}",
            law="SL-001",
            risk="CRITICAL",
            confidence=100,
            recommendation="Select an external customer storage device."
        )

        return Assessment(
            device=device,
            decision=decision,
            information=information,
            warnings=warnings,
            recommendations=[
                "Select an external customer storage device."
            ] + recommendations
        )

    recommendations.append(
        "Proceed with assessment."
    )

    if device.is_mounted():

        warnings.append(
            "External device is currently mounted. Mounted filesystems may receive unintended write operations."
        )

        recommendations.append(
            "Unmount the device before imaging or recovery whenever possible."
        )

    decision = Decision(
        status="APPROVED",
        reason="External device.",
        evidence=f"Selected device: {device.path}",
        law=None,
        risk="LOW",
        confidence=100,
        recommendation="Proceed with assessment."
    )

    return Assessment(
        device=device,
        decision=decision,
        information=information,
        warnings=warnings,
        recommendations=recommendations
    )
