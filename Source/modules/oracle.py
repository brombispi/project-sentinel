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
            goal="oracle.goal.protect_device",
            priority="CRITICAL",
            steps=[
                "oracle.step.stop_no_recovery",
                assessment.decision.recommendation,
            ],
            reason=assessment.decision.reason,
        )

    return Strategy(
        status="APPROVED",
        goal="oracle.goal.preserve_device",
        priority="HIGH",
        steps=[
            "oracle.step.create_forensic_image",
            "oracle.step.verify_image_integrity",
            "oracle.step.recover_on_image",
        ],
        reason=assessment.decision.reason,
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
        "reason": "oracle.recovery.photorec_only",
    }
