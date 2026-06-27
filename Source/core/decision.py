class Decision:
    def __init__(
        self,
        status,
        reason,
        evidence,
        law,
        risk,
        confidence,
        recommendation
    ):
        self.status = status
        self.reason = reason
        self.evidence = evidence
        self.law = law
        self.risk = risk
        self.confidence = confidence
        self.recommendation = recommendation

    def is_approved(self):
        return self.status == "APPROVED"

    def is_denied(self):
        return self.status == "DENIED"

    def has_warning(self):
        return self.status == "APPROVED_WITH_WARNING"
