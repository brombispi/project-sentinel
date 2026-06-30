def print_device_selection_list(title, devices):
    """
    Print a compact device selection list.
    """

    print(title)
    print("=" * 40)

    for index, device in enumerate(devices, start=1):
        print(f"[{index}] {device.model} ({device.size})")
        print(f"    Path : {device.path}")
        print(f"    Role : {device.role}")
        print("-" * 40)

    print()

def select_source_device(devices):
    """
    Allow the technician to select one device.
    """

    while True:
        try:
            selection = int(input(f"Select device [1-{len(devices)}]: "))

            if 1 <= selection <= len(devices):
                return devices[selection - 1]

            print("Invalid selection.")

        except ValueError:
            print("Please enter a number.")

def select_destination_device(devices):
    """
    Allow the technician to select a destination device.
    """

    while True:
        try:
            selection = int(input(f"Select destination device [1-{len(devices)}]: "))

            if 1 <= selection <= len(devices):
                return devices[selection - 1]

            print("Invalid selection.")

        except ValueError:
            print("Please enter a number.")