import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from core.session import RecoverySession
from core.status import (
    RecoveryOperationState,
    RecoveryOperationType,
    RecoveryStatus,
)
from modules import case_loader, session_manager
from modules.case_discovery import archive_case
from modules.session_manager import RecoveryOperationError


def _fake_device():
    return SimpleNamespace(
        path="/dev/testsrc",
        model="Test Model",
        serial="TESTSERIAL",
        size="1G",
        transport="USB",
        filesystem="ext4",
        role="EXTERNAL DEVICE",
    )


def _fake_assessment():
    decision = SimpleNamespace(
        status="APPROVED",
        reason="External device.",
        risk="LOW",
        confidence=100,
    )
    return SimpleNamespace(decision=decision)


def _session(recovery_path, **overrides):
    values = {
        "session_id": "REC-2026-000001",
        "created_at": datetime(2026, 7, 16, 10, 0, 0),
        "status": RecoveryStatus.READY_FOR_RECOVERY,
        "recovery_path": str(recovery_path),
        "case_name": "Test Case",
    }
    values.update(overrides)
    session = RecoverySession(**values)
    session.source_device = _fake_device()
    session.assessment = _fake_assessment()
    return session


def _read_manifest(recovery_path):
    return json.loads(
        (Path(recovery_path) / "case.json").read_text(encoding="utf-8")
    )


def _write_manifest(recovery_path, manifest):
    (Path(recovery_path) / "case.json").write_text(
        json.dumps(manifest, indent=4) + "\n",
        encoding="utf-8",
    )


class RecoveryOperationEnumTests(unittest.TestCase):
    def test_type_defines_photorec_and_testdisk(self):
        self.assertEqual(
            [member.value for member in RecoveryOperationType],
            ["PHOTOREC", "TESTDISK"],
        )

    def test_states_are_exactly_the_four_allowed(self):
        self.assertEqual(
            [member.value for member in RecoveryOperationState],
            ["RUNNING", "COMPLETED", "FAILED", "INTERRUPTED"],
        )

    def test_enum_members_are_strings(self):
        self.assertIsInstance(RecoveryOperationState.RUNNING, str)
        self.assertEqual(RecoveryOperationType.PHOTOREC.value, "PHOTOREC")

    def test_testdisk_type_serialization_round_trips(self):
        # The stored value is the plain string persisted in case.json, and it
        # rehydrates back to the same member.
        self.assertIsInstance(RecoveryOperationType.TESTDISK, str)
        self.assertEqual(RecoveryOperationType.TESTDISK.value, "TESTDISK")
        self.assertEqual(RecoveryOperationType("TESTDISK"), RecoveryOperationType.TESTDISK)


class SessionModelTests(unittest.TestCase):
    def test_new_session_has_empty_operations_list(self):
        session = RecoverySession(
            session_id="REC-2026-000001",
            created_at=datetime(2026, 7, 16, 10, 0, 0),
            status=RecoveryStatus.NEW,
            recovery_path="/tmp/whatever",
        )
        self.assertEqual(session.recovery_operations, [])

    def test_separate_sessions_do_not_share_the_list(self):
        first = RecoverySession(
            session_id="REC-2026-000001",
            created_at=datetime(2026, 7, 16, 10, 0, 0),
            status=RecoveryStatus.NEW,
            recovery_path="/tmp/a",
        )
        second = RecoverySession(
            session_id="REC-2026-000002",
            created_at=datetime(2026, 7, 16, 10, 0, 0),
            status=RecoveryStatus.NEW,
            recovery_path="/tmp/b",
        )
        first.recovery_operations.append({"type": "PHOTOREC"})
        self.assertEqual(second.recovery_operations, [])


class AppendAndRetryTests(unittest.TestCase):
    def test_append_adds_trailing_running_entry(self):
        session = _session("/tmp/case")
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )

        self.assertEqual(len(session.recovery_operations), 1)
        entry = session.recovery_operations[0]
        self.assertEqual(entry["type"], "PHOTOREC")
        self.assertEqual(entry["state"], "RUNNING")
        self.assertIsNotNone(entry["started_at"])
        self.assertIsNone(entry["finished_at"])

    def test_only_one_running_operation_may_exist(self):
        session = _session("/tmp/case")
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        with self.assertRaises(RecoveryOperationError):
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
        self.assertEqual(len(session.recovery_operations), 1)

    def test_completed_success_resolves_trailing_entry(self):
        session = _session("/tmp/case")
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        session_manager.complete_recovery_operation(session, success=True)

        entry = session.recovery_operations[-1]
        self.assertEqual(entry["state"], "COMPLETED")
        self.assertIsNotNone(entry["finished_at"])

    def test_failed_execution_resolves_trailing_entry(self):
        session = _session("/tmp/case")
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        session_manager.complete_recovery_operation(session, success=False)

        entry = session.recovery_operations[-1]
        self.assertEqual(entry["state"], "FAILED")
        self.assertIsNotNone(entry["finished_at"])

    def test_zero_recovered_items_does_not_force_failed(self):
        # State is derived from execution success only; a normally ended
        # PhotoRec session with zero files is still COMPLETED.
        session = _session("/tmp/case")
        recovery_result = {"success": True, "recovered_file_count": 0}
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        session_manager.complete_recovery_operation(
            session, success=recovery_result["success"]
        )
        self.assertEqual(session.recovery_operations[-1]["state"], "COMPLETED")

    def test_complete_without_running_raises(self):
        session = _session("/tmp/case")
        with self.assertRaises(RecoveryOperationError):
            session_manager.complete_recovery_operation(session, success=True)

    def test_retry_appends_after_resolution_and_keeps_history(self):
        session = _session("/tmp/case")

        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        session_manager.complete_recovery_operation(session, success=False)
        first = dict(session.recovery_operations[0])

        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        session_manager.complete_recovery_operation(session, success=True)

        self.assertEqual(len(session.recovery_operations), 2)
        # Earlier terminal entry is immutable.
        self.assertEqual(session.recovery_operations[0], first)
        self.assertEqual(session.recovery_operations[0]["state"], "FAILED")
        self.assertEqual(session.recovery_operations[1]["state"], "COMPLETED")

    def test_cannot_retry_while_prior_running_unresolved(self):
        session = _session("/tmp/case")
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        with self.assertRaises(RecoveryOperationError):
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.size_patcher = mock.patch(
            "modules.manifest.get_block_device_size_bytes",
            return_value=123456,
        )
        self.size_patcher.start()

    def tearDown(self):
        self.size_patcher.stop()

    def test_operations_are_serialized_into_case_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
            session_manager.complete_recovery_operation(session, success=True)

            session_manager.save_case(session)

            manifest = _read_manifest(temp_dir)
            self.assertIn("recovery_operations", manifest)
            self.assertEqual(len(manifest["recovery_operations"]), 1)
            self.assertEqual(
                manifest["recovery_operations"][0]["state"], "COMPLETED"
            )

    def test_empty_operations_are_not_written(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session_manager.save_case(session)

            manifest = _read_manifest(temp_dir)
            self.assertNotIn("recovery_operations", manifest)

    def test_round_trip_preserves_mixed_states(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            session = _session(case_dir, status=RecoveryStatus.COMPLETED)
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
            session_manager.complete_recovery_operation(session, success=False)
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
            session_manager.complete_recovery_operation(session, success=True)
            session_manager.save_case(session)

            with mock.patch(
                "modules.case_loader.enumerate_all_permitted_roots",
                return_value=[{"path": Path(temp_dir).resolve()}],
            ):
                result = case_loader.load_case(case_dir, devices=[])

            self.assertTrue(result["success"])
            operations = result["session"].recovery_operations
            self.assertEqual([op["state"] for op in operations], ["FAILED", "COMPLETED"])

    def test_archive_preserves_operations_on_disk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            recoveries_root = Path(temp_dir) / "Recoveries"
            case_dir = recoveries_root / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Test Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.COMPLETED,
                    "recovery_operations": [
                        {
                            "type": "PHOTOREC",
                            "state": "COMPLETED",
                            "started_at": "2026-07-16T11:00:05",
                            "finished_at": "2026-07-16T11:42:31",
                        }
                    ],
                },
            )
            session = RecoverySession(
                session_id="REC-2026-000001",
                created_at=datetime(2026, 7, 16, 10, 0, 0),
                status=RecoveryStatus.COMPLETED,
                recovery_path=str(case_dir),
                case_name="Test Case",
            )

            result = archive_case(session)
            self.assertTrue(result["success"])

            moved = _read_manifest(session.recovery_path)
            self.assertEqual(len(moved["recovery_operations"]), 1)
            self.assertEqual(moved["recovery_operations"][0]["state"], "COMPLETED")


class HydrationTests(unittest.TestCase):
    def test_legacy_case_without_field_hydrates_to_empty_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Legacy Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.COMPLETED,
                },
            )

            with mock.patch(
                "modules.case_loader.enumerate_all_permitted_roots",
                return_value=[{"path": Path(temp_dir).resolve()}],
            ):
                result = case_loader.load_case(case_dir, devices=[])

            self.assertTrue(result["success"])
            self.assertEqual(result["session"].recovery_operations, [])

    def test_load_hydrates_operations_from_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.READY_FOR_RECOVERY,
                    "recovery_operations": [
                        {
                            "type": "PHOTOREC",
                            "state": "COMPLETED",
                            "started_at": "2026-07-16T11:00:05",
                            "finished_at": "2026-07-16T11:42:31",
                        }
                    ],
                },
            )

            with mock.patch(
                "modules.case_loader.enumerate_all_permitted_roots",
                return_value=[{"path": Path(temp_dir).resolve()}],
            ):
                result = case_loader.load_case(case_dir, devices=[])

            self.assertTrue(result["success"])
            operations = result["session"].recovery_operations
            self.assertEqual(len(operations), 1)
            self.assertEqual(operations[0]["type"], "PHOTOREC")


class InterruptionTests(unittest.TestCase):
    def setUp(self):
        self.size_patcher = mock.patch(
            "modules.manifest.get_block_device_size_bytes",
            return_value=123456,
        )
        self.size_patcher.start()

    def tearDown(self):
        self.size_patcher.stop()

    def test_hydration_does_not_mutate_a_running_case(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.RECOVERING,
                    "device": {
                        "path": "/dev/testsrc",
                        "model": "Test Model",
                        "serial": "TESTSERIAL",
                        "size_bytes": 123456,
                    },
                    "recovery_operations": [
                        {
                            "type": "PHOTOREC",
                            "state": "RUNNING",
                            "started_at": "2026-07-16T11:00:05",
                            "finished_at": None,
                        }
                    ],
                },
            )
            before = (case_dir / "case.json").read_text(encoding="utf-8")

            with mock.patch(
                "modules.case_loader.enumerate_all_permitted_roots",
                return_value=[{"path": Path(temp_dir).resolve()}],
            ), mock.patch(
                "modules.case_loader.get_block_device_size_bytes",
                return_value=123456,
            ):
                result = case_loader.load_case(case_dir, devices=[])

            after = (case_dir / "case.json").read_text(encoding="utf-8")
            # Read-only hydration must not rewrite the case.
            self.assertEqual(before, after)
            # The RUNNING record is hydrated as-is, not silently resolved.
            self.assertEqual(
                result["session"].recovery_operations[-1]["state"], "RUNNING"
            )

    def test_resolution_finalizes_running_to_interrupted_and_persists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(
                temp_dir, status=RecoveryStatus.RECOVERING
            )
            session.recovery_operations = [
                {
                    "type": "PHOTOREC",
                    "state": "RUNNING",
                    "started_at": "2026-07-16T11:00:05",
                    "finished_at": None,
                }
            ]

            resolved = session_manager.resolve_interrupted_recovery_operation(
                session,
                session.source_device,
                session.assessment,
            )

            self.assertTrue(resolved)
            entry = session.recovery_operations[-1]
            self.assertEqual(entry["state"], "INTERRUPTED")
            self.assertIsNotNone(entry["finished_at"])

            manifest = _read_manifest(temp_dir)
            self.assertEqual(
                manifest["recovery_operations"][-1]["state"], "INTERRUPTED"
            )

    def test_resolution_is_noop_without_running_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session.recovery_operations = [
                {
                    "type": "PHOTOREC",
                    "state": "COMPLETED",
                    "started_at": "2026-07-16T11:00:05",
                    "finished_at": "2026-07-16T11:42:31",
                }
            ]

            resolved = session_manager.resolve_interrupted_recovery_operation(
                session,
                session.source_device,
                session.assessment,
            )

            self.assertFalse(resolved)
            self.assertEqual(
                session.recovery_operations[-1]["state"], "COMPLETED"
            )

    def test_interrupted_then_retry_appends_new_record(self):
        session = _session("/tmp/case", status=RecoveryStatus.RECOVERING)
        session.recovery_operations = [
            {
                "type": "PHOTOREC",
                "state": "RUNNING",
                "started_at": "2026-07-16T11:00:05",
                "finished_at": None,
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            session.recovery_path = temp_dir
            with mock.patch(
                "modules.manifest.get_block_device_size_bytes",
                return_value=123456,
            ):
                session_manager.resolve_interrupted_recovery_operation(
                    session,
                    session.source_device,
                    session.assessment,
                )

        # After resolution, a new attempt may be appended.
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )

        states = [op["state"] for op in session.recovery_operations]
        self.assertEqual(states, ["INTERRUPTED", "RUNNING"])


class HandledFailureAndCrashTests(unittest.TestCase):
    """
    Review point 1: a handled recovery failure (ARCHIVE returns a failure
    result) finalizes the trailing RUNNING operation as FAILED and persists it
    before the workflow continues; genuine termination / unhandled crash leaves
    the operation RUNNING for the resume flow to resolve as INTERRUPTED.
    """

    def setUp(self):
        self.size_patcher = mock.patch(
            "modules.manifest.get_block_device_size_bytes",
            return_value=123456,
        )
        self.size_patcher.start()

    def tearDown(self):
        self.size_patcher.stop()

    def test_failure_result_is_finalized_as_failed_and_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
            # Mirror the workflow: ARCHIVE returned a failure result.
            recovery_result = {"success": False}
            session_manager.complete_recovery_operation(
                session, success=recovery_result["success"]
            )
            session_manager.save_case(session)

            entry = session.recovery_operations[-1]
            self.assertEqual(entry["state"], "FAILED")
            self.assertIsNotNone(entry["finished_at"])
            # Persisted before the workflow continues.
            manifest = _read_manifest(temp_dir)
            self.assertEqual(
                manifest["recovery_operations"][-1]["state"], "FAILED"
            )

    def test_unhandled_crash_leaves_running_not_failed(self):
        # An unhandled exception during execution means complete_/interrupt_ is
        # never reached; the persisted RUNNING record must remain RUNNING so the
        # existing resume flow can resolve it to INTERRUPTED (never FAILED).
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir, status=RecoveryStatus.RECOVERING)
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
            # Persist the RUNNING record exactly as update_status(RECOVERING) does.
            session_manager.save_case(session)

            # ... simulated crash: no complete/interrupt call happens here ...

            manifest = _read_manifest(temp_dir)
            self.assertEqual(
                manifest["recovery_operations"][-1]["state"], "RUNNING"
            )
            self.assertIsNone(
                manifest["recovery_operations"][-1]["finished_at"]
            )

            # The resume flow later resolves it to INTERRUPTED, not FAILED.
            session_manager.resolve_interrupted_recovery_operation(
                session, session.source_device, session.assessment
            )
            self.assertEqual(
                session.recovery_operations[-1]["state"], "INTERRUPTED"
            )


class CompletedAtOrderingTests(unittest.TestCase):
    """
    Review point 2: completed_at must never be earlier than the finished_at of
    the final recovery operation, including after reopen + rework + refinalize.
    """

    def setUp(self):
        self.size_patcher = mock.patch(
            "modules.manifest.get_block_device_size_bytes",
            return_value=123456,
        )
        self.size_patcher.start()

    def tearDown(self):
        self.size_patcher.stop()

    def _assert_completed_at_not_before_final_op(self, session):
        final_finished = session.recovery_operations[-1]["finished_at"]
        self.assertIsNotNone(session.completed_at)
        self.assertGreaterEqual(
            datetime.fromisoformat(session.completed_at),
            datetime.fromisoformat(final_finished),
        )

    def test_single_pass_completed_at_not_before_final_op(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
            session_manager.complete_recovery_operation(session, success=True)
            session_manager.update_status(
                session,
                RecoveryStatus.READY_FOR_RECOVERY,
                session.source_device,
                session.assessment,
            )
            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )
            self._assert_completed_at_not_before_final_op(session)

    def test_reopen_rework_refinalize_restamps_completed_at(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)

            # First completion.
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
            session_manager.complete_recovery_operation(session, success=True)
            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )
            first_completed_at = session.completed_at

            # Reopen to an active status (completed_at preserved while reopened).
            session_manager.update_status(
                session,
                RecoveryStatus.READY_FOR_RECOVERY,
                session.source_device,
                session.assessment,
            )
            self.assertEqual(session.completed_at, first_completed_at)

            # Rework: a later recovery operation.
            session_manager.append_running_recovery_operation(
                session, RecoveryOperationType.PHOTOREC.value
            )
            session.recovery_operations[-1]["started_at"] = "2026-07-16T14:00:00"
            session_manager.complete_recovery_operation(session, success=True)
            session.recovery_operations[-1]["finished_at"] = "2026-07-16T14:30:00"

            # Refinalize: completed_at is re-stamped to now (>= final finished_at).
            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )

            self.assertNotEqual(session.completed_at, first_completed_at)
            self._assert_completed_at_not_before_final_op(session)

    def test_idempotent_completed_transition_does_not_restamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )
            first = session.completed_at
            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )
            self.assertEqual(session.completed_at, first)


class InvariantEnforcementTests(unittest.TestCase):
    def test_terminal_entries_are_immutable_across_new_attempt(self):
        session = _session("/tmp/case")
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        session_manager.complete_recovery_operation(session, success=True)
        snapshot = dict(session.recovery_operations[0])

        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        session_manager.complete_recovery_operation(session, success=False)

        self.assertEqual(session.recovery_operations[0], snapshot)

    def test_has_active_running_operation_reflects_trailing_state(self):
        session = _session("/tmp/case")
        self.assertFalse(
            session_manager.has_active_running_operation(session)
        )
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.PHOTOREC.value
        )
        self.assertTrue(
            session_manager.has_active_running_operation(session)
        )
        session_manager.complete_recovery_operation(session, success=True)
        self.assertFalse(
            session_manager.has_active_running_operation(session)
        )

    def test_unknown_operation_type_is_rejected(self):
        session = _session("/tmp/case")
        with self.assertRaises(ValueError):
            session_manager.append_running_recovery_operation(
                session, "DDRESCUE"
            )

    def test_testdisk_operation_type_is_accepted(self):
        session = _session("/tmp/case")
        session_manager.append_running_recovery_operation(
            session, RecoveryOperationType.TESTDISK.value
        )
        entry = session.recovery_operations[-1]
        self.assertEqual(entry["type"], "TESTDISK")
        self.assertEqual(entry["state"], "RUNNING")


if __name__ == "__main__":
    unittest.main()
