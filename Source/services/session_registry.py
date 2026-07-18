import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class SessionRegistryError(Exception):
    """Raised when an existing session registry cannot be read or validated."""


class SessionRegistry:

    def __init__(self, registry_path=None):
        if registry_path is None:
            project_root = Path(__file__).resolve().parent.parent
            registry_path = project_root / "state" / "session_registry.json"

        self.registry_path = Path(registry_path)

    @staticmethod
    def _default_registry():
        return {
            "year": datetime.now().year,
            "last_number": 0,
        }

    @staticmethod
    def _validate(registry):
        if not isinstance(registry, dict):
            raise SessionRegistryError(
                "session_registry.json must contain a JSON object."
            )

        year = registry.get("year")
        if not isinstance(year, int) or isinstance(year, bool):
            raise SessionRegistryError(
                "session_registry.json is missing a valid integer 'year'."
            )

        last_number = registry.get("last_number")
        if not isinstance(last_number, int) or isinstance(last_number, bool):
            raise SessionRegistryError(
                "session_registry.json is missing a valid integer 'last_number'."
            )

    def load(self):
        """
        Load the registry, self-initializing an in-memory default when the
        registry file has never been created.

        A missing registry file (fresh installation) returns a default and
        is not an error. An existing but unreadable or malformed registry
        raises SessionRegistryError and is never silently reset, because
        resetting could reuse previously allocated case numbers.
        """

        if not self.registry_path.exists():
            return self._default_registry()

        try:
            content = self.registry_path.read_text(encoding="utf-8")
        except OSError as error:
            raise SessionRegistryError(
                f"session_registry.json could not be read: {self.registry_path}"
            ) from error

        try:
            registry = json.loads(content)
        except json.JSONDecodeError as error:
            raise SessionRegistryError(
                f"session_registry.json is malformed: {self.registry_path}"
            ) from error

        self._validate(registry)

        return registry

    @contextmanager
    def _exclusive_lock(self):
        """
        Hold an exclusive advisory lock for the duration of a registry
        read-modify-write, so two Sentinel processes cannot allocate the
        same case number or corrupt the registry.

        Uses a dedicated sibling lock file and fcntl.flock(LOCK_EX). The
        acquisition blocks in the kernel (no busy-loop) until the lock is
        free. The kernel releases the lock automatically when this file
        descriptor is closed or the holding process exits, so there is no
        on-disk lock state that can become stale.
        """

        lock_path = self.registry_path.with_name(
            f"{self.registry_path.name}.lock"
        )

        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(lock_path, "a", encoding="utf-8")
        except OSError as error:
            raise SessionRegistryError(
                f"session registry lock could not be created: {lock_path}"
            ) from error

        try:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except OSError as error:
                raise SessionRegistryError(
                    f"session registry lock could not be acquired: {lock_path}"
                ) from error

            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lock_file.close()

    def next_session_id(self):
        with self._exclusive_lock():
            registry = self.load()

            year = registry["year"]
            number = registry["last_number"] + 1

            registry["last_number"] = number
            self.save(registry)

        return f"REC-{year}-{number:06d}"

    def save(self, registry):
        """
        Persist the registry atomically.

        Writes to a temporary file in the registry directory, flushes and
        fsyncs it, then replaces the live registry with os.replace(). The
        live registry is never written in place, so a failed write leaves
        the previous live registry intact.
        """

        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.registry_path.with_name(
            f"{self.registry_path.name}.tmp"
        )

        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(registry, file, indent=4)
                file.flush()
                os.fsync(file.fileno())

            os.replace(temp_path, self.registry_path)
        except OSError:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise
