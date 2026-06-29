RECOVERY_TOOLS = [
    {
        "id": "ddrescue",
        "name": "GNU ddrescue",
        "type": "imaging",
        "installed": False,
    },
    {
        "id": "hddsuperclone",
        "name": "HDDSuperClone",
        "type": "imaging",
        "installed": False,
    },
    {
        "id": "photorec",
        "name": "PhotoRec",
        "type": "logical",
        "installed": False,
    },
    {
        "id": "testdisk",
        "name": "TestDisk",
        "type": "filesystem",
        "installed": False,
    },
]

import shutil

def list_recovery_tools():
    """
    Print all available recovery tools.
    """

    print("\nRecovery Tools")
    print("=" * 40)

    for index, tool in enumerate(RECOVERY_TOOLS, start=1):
        installed = is_tool_installed(tool["id"])
        status = "Installed" if installed else "Not installed"

        print(f"[{index}] {tool['name']}")
        print(f"    Type   : {tool['type']}")
        print(f"    Status : {status}")
        print()

def select_recovery_tool():
    """
    Let the technician choose a recovery tool.
    """

    list_recovery_tools()

    while True:
        try:
            choice = int(input("Select recovery tool: "))

            if 1 <= choice <= len(RECOVERY_TOOLS):
                return RECOVERY_TOOLS[choice - 1]

            print("Invalid selection.")

        except ValueError:
            print("Please enter a number.")

def is_tool_installed(tool_id):
    """
    Check whether a recovery tool is installed.
    """

    return shutil.which(tool_id) is not None