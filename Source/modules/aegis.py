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


def _rule_source_must_be_unmounted(device: Device):
    """SL-008: a mounted source device must not be operated on."""

    if not device.is_mounted():
        return None

    return Decision(
        status="STOP",
        reason="Source device is currently mounted.",
        evidence=f"Selected device: {device.path}",
        law="SL-008",
        risk="CRITICAL",
        confidence=100,
        recommendation="Unmount the source device before continuing."
    )


def _serial_is_trustworthy(serial):
    normalized = "" if serial is None else str(serial).strip()
    if not normalized:
        return False
    return normalized.lower() not in ("unknown", "n/a")


def _rule_device_must_be_identified(device: Device):
    """SL-003: unknown devices shall never be acted upon."""

    if _serial_is_trustworthy(device.serial):
        return None

    return Decision(
        status="STOP",
        reason="Source device identity cannot be trusted.",
        evidence=f"Selected device: {device.path}",
        law="SL-003",
        risk="CRITICAL",
        confidence=100,
        recommendation=(
            "Verify the physical source device and obtain a trustworthy "
            "serial before continuing."
        ),
    )


# Ordered list of safety rules. Each rule returns a blocking Decision when its
# law is violated, or None when it has nothing to say. Future Sentinel Laws
# plug in here without changing the aggregation logic below.
RULES = [
    _rule_protect_recovery_engine,
    _rule_source_must_be_unmounted,
    _rule_device_must_be_identified,
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
