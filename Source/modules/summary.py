def print_summary(assessment, strategy):
    """
    Display the final assessment summary.

    SUMMARY does not make decisions.
    SUMMARY only translates module outputs
    into a clear human-readable conclusion.
    """

    print()
    print("==========================================")
    print("SUMMARY")
    print("==========================================")
    print()

    print(f"Assessment : {assessment.decision.status}")
    print(f"Goal       : {strategy.goal}")
    print(f"Priority   : {strategy.priority}")

    print()
    print("Assessment Complete")