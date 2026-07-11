def format_bytes(size_bytes):
    """
    Format a byte count as a human-readable size string.
    """

    if size_bytes <= 0:
        return "0 B"

    units = ("B", "KB", "MB", "GB", "TB", "PB")
    size = float(size_bytes)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def _smart_summary(smart_result):
    if smart_result is None:
        return "Not performed"

    if smart_result["available"]:
        return f"Available ({smart_result['health']})"

    return "Not available"


def _integrity_summary(integrity_result):
    if integrity_result is None:
        return "Not performed"

    if integrity_result["success"]:
        return "Hash recorded"

    return "Failed"


def _imaging_summary(imaging_result, imaging_declined):
    if imaging_declined:
        return "Declined by operator"

    if imaging_result is None:
        return "Not performed"

    if imaging_result["success"]:
        return "Completed"

    return "Failed"


def _photorec_summary(recovery_result, recovery_declined):
    if recovery_declined:
        return "Declined by operator"

    if recovery_result is None:
        return "Not performed"

    if recovery_result["success"]:
        return "Ended normally"

    return "Failed"


def _recovered_files_summary(recovery_result, recovery_declined):
    if recovery_declined or recovery_result is None:
        return "Not performed"

    return str(recovery_result["recovered_file_count"])


def _recovered_size_summary(recovery_result, recovery_declined):
    if recovery_declined or recovery_result is None:
        return "Not performed"

    return format_bytes(recovery_result["recovered_total_bytes"])


def print_summary(
    assessment,
    strategy,
    session,
    smart_result=None,
    imaging_result=None,
    integrity_result=None,
    recovery_result=None,
    imaging_declined=False,
    recovery_declined=False,
):
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

    print(f"Assessment      : {assessment.decision.status}")
    print(f"Goal            : {strategy.goal}")
    print(f"Priority        : {strategy.priority}")
    print(f"SMART           : {_smart_summary(smart_result)}")
    print(f"Imaging         : {_imaging_summary(imaging_result, imaging_declined)}")
    print(f"Integrity       : {_integrity_summary(integrity_result)}")
    print(
        f"PhotoRec session: "
        f"{_photorec_summary(recovery_result, recovery_declined)}"
    )
    print(
        f"Recovered files : "
        f"{_recovered_files_summary(recovery_result, recovery_declined)}"
    )
    print(
        f"Recovered size  : "
        f"{_recovered_size_summary(recovery_result, recovery_declined)}"
    )
    print(f"Current status  : {session.status}")
    print(f"Case location   : {session.recovery_path}")

    print()
    print("Assessment Complete")
