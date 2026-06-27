class Strategy:
    """
    Immutable recovery strategy produced by ORACLE.

    A Strategy describes what should be done next.
    It does not execute any operation.
    """

    def __init__(
        self,
        status,
        goal,
        priority,
        steps,
        reason,
        warnings=None
    ):
        self.status = status
        self.goal = goal
        self.priority = priority
        self.steps = tuple(steps)
        self.warnings = tuple(warnings or [])
        self.reason = reason