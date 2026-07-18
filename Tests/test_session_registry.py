import fcntl
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

import services.session_registry as sr_module
from services.session_registry import SessionRegistry, SessionRegistryError

TEMP_SUFFIX = ".tmp"
LOCK_SUFFIX = ".lock"


def _registry_path(base_dir):
    return Path(base_dir) / "state" / "session_registry.json"


def _temp_path(registry_path):
    return registry_path.with_name(registry_path.name + TEMP_SUFFIX)


def _lock_path(registry_path):
    return registry_path.with_name(registry_path.name + LOCK_SUFFIX)


def _allocate_worker(registry_path_str, allocations, out_queue):
    """
    Module-level worker so it is importable under the 'spawn' start method.

    Allocates `allocations` case IDs in a fresh process and returns them.
    """

    registry = SessionRegistry(Path(registry_path_str))
    ids = [registry.next_session_id() for _ in range(allocations)]
    out_queue.put(ids)


class BootstrapTests(unittest.TestCase):
    def test_load_returns_default_when_state_directory_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            self.assertFalse(registry_path.parent.exists())

            registry = SessionRegistry(registry_path).load()

            self.assertEqual(
                registry,
                {"year": datetime.now().year, "last_number": 0},
            )
            # load() must not create the state directory or file.
            self.assertFalse(registry_path.parent.exists())
            self.assertFalse(registry_path.exists())

    def test_load_returns_default_when_registry_file_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry_path.parent.mkdir(parents=True)

            registry = SessionRegistry(registry_path).load()

            self.assertEqual(
                registry,
                {"year": datetime.now().year, "last_number": 0},
            )
            self.assertFalse(registry_path.exists())

    def test_first_allocation_from_fresh_installation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            self.assertFalse(registry_path.parent.exists())

            session_id = SessionRegistry(registry_path).next_session_id()

            year = datetime.now().year
            self.assertEqual(session_id, f"REC-{year}-000001")
            self.assertTrue(registry_path.is_file())
            self.assertEqual(
                json.loads(registry_path.read_text(encoding="utf-8")),
                {"year": year, "last_number": 1},
            )


class ExistingRegistryTests(unittest.TestCase):
    def test_existing_valid_registry_loads_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry_path.parent.mkdir(parents=True)
            payload = {"year": 2026, "last_number": 12}
            registry_path.write_text(
                json.dumps(payload, indent=4), encoding="utf-8"
            )

            registry = SessionRegistry(registry_path).load()

            self.assertEqual(registry, payload)

    def test_existing_registry_incremented_normally(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(
                json.dumps({"year": 2020, "last_number": 41}, indent=4),
                encoding="utf-8",
            )

            session_id = SessionRegistry(registry_path).next_session_id()

            self.assertEqual(session_id, "REC-2020-000042")
            self.assertEqual(
                json.loads(registry_path.read_text(encoding="utf-8")),
                {"year": 2020, "last_number": 42},
            )


class AtomicPersistenceTests(unittest.TestCase):
    def test_save_uses_temporary_file_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry = SessionRegistry(registry_path)

            with mock.patch.object(
                sr_module.os,
                "replace",
                wraps=os.replace,
            ) as replace_mock:
                registry.save({"year": 2026, "last_number": 5})

            replace_mock.assert_called_once()
            source_arg, dest_arg = replace_mock.call_args[0]
            self.assertTrue(str(source_arg).endswith(TEMP_SUFFIX))
            self.assertEqual(Path(dest_arg), registry_path)

            self.assertFalse(_temp_path(registry_path).exists())
            self.assertEqual(
                json.loads(registry_path.read_text(encoding="utf-8")),
                {"year": 2026, "last_number": 5},
            )

    def test_failed_write_preserves_previous_live_registry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry = SessionRegistry(registry_path)
            registry.save({"year": 2026, "last_number": 7})
            original_bytes = registry_path.read_bytes()

            with mock.patch.object(
                sr_module.os,
                "replace",
                side_effect=OSError("simulated replace failure"),
            ):
                with self.assertRaises(OSError):
                    registry.save({"year": 2026, "last_number": 8})

            # The live registry must be untouched by the failed write.
            self.assertEqual(registry_path.read_bytes(), original_bytes)
            # The temporary file must not be left behind.
            self.assertFalse(_temp_path(registry_path).exists())


class MalformedRegistryTests(unittest.TestCase):
    def test_malformed_json_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text("{ not valid json", encoding="utf-8")
            registry = SessionRegistry(registry_path)

            with self.assertRaises(SessionRegistryError):
                registry.load()

            with self.assertRaises(SessionRegistryError):
                registry.next_session_id()

            # The corrupt file must remain untouched (no reset, no reuse).
            self.assertEqual(
                registry_path.read_text(encoding="utf-8"),
                "{ not valid json",
            )

    def test_structurally_invalid_registry_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(
                json.dumps({"unexpected": "shape"}), encoding="utf-8"
            )
            registry = SessionRegistry(registry_path)

            with self.assertRaises(SessionRegistryError):
                registry.load()


class SingleWriterGuardTests(unittest.TestCase):
    def test_next_session_id_acquires_and_releases_exclusive_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry = SessionRegistry(registry_path)

            flock_calls = []
            real_flock = fcntl.flock

            def _record_flock(fd, operation):
                flock_calls.append(operation)
                return real_flock(fd, operation)

            with mock.patch.object(
                sr_module.fcntl, "flock", side_effect=_record_flock
            ):
                registry.next_session_id()

            self.assertEqual(
                flock_calls,
                [fcntl.LOCK_EX, fcntl.LOCK_UN],
            )

    def test_concurrent_holder_blocks_allocation_until_released(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry_path.parent.mkdir(parents=True)
            registry = SessionRegistry(registry_path)

            # Hold the exclusive lock from an independent file descriptor,
            # mimicking a second Sentinel process.
            holder = open(_lock_path(registry_path), "a", encoding="utf-8")
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)

            completed = threading.Event()
            result = {}

            def _allocate():
                result["session_id"] = registry.next_session_id()
                completed.set()

            worker = threading.Thread(target=_allocate)
            worker.start()
            try:
                # While the lock is held, allocation must not proceed.
                self.assertFalse(completed.wait(timeout=0.5))

                # Releasing the lock must let the blocked allocation finish.
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
                holder.close()

                self.assertTrue(completed.wait(timeout=5))
            finally:
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            year = datetime.now().year
            self.assertEqual(result["session_id"], f"REC-{year}-000001")

    def test_lock_failure_raises_session_registry_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(
                json.dumps({"year": 2026, "last_number": 4}, indent=4),
                encoding="utf-8",
            )
            original_bytes = registry_path.read_bytes()
            registry = SessionRegistry(registry_path)

            with mock.patch.object(
                sr_module.fcntl,
                "flock",
                side_effect=OSError("simulated flock failure"),
            ):
                with self.assertRaises(SessionRegistryError):
                    registry.next_session_id()

            # An explicit lock failure must not mutate the registry.
            self.assertEqual(registry_path.read_bytes(), original_bytes)

    def test_two_processes_never_allocate_the_same_case_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = _registry_path(temp_dir)
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(
                json.dumps({"year": 2026, "last_number": 0}, indent=4),
                encoding="utf-8",
            )

            allocations_per_process = 25
            context = multiprocessing.get_context("spawn")
            queue = context.Queue()

            processes = [
                context.Process(
                    target=_allocate_worker,
                    args=(str(registry_path), allocations_per_process, queue),
                )
                for _ in range(2)
            ]

            for process in processes:
                process.start()

            collected = [
                queue.get(timeout=30) for _ in range(len(processes))
            ]

            for process in processes:
                process.join(timeout=30)
                self.assertEqual(process.exitcode, 0)

            all_ids = [session_id for batch in collected for session_id in batch]
            total = len(processes) * allocations_per_process

            self.assertEqual(len(all_ids), total)
            self.assertEqual(len(set(all_ids)), total)
            self.assertEqual(
                json.loads(registry_path.read_text(encoding="utf-8")),
                {"year": 2026, "last_number": total},
            )


if __name__ == "__main__":
    unittest.main()
