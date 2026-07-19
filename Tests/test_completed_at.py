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
from core.status import RecoveryStatus
from modules import case_loader, manifest, session_manager
from modules.case_discovery import archive_case


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
        law=None,
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
    return json.loads((Path(recovery_path) / "case.json").read_text(encoding="utf-8"))


def _write_manifest(recovery_path, manifest):
    (Path(recovery_path) / "case.json").write_text(
        json.dumps(manifest, indent=4) + "\n",
        encoding="utf-8",
    )


class CompletedAtPersistenceTests(unittest.TestCase):
    def setUp(self):
        # write_case_manifest queries exact block-device size; the real call
        # shells out to `blockdev`, which is irrelevant to this fact and not
        # present on every platform. Pin it so tests are deterministic.
        self.size_patcher = mock.patch(
            "modules.manifest.get_block_device_size_bytes",
            return_value=123456,
        )
        self.size_patcher.start()

    def tearDown(self):
        self.size_patcher.stop()

    def test_first_completed_transition_records_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            self.assertIsNone(session.completed_at)

            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )

            self.assertIsNotNone(session.completed_at)
            # Recorded with the same ISO 8601 representation as created_at.
            self.assertEqual(
                datetime.fromisoformat(session.completed_at).isoformat(),
                session.completed_at,
            )

            manifest = _read_manifest(temp_dir)
            self.assertEqual(manifest["completed_at"], session.completed_at)
            self.assertEqual(manifest["status"], RecoveryStatus.COMPLETED)

    def test_subsequent_persistence_preserves_completed_at(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)

            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )
            first_value = session.completed_at

            # A repeated COMPLETED transition must not overwrite the fact.
            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )
            self.assertEqual(session.completed_at, first_value)

            # A plain re-save must also preserve it.
            session_manager.save_case(session)

            manifest = _read_manifest(temp_dir)
            self.assertEqual(manifest["completed_at"], first_value)

    def test_non_completed_transition_does_not_create_field(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)

            session_manager.update_status(
                session,
                RecoveryStatus.READY_FOR_IMAGING,
                session.source_device,
                session.assessment,
            )

            self.assertIsNone(session.completed_at)
            manifest = _read_manifest(temp_dir)
            self.assertNotIn("completed_at", manifest)

    def test_reopen_status_change_preserves_completed_at(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )
            recorded = session.completed_at

            # Simulate a reopen resuming the case to a working status.
            session_manager.update_status(
                session,
                RecoveryStatus.READY_FOR_RECOVERY,
                session.source_device,
                session.assessment,
            )

            self.assertEqual(session.completed_at, recorded)
            manifest = _read_manifest(temp_dir)
            self.assertEqual(manifest["completed_at"], recorded)
            self.assertEqual(manifest["status"], RecoveryStatus.READY_FOR_RECOVERY)

    def test_archive_preserves_completed_at_on_disk(self):
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
                    "completed_at": "2026-07-16T11:02:55",
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

            moved_manifest = _read_manifest(session.recovery_path)
            self.assertEqual(moved_manifest["completed_at"], "2026-07-16T11:02:55")


class CompletedAtLoadTests(unittest.TestCase):
    def test_legacy_case_without_completed_at_loads(self):
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
            self.assertIsNone(result["session"].completed_at)

    def test_load_hydrates_completed_at_from_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Completed Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.COMPLETED,
                    "completed_at": "2026-07-16T11:02:55",
                },
            )

            with mock.patch(
                "modules.case_loader.enumerate_all_permitted_roots",
                return_value=[{"path": Path(temp_dir).resolve()}],
            ):
                result = case_loader.load_case(case_dir, devices=[])

            self.assertTrue(result["success"])
            self.assertEqual(
                result["session"].completed_at,
                "2026-07-16T11:02:55",
            )


class GoverningLawPersistenceTests(unittest.TestCase):
    """SL-004: the governing Sentinel Law is a permanent part of the audit
    trail. It must be written into the manifest exactly as AEGIS produced it
    and restored on load, while legacy manifests without the field stay valid."""

    def setUp(self):
        self.size_patcher = mock.patch(
            "modules.manifest.get_block_device_size_bytes",
            return_value=123456,
        )
        self.size_patcher.start()

    def tearDown(self):
        self.size_patcher.stop()

    def test_manifest_records_governing_law(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session.assessment.decision.status = "STOP"
            session.assessment.decision.reason = (
                "Source device identity cannot be trusted."
            )
            session.assessment.decision.law = "SL-003"

            manifest.write_case_manifest(
                session,
                session.source_device,
                session.assessment,
            )

            data = _read_manifest(temp_dir)
            self.assertEqual(data["assessment"]["law"], "SL-003")

    def test_manifest_records_null_law_for_approved_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)  # APPROVED, no governing law.

            manifest.write_case_manifest(
                session,
                session.source_device,
                session.assessment,
            )

            data = _read_manifest(temp_dir)
            self.assertIn("law", data["assessment"])
            self.assertIsNone(data["assessment"]["law"])

    def _load(self, case_dir, temp_dir):
        with mock.patch(
            "modules.case_loader.enumerate_all_permitted_roots",
            return_value=[{"path": Path(temp_dir).resolve()}],
        ):
            return case_loader.load_case(case_dir, devices=[])

    def test_load_restores_governing_law(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Law Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.COMPLETED,
                    "assessment": {
                        "decision": "STOP",
                        "reason": "Source device identity cannot be trusted.",
                        "law": "SL-003",
                        "risk": "CRITICAL",
                        "confidence": 100,
                    },
                },
            )

            result = self._load(case_dir, temp_dir)

            self.assertTrue(result["success"])
            self.assertEqual(result["assessment"].decision.law, "SL-003")

    def test_legacy_manifest_without_law_loads_with_none(self):
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
                    "assessment": {
                        "decision": "APPROVED",
                        "reason": "External device.",
                        "risk": "LOW",
                        "confidence": 100,
                    },
                },
            )

            result = self._load(case_dir, temp_dir)

            self.assertTrue(result["success"])
            self.assertIsNone(result["assessment"].decision.law)


if __name__ == "__main__":
    unittest.main()
