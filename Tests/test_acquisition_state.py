"""
Characterization tests for acquisition-state classification and resume-status
resolution. These lock in CURRENT behaviour to make future refactoring safe.
They do not assert intended-but-unimplemented behaviour.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

import modules.archive as archive
import modules.case_loader as case_loader
from core.status import RecoveryStatus


def _make_case(base_dir, *, image=False, mapfile=False, sha=False):
    case_dir = Path(base_dir)
    (case_dir / "images").mkdir(parents=True, exist_ok=True)
    (case_dir / "evidence").mkdir(parents=True, exist_ok=True)

    if image:
        (case_dir / "images" / archive.IMAGE_FILENAME).write_bytes(b"img")
    if mapfile:
        (case_dir / "images" / archive.MAP_FILENAME).write_bytes(b"map")
    if sha:
        (case_dir / "evidence" / archive.SHA256_FILENAME).write_text(
            "algorithm=SHA-256\n", encoding="utf-8"
        )

    return case_dir


def _map_status(status, current_status=None):
    return {
        "status": status,
        "current_status": current_status,
        "validation": {"valid": status != "unreadable", "exit_code": 0},
    }


class ClassifyAcquisitionStateTests(unittest.TestCase):
    def test_no_artifacts_is_no_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            result = archive.classify_acquisition_state(tmp)

            self.assertEqual(result["state"], "no_acquisition")
            self.assertEqual(result["code"], "ACQUISITION_NO_ARTIFACTS")
            self.assertFalse(result["image_exists"])
            self.assertFalse(result["map_exists"])
            self.assertFalse(result["sha256_exists"])
            self.assertIsNone(result["map_status"])

    def test_image_without_map_is_inconsistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True)
            result = archive.classify_acquisition_state(tmp)

            self.assertEqual(result["state"], "inconsistent_artifacts")
            self.assertEqual(result["code"], "ACQUISITION_INCONSISTENT")
            self.assertEqual(
                result["display_args"],
                {"present": archive.IMAGE_FILENAME, "missing": archive.MAP_FILENAME},
            )

    def test_map_without_image_is_inconsistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, mapfile=True)
            result = archive.classify_acquisition_state(tmp)

            self.assertEqual(result["state"], "inconsistent_artifacts")
            self.assertEqual(
                result["display_args"],
                {"present": archive.MAP_FILENAME, "missing": archive.IMAGE_FILENAME},
            )

    def test_image_map_and_sha_is_completed_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True, sha=True)
            # sha presence short-circuits before map classification.
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                side_effect=AssertionError("map must not be classified"),
            ):
                result = archive.classify_acquisition_state(tmp)

            self.assertEqual(result["state"], "completed_canonical")
            self.assertEqual(result["code"], "ACQUISITION_COMPLETED_CANONICAL")
            self.assertTrue(result["sha256_exists"])

    def test_image_map_no_sha_unreadable_map_is_invalid_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("unreadable"),
            ):
                result = archive.classify_acquisition_state(tmp)

            self.assertEqual(result["state"], "invalid_map")
            self.assertEqual(result["code"], "ACQUISITION_INVALID_MAP")
            self.assertEqual(result["map_status"], "unreadable")

    def test_image_map_no_sha_finished_map_is_fingerprint_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("finished", "+"),
            ):
                result = archive.classify_acquisition_state(tmp)

            self.assertEqual(result["state"], "imaging_complete_fingerprint_missing")
            self.assertEqual(result["code"], "ACQUISITION_FINGERPRINT_MISSING")
            self.assertEqual(result["current_status"], "+")

    def test_image_map_no_sha_incomplete_map_is_incomplete_ddrescue(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("incomplete", "?"),
            ):
                result = archive.classify_acquisition_state(tmp)

            self.assertEqual(result["state"], "incomplete_ddrescue")
            self.assertEqual(result["code"], "ACQUISITION_INCOMPLETE_DDRESCUE")
            self.assertEqual(result["current_status"], "?")


class _FakeSession:
    def __init__(self, recovery_path):
        self.recovery_path = str(recovery_path)


class ResolveResumeStatusTests(unittest.TestCase):
    def test_completed_canonical_resolves_to_ready_for_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True, sha=True)
            status = case_loader.resolve_resume_status(_FakeSession(tmp))
            self.assertEqual(status, RecoveryStatus.READY_FOR_RECOVERY)

    def test_no_acquisition_resolves_to_ready_for_imaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            status = case_loader.resolve_resume_status(_FakeSession(tmp))
            self.assertEqual(status, RecoveryStatus.READY_FOR_IMAGING)

    def test_incomplete_ddrescue_resolves_to_ready_for_imaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("incomplete", "?"),
            ):
                status = case_loader.resolve_resume_status(_FakeSession(tmp))
            self.assertEqual(status, RecoveryStatus.READY_FOR_IMAGING)

    def test_fingerprint_missing_resolves_to_ready_for_imaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("finished", "+"),
            ):
                status = case_loader.resolve_resume_status(_FakeSession(tmp))
            self.assertEqual(status, RecoveryStatus.READY_FOR_IMAGING)

    def test_invalid_map_resolves_to_ready_for_imaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("unreadable"),
            ):
                status = case_loader.resolve_resume_status(_FakeSession(tmp))
            self.assertEqual(status, RecoveryStatus.READY_FOR_IMAGING)

    def test_inconsistent_artifacts_resolves_to_ready_for_imaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True)
            status = case_loader.resolve_resume_status(_FakeSession(tmp))
            self.assertEqual(status, RecoveryStatus.READY_FOR_IMAGING)

    def test_unknown_state_falls_back_to_assessing(self):
        # Characterizes the documented fallback branch that current
        # classify_acquisition_state never actually returns.
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            with mock.patch.object(
                case_loader,
                "classify_acquisition_state",
                return_value={"state": "an_unmodeled_state"},
            ):
                status = case_loader.resolve_resume_status(_FakeSession(tmp))
            self.assertEqual(status, RecoveryStatus.ASSESSING)


if __name__ == "__main__":
    unittest.main()
