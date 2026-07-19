from core.device import Device
from core.decision import Decision
from core.assessment import Assessment
from core.codex import Codex


def _rule_protect_recovery_engine(device: Device):
    """SL-001: never permit operations targeting the Recovery Engine."""

    if not device.is_protected():
        return None

    return Decision(
        status="STOP",
        reason="Target is the Recovery Engine.",
        evidence=f"Selected device: {device.path}",
        law="SL-001",
        risk="CRITICAL",
        confidence=100,
        recommendation="Select an external customer storage device."
    )


# Ordered list of safety rules. Each rule returns a blocking Decision when its
# law is violated, or None when it has nothing to say. Future Sentinel Laws
# plug in here without changing the aggregation logic below.
RULES = [
    _rule_protect_recovery_engine,
]


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

    for rule in RULES:

        decision = rule(device)

        if decision is None:
            continue

        return Assessment(
            device=device,
            decision=decision,
            information=information,
            warnings=warnings,
            recommendations=[
                decision.recommendation
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
