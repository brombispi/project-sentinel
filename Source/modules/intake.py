from i18n import tr


def collect_case_intake():
    """
    Collect basic recovery case intake information.
    """

    print("==========================================")
    print(tr("intake.title"))
    print("==========================================")
    print()

    contact_name = input(tr("intake.contact_name")).strip()
    contact_phone = input(tr("intake.contact_phone")).strip()
    contact_email = input(tr("intake.contact_email")).strip()

    print()

    recovery_request = input(tr("intake.recovery_request")).strip()
    incident_description = input(tr("intake.incident_description")).strip()
    previous_attempts = input(tr("intake.previous_attempts")).strip()
    data_priority = input(tr("intake.data_priority")).strip()

    return {
        "case_contact": {
            "name": contact_name,
            "phone": contact_phone,
            "email": contact_email
        },
        "intake": {
            "recovery_request": recovery_request,
            "incident_description": incident_description,
            "previous_recovery_attempts": previous_attempts,
            "data_priority": data_priority
        }
    }
