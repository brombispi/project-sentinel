def collect_case_intake():
    """
    Collect basic recovery case intake information.
    """

    print("==========================================")
    print("RECOVERY CASE INTAKE")
    print("==========================================")
    print()

    contact_name = input("Contact name: ").strip()
    contact_phone = input("Contact phone: ").strip()
    contact_email = input("Contact email: ").strip()

    print()

    recovery_request = input("Recovery request: ").strip()
    incident_description = input("Incident description: ").strip()
    previous_attempts = input("Previous recovery attempts: ").strip()
    data_priority = input("Most important data: ").strip()

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