"""
Characterization tests for the execute_forensic_image() decision matrix.

These exercise decision routing only. The ddrescue subprocess is always
mocked, so no imaging is ever performed. Refusal paths additionally assert
that ddrescue is never invoked.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

import modules.archive as archive

CURRENT_SIZE = 500107862016


class _FakeDevice:
    def __init__(self, path="/dev/sdb", serial="SERIAL123", model="Samsung SSD 860"):
        self.path = path
        self.serial = serial
        self.model = model
        self.role = "EXTERNAL DEVICE"


class _FakeSession:
    def __init__(self, recovery_path, source_device):
        self.recovery_path = str(recovery_path)
        self.source_device = source_device


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


def _write_acquisition_source(case_dir, *, serial="SERIAL123",
                              model="Samsung SSD 860", size=CURRENT_SIZE,
                              path="/dev/sdb"):
    evidence_dir = Path(case_dir) / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / archive.ACQUISITION_SOURCE_FILENAME).write_text(
        json.dumps(
            {"serial": serial, "model": model, "size_bytes": size, "path": path},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class RefusalRoutingTests(unittest.TestCase):
    """Every refusal returns without invoking ddrescue."""

    def _run(self, tmp, *, resume, map_status=None, no_mounts=True):
        session = _FakeSession(tmp, _FakeDevice())
        run_mock = mock.MagicMock(name="subprocess.run")
        patches = [
            mock.patch.object(archive.shutil, "which", return_value="/usr/bin/ddrescue"),
            mock.patch.object(archive.subprocess, "run", run_mock),
            mock.patch.object(
                archive,
                "observe_source_mounted_descendants",
                return_value=[] if no_mounts else [
                    {"device_path": "/dev/sdb1", "mount_target": "/mnt/x"}
                ],
            ),
        ]
        if map_status is not None:
            patches.append(
                mock.patch.object(
                    archive, "classify_ddrescue_map_status", return_value=map_status
                )
            )

        with patches[0], patches[1], patches[2]:
            if map_status is not None:
                with patches[3]:
                    result = archive.execute_forensic_image(session, resume=resume)
            else:
                result = archive.execute_forensic_image(session, resume=resume)
        return result, run_mock

    def test_ddrescue_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            session = _FakeSession(tmp, _FakeDevice())
            run_mock = mock.MagicMock()
            with mock.patch.object(archive.shutil, "which", return_value=None), \
                    mock.patch.object(archive.subprocess, "run", run_mock):
                result = archive.execute_forensic_image(session, resume=False)

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], "IMAGING_DDRESCUE_NOT_INSTALLED")
            run_mock.assert_not_called()

    def test_resume_refused_when_completed_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True, sha=True)
            result, run_mock = self._run(tmp, resume=True)
            self.assertEqual(result["code"], "IMAGING_RESUME_REFUSED_CANONICAL")
            run_mock.assert_not_called()

    def test_resume_refused_when_imaging_complete_fingerprint_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            result, run_mock = self._run(
                tmp, resume=True, map_status=_map_status("finished", "+")
            )
            self.assertEqual(result["code"], "IMAGING_RESUME_REFUSED_COMPLETE")
            run_mock.assert_not_called()

    def test_resume_refused_when_map_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            result, run_mock = self._run(
                tmp, resume=True, map_status=_map_status("unreadable")
            )
            self.assertEqual(result["code"], "IMAGING_RESUME_REFUSED_MAP_UNREADABLE")
            run_mock.assert_not_called()

    def test_resume_refused_when_inconsistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True)
            result, run_mock = self._run(tmp, resume=True)
            self.assertEqual(result["code"], "IMAGING_RESUME_REFUSED_INCONSISTENT")
            run_mock.assert_not_called()

    def test_resume_refused_generic_state_when_no_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            result, run_mock = self._run(tmp, resume=True)
            self.assertEqual(result["code"], "IMAGING_RESUME_REFUSED_STATE")
            self.assertEqual(result["display_args"], {"state": "no_acquisition"})
            run_mock.assert_not_called()

    def test_new_refused_when_completed_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True, sha=True)
            result, run_mock = self._run(tmp, resume=False)
            self.assertEqual(result["code"], "IMAGING_REFUSED_CANONICAL")
            run_mock.assert_not_called()

    def test_new_refused_when_map_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            result, run_mock = self._run(
                tmp, resume=False, map_status=_map_status("unreadable")
            )
            self.assertEqual(result["code"], "IMAGING_REFUSED_MAP_UNREADABLE")
            run_mock.assert_not_called()

    def test_new_refused_when_inconsistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, mapfile=True)
            result, run_mock = self._run(tmp, resume=False)
            self.assertEqual(result["code"], "IMAGING_REFUSED_INCONSISTENT")
            run_mock.assert_not_called()

    def test_new_refused_generic_state_when_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            result, run_mock = self._run(
                tmp, resume=False, map_status=_map_status("incomplete", "?")
            )
            self.assertEqual(result["code"], "IMAGING_REFUSED_STATE")
            self.assertEqual(result["display_args"], {"state": "incomplete_ddrescue"})
            run_mock.assert_not_called()

    def test_new_refused_generic_state_when_fingerprint_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            result, run_mock = self._run(
                tmp, resume=False, map_status=_map_status("finished", "+")
            )
            self.assertEqual(result["code"], "IMAGING_REFUSED_STATE")
            self.assertEqual(
                result["display_args"],
                {"state": "imaging_complete_fingerprint_missing"},
            )
            run_mock.assert_not_called()

    def test_resume_refused_on_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            _write_acquisition_source(tmp, size=123)  # mismatch vs current size
            session = _FakeSession(tmp, _FakeDevice())
            run_mock = mock.MagicMock()
            with mock.patch.object(archive.shutil, "which", return_value="/x"), \
                    mock.patch.object(archive.subprocess, "run", run_mock), \
                    mock.patch.object(
                        archive, "classify_ddrescue_map_status",
                        return_value=_map_status("incomplete", "?")), \
                    mock.patch.object(
                        archive, "get_block_device_size_bytes",
                        return_value=CURRENT_SIZE), \
                    mock.patch.object(
                        archive, "observe_source_mounted_descendants",
                        return_value=[]):
                result = archive.execute_forensic_image(session, resume=True)

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], "IDENTITY_SIZE_MISMATCH")
            run_mock.assert_not_called()

    def test_resume_refused_when_mounted_descendants_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            _write_acquisition_source(tmp)
            session = _FakeSession(tmp, _FakeDevice())
            run_mock = mock.MagicMock()
            with mock.patch.object(archive.shutil, "which", return_value="/x"), \
                    mock.patch.object(archive.subprocess, "run", run_mock), \
                    mock.patch.object(
                        archive, "classify_ddrescue_map_status",
                        return_value=_map_status("incomplete", "?")), \
                    mock.patch.object(
                        archive, "get_block_device_size_bytes",
                        return_value=CURRENT_SIZE), \
                    mock.patch.object(
                        archive, "observe_source_mounted_descendants",
                        return_value=[{"device_path": "/dev/sdb1",
                                       "mount_target": "/mnt/x"}]):
                result = archive.execute_forensic_image(session, resume=True)

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], "IMAGING_REFUSED_MOUNTED_DESCENDANTS")
            run_mock.assert_not_called()

    def test_new_refused_when_mounted_descendants_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            result, run_mock = self._run(tmp, resume=False, no_mounts=False)
            self.assertEqual(result["code"], "IMAGING_REFUSED_MOUNTED_DESCENDANTS")
            run_mock.assert_not_called()


class ProceedRoutingTests(unittest.TestCase):
    """Guarded paths that reach a (mocked) ddrescue invocation."""

    def test_new_imaging_reaches_ddrescue_and_records_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            session = _FakeSession(tmp, _FakeDevice())
            run_mock = mock.MagicMock()
            run_mock.return_value.returncode = 0
            with mock.patch.object(archive.shutil, "which", return_value="/x"), \
                    mock.patch.object(archive.subprocess, "run", run_mock), \
                    mock.patch.object(
                        archive, "observe_source_mounted_descendants",
                        return_value=[]), \
                    mock.patch.object(
                        archive, "get_block_device_size_bytes",
                        return_value=CURRENT_SIZE), \
                    mock.patch.object(
                        archive, "get_logical_sector_size", return_value=512), \
                    mock.patch.object(
                        archive, "get_physical_sector_size", return_value=512):
                result = archive.execute_forensic_image(session, resume=False)

            self.assertTrue(result["success"])
            self.assertEqual(result["code"], "IMAGING_CREATED_SUCCESS")

            paths = archive._artifact_paths(tmp)
            run_mock.assert_called_once()
            argv = run_mock.call_args[0][0]
            self.assertEqual(
                argv,
                [
                    "ddrescue", "-f", "-n", "/dev/sdb",
                    str(paths["image_path"]), str(paths["map_path"]),
                ],
            )
            # acquisition_source.json is recorded before the first run.
            self.assertTrue(paths["acquisition_source_path"].is_file())

    def test_resume_imaging_reaches_ddrescue(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            _write_acquisition_source(tmp)
            session = _FakeSession(tmp, _FakeDevice())
            run_mock = mock.MagicMock()
            run_mock.return_value.returncode = 0
            with mock.patch.object(archive.shutil, "which", return_value="/x"), \
                    mock.patch.object(archive.subprocess, "run", run_mock), \
                    mock.patch.object(
                        archive, "classify_ddrescue_map_status",
                        return_value=_map_status("incomplete", "?")), \
                    mock.patch.object(
                        archive, "get_block_device_size_bytes",
                        return_value=CURRENT_SIZE), \
                    mock.patch.object(
                        archive, "observe_source_mounted_descendants",
                        return_value=[]):
                result = archive.execute_forensic_image(session, resume=True)

            self.assertTrue(result["success"])
            self.assertEqual(result["code"], "IMAGING_RESUMED_SUCCESS")
            run_mock.assert_called_once()

    def test_new_imaging_ddrescue_nonzero_exit_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            session = _FakeSession(tmp, _FakeDevice())
            run_mock = mock.MagicMock()
            run_mock.return_value.returncode = 1
            with mock.patch.object(archive.shutil, "which", return_value="/x"), \
                    mock.patch.object(archive.subprocess, "run", run_mock), \
                    mock.patch.object(
                        archive, "observe_source_mounted_descendants",
                        return_value=[]), \
                    mock.patch.object(
                        archive, "get_block_device_size_bytes",
                        return_value=CURRENT_SIZE), \
                    mock.patch.object(
                        archive, "get_logical_sector_size", return_value=512), \
                    mock.patch.object(
                        archive, "get_physical_sector_size", return_value=512):
                result = archive.execute_forensic_image(session, resume=False)

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], "IMAGING_DDRESCUE_EXIT")
            self.assertEqual(result["display_args"], {"exit_code": 1})

    def test_new_imaging_keyboard_interrupt_is_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            session = _FakeSession(tmp, _FakeDevice())
            with mock.patch.object(archive.shutil, "which", return_value="/x"), \
                    mock.patch.object(
                        archive.subprocess, "run", side_effect=KeyboardInterrupt), \
                    mock.patch.object(
                        archive, "observe_source_mounted_descendants",
                        return_value=[]), \
                    mock.patch.object(
                        archive, "get_block_device_size_bytes",
                        return_value=CURRENT_SIZE), \
                    mock.patch.object(
                        archive, "get_logical_sector_size", return_value=512), \
                    mock.patch.object(
                        archive, "get_physical_sector_size", return_value=512):
                result = archive.execute_forensic_image(session, resume=False)

            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["code"], "IMAGING_INTERRUPTED")
            self.assertTrue(result["interrupted"])


if __name__ == "__main__":
    unittest.main()
