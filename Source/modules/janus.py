from dataclasses import dataclass


@dataclass
class DestinationAssessment:
    approved: bool
    reason: str
    risk: str


def evaluate_destination(device):
    """
    Evaluate whether a device can safely be used as
    the recovery destination.
    """

    if device.protected:
        return DestinationAssessment(
            approved=False,
            reason="Recovery Engine cannot be used as a recovery destination.",
            risk="CRITICAL"
        )

    if not device.mount_point:
        return DestinationAssessment(
            approved=False,
            reason="Destination is not mounted or has no writable mount point.",
            risk="HIGH"
        )

    return DestinationAssessment(
        approved=True,
        reason=f"Destination device approved at {device.mount_point}.",
        risk="LOW"
    )