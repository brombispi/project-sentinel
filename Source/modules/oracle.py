from core.assessment import Assessment
from core.strategy import Strategy


def create_strategy(assessment: Assessment):
    """
    Create a recovery strategy from an assessment.

    ORACLE never performs recovery.
    It only produces a strategy.
    """

    if assessment.decision.status == "STOP":
        return Strategy(
            status="STOP",
            goal="Protect the original device.",
            priority="CRITICAL",
            steps=[
                "Do not perform any recovery operation.",
                assessment.decision.recommendation
            ],
            reason=assessment.decision.reason
        )

    return Strategy(
        status="APPROVED",
        goal="Preserve the original device.",
        priority="HIGH",
        steps=[
            "Create a forensic image.",
            "Verify image integrity.",
            "Perform recovery on the image, not the original device."
        ],
        reason=assessment.decision.reason
    )


def recommend_recovery_method():
    """
    Recommend a recovery operation after integrity verification.

    ORACLE never performs recovery.
    It only produces a recommendation.
    """

    return {
        "recommended_operation": "photorec",
        "confidence": "LOW",
        "reason": "PhotoRec is currently the only integrated recovery method.",
    }