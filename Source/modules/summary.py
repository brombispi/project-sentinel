from i18n import tr


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
        return tr("summary.value.not_performed")

    if smart_result["available"]:
        return tr(
            "summary.value.smart_available",
            health=smart_result["health"],
        )

    return tr("summary.value.not_available")


def _integrity_summary(integrity_result):
    if integrity_result is None:
        return tr("summary.value.not_performed")

    if integrity_result["success"]:
        return tr("summary.value.hash_recorded")

    return tr("summary.value.failed")


def _imaging_summary(imaging_result, imaging_declined):
    if imaging_declined:
        return tr("summary.value.declined")

    if imaging_result is None:
        return tr("summary.value.not_performed")

    if imaging_result["success"]:
        return tr("summary.value.completed")

    return tr("summary.value.failed")


def _recovery_method_summary(
    recovery_selection_cancelled,
    recovery_declined,
    recovery_result,
):
    if recovery_selection_cancelled:
        return tr("summary.value.cancelled")

    if recovery_declined or recovery_result is not None:
        return tr("summary.value.photorec")

    return tr("summary.value.not_performed")


def _photorec_summary(
    recovery_result,
    recovery_declined,
    recovery_selection_cancelled=False,
):
    if recovery_selection_cancelled:
        return tr("summary.value.not_performed")

    if recovery_declined:
        return tr("summary.value.declined")

    if recovery_result is None:
        return tr("summary.value.not_performed")

    if recovery_result["success"]:
        return tr("summary.value.ended_normally")

    return tr("summary.value.failed")


def _recovered_files_summary(
    recovery_result,
    recovery_declined,
    recovery_selection_cancelled=False,
):
    if recovery_selection_cancelled or recovery_declined or recovery_result is None:
        return tr("summary.value.not_performed")

    return str(recovery_result["recovered_file_count"])


def _recovered_size_summary(
    recovery_result,
    recovery_declined,
    recovery_selection_cancelled=False,
):
    if recovery_selection_cancelled or recovery_declined or recovery_result is None:
        return tr("summary.value.not_performed")

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
    recovery_selection_cancelled=False,
):
    """
    Display the final assessment summary.

    SUMMARY does not make decisions.
    SUMMARY only translates module outputs
    into a clear human-readable conclusion.
    """

    from i18n import display_oracle_goal

    print()
    print("==========================================")
    print(tr("summary.title"))
    print("==========================================")
    print()

    print(tr("summary.label.assessment"), assessment.decision.status)
    print(tr("summary.label.goal"), display_oracle_goal(strategy.goal))
    print(tr("summary.label.priority"), strategy.priority)
    print(tr("summary.label.smart"), _smart_summary(smart_result))
    print(
        tr("summary.label.imaging"),
        _imaging_summary(imaging_result, imaging_declined),
    )
    print(tr("summary.label.integrity"), _integrity_summary(integrity_result))
    print(
        tr("summary.label.recovery_method"),
        _recovery_method_summary(
            recovery_selection_cancelled,
            recovery_declined,
            recovery_result,
        ),
    )
    print(
        tr("summary.label.photorec_session"),
        _photorec_summary(
            recovery_result,
            recovery_declined,
            recovery_selection_cancelled,
        ),
    )
    print(
        tr("summary.label.recovered_files"),
        _recovered_files_summary(
            recovery_result,
            recovery_declined,
            recovery_selection_cancelled,
        ),
    )
    print(
        tr("summary.label.recovered_size"),
        _recovered_size_summary(
            recovery_result,
            recovery_declined,
            recovery_selection_cancelled,
        ),
    )
    print(tr("summary.label.current_status"), session.status)
    print(tr("summary.label.case_location"), session.recovery_path)

    print()
    print(tr("summary.complete"))
