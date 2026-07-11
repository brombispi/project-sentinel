class Device:
    def __init__(
        self,
        name,
        model,
        serial,
        size,
        transport,
        role,
        protected,
        mounted,
        filesystem,
        access_mode,
        mount_point=None,
    ):
        self.name = name
        self.path = f"/dev/{name}"
        self.model = model
        self.serial = serial
        self.size = size
        self.transport = transport
        self.role = role
        self.protected = protected
        self.mounted = mounted
        self.filesystem = filesystem
        self.access_mode = access_mode
        self.mount_point = mount_point

    def is_protected(self):
        return self.protected

    def is_external(self):
        return self.role == "EXTERNAL DEVICE"

    def is_mounted(self):
        return self.mounted

    def is_read_only(self):
        return self.access_mode == "READ_ONLY"

    def is_read_write(self):
        return self.access_mode == "READ_WRITE"
