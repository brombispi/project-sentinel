from core.decision import Decision


class Assessment:
    def __init__(
        self,
        device,
        decision: Decision,
        information=None,
        warnings=None,
        recommendations=None
    ):
        self.device = device
        self.decision = decision
        self.information = information or []
        self.warnings = warnings or []
        self.recommendations = recommendations or []

    def is_approved(self):
        return self.decision.is_approved()

    def is_denied(self):
        return self.decision.is_denied()

    def has_warnings(self):
        return len(self.warnings) > 0
