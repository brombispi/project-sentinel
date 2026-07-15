from i18n import tr


def print_device_selection_list(title, devices):
    """
    Print a compact device selection list.
    """

    print(title)
    print("=" * 40)

    for index, device in enumerate(devices, start=1):
        print(f"[{index}] {device.model} ({device.size})")
        print(f"{tr('device.label.path')} {device.path}")
        print(f"{tr('device.label.role')} {device.role}")
        print("-" * 40)

    print()


def select_source_device(devices):
    """
    Allow the technician to select one device.
    """

    device_range = f"1-{len(devices)}"

    while True:
        try:
            selection = int(
                input(tr("device.prompt.select", range=device_range))
            )

            if 1 <= selection <= len(devices):
                return devices[selection - 1]

            print(tr("validation.invalid_selection"))

        except ValueError:
            print(tr("validation.enter_number"))


def select_destination_device(devices):
    """
    Allow the technician to select a destination device.
    """

    device_range = f"1-{len(devices)}"

    while True:
        try:
            selection = int(
                input(
                    tr(
                        "device.prompt.select_destination",
                        range=device_range,
                    )
                )
            )

            if 1 <= selection <= len(devices):
                return devices[selection - 1]

            print(tr("validation.invalid_selection"))

        except ValueError:
            print(tr("validation.enter_number"))
