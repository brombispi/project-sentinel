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

    return DestinationAssessment(
        approved=True,
        reason="Destination device approved.",
        risk="LOW"
    )