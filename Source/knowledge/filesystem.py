FILESYSTEM_KNOWLEDGE = {
    "ntfs": {
        "warning": "Do not run CHKDSK before imaging.",
        "risk": "CHKDSK may modify filesystem metadata.",
        "recommended_action": "Create an image before repair attempts."
    },
    "vfat": {
        "warning": "FAT filesystems are vulnerable to corruption after unsafe removal.",
        "risk": "Directory and allocation table damage may affect recovery.",
        "recommended_action": "Image the device before repair or write operations."
    },
    "ext4": {
        "warning": "Mounting ext4 may replay the journal and modify metadata.",
        "risk": "Filesystem metadata may change after mounting.",
        "recommended_action": "Prefer read-only access before recovery work."
    }
}
