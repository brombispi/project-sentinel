import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
TESTS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(TESTS_ROOT))

import modules.archive as archive
from i18n.translator import operator_message, set_language, tr
from modules.archive import (
    execute_photorec_recovery,
    prepare_testdisk_execution,
)

RECOVERY_UID = 999
RECOVERY_GID = 991


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


def _valid_testdisk_config(**overrides):
    config = {
        "recovery_account": "sentinel-recovery",
        "forbidden_groups": ["disk", "sudo"],
        "privilege_drop_mechanism": "setpriv",
        "execution_mode": "root",
        "working_copy_safety_margin_bytes": 0,
    }
    config.update(overrides)
    return config


def _identity(**overrides):
    identity = {
        "account": "sentinel-recovery",
        "uid": RECOVERY_UID,
        "gid": RECOVERY_GID,
        "groups": ["sentinel-recovery"],
        "group_gids": [RECOVERY_GID],
    }
    identity.update(overrides)
    return lambda name: dict(identity)


class FakeExecFs:
    def resolver(self, missing=()):
        mapping = {"setpriv": "/usr/bin/setpriv", "testdisk": "/usr/bin/testdisk"}

        def _resolve(name):
            if name in missing:
                return None
            return mapping.get(name)

        return _resolve

    def stat(self, path):
        raise FileNotFoundError(path)

    def statvfs(self, path):
        return SimpleNamespace(f_bavail=0, f_frsize=1)

    def lstat(self, path):
        raise FileNotFoundError(path)


class PhotoRecAcquisitionGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.session = SimpleNamespace(
            recovery_path=self.tmp,
            source_device=None,
        )

    @mock.patch("modules.archive.shutil.which", return_value="/usr/bin/photorec")
    @mock.patch("modules.archive.subprocess.run")
    def test_missing_image_refuses_before_subprocess(self, run_mock, which_mock):
        _make_case(self.tmp)

        result = execute_photorec_recovery(self.session)

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["code"], "PHOTOREC_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(result["display_args"], {"state": "no_acquisition"})
        run_mock.assert_not_called()

    @mock.patch("modules.archive.shutil.which", return_value="/usr/bin/photorec")
    @mock.patch("modules.archive.subprocess.run")
    def test_incomplete_acquisition_refuses_photorec(self, run_mock, which_mock):
        _make_case(self.tmp, image=True, mapfile=True)
        with mock.patch.object(
            archive,
            "classify_ddrescue_map_status",
            return_value=_map_status("incomplete", "?"),
        ):
            result = execute_photorec_recovery(self.session)

        self.assertEqual(result["code"], "PHOTOREC_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(result["display_args"], {"state": "incomplete_ddrescue"})
        run_mock.assert_not_called()

    @mock.patch("modules.archive.shutil.which", return_value="/usr/bin/photorec")
    @mock.patch("modules.archive.subprocess.run")
    def test_fingerprint_missing_refuses_photorec(self, run_mock, which_mock):
        _make_case(self.tmp, image=True, mapfile=True)
        with mock.patch.object(
            archive,
            "classify_ddrescue_map_status",
            return_value=_map_status("finished", "+"),
        ):
            result = execute_photorec_recovery(self.session)

        self.assertEqual(result["code"], "PHOTOREC_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(
            result["display_args"],
            {"state": "imaging_complete_fingerprint_missing"},
        )
        run_mock.assert_not_called()

    @mock.patch("modules.archive.shutil.which", return_value="/usr/bin/photorec")
    @mock.patch("modules.archive.subprocess.run")
    def test_inconsistent_artifacts_refuses_photorec(self, run_mock, which_mock):
        _make_case(self.tmp, image=True)

        result = execute_photorec_recovery(self.session)

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["code"], "PHOTOREC_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(
            result["display_args"],
            {"state": "inconsistent_artifacts"},
        )
        run_mock.assert_not_called()

    @mock.patch("modules.archive.shutil.which", return_value="/usr/bin/photorec")
    @mock.patch("modules.archive.subprocess.run")
    def test_invalid_map_refuses_photorec(self, run_mock, which_mock):
        _make_case(self.tmp, image=True, mapfile=True)
        with mock.patch.object(
            archive,
            "classify_ddrescue_map_status",
            return_value=_map_status("unreadable"),
        ):
            result = execute_photorec_recovery(self.session)

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["code"], "PHOTOREC_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(result["display_args"], {"state": "invalid_map"})
        run_mock.assert_not_called()

    @mock.patch("modules.archive.shutil.which", return_value="/usr/bin/photorec")
    @mock.patch("modules.archive.subprocess.run")
    def test_completed_canonical_reaches_existing_execution_path(
        self,
        run_mock,
        which_mock,
    ):
        _make_case(self.tmp, image=True, mapfile=True, sha=True)
        run_mock.return_value = SimpleNamespace(returncode=0)

        result = execute_photorec_recovery(self.session)

        run_mock.assert_called_once()
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "PHOTOREC_ENDED_NORMALLY")

    @mock.patch("modules.archive.shutil.which", return_value="/usr/bin/photorec")
    @mock.patch("modules.archive.subprocess.run")
    def test_original_path_protection_still_applies(self, run_mock, which_mock):
        _make_case(self.tmp, image=True, mapfile=True, sha=True)
        image_path = str(Path(self.tmp) / "images" / archive.IMAGE_FILENAME)
        self.session.source_device = SimpleNamespace(path=image_path)

        result = execute_photorec_recovery(self.session)

        self.assertEqual(result["code"], "PHOTOREC_REFUSED_ORIGINAL")
        run_mock.assert_not_called()


class TestDiskAcquisitionGateTests(unittest.TestCase):
    def _prepare(self, tmp, fs, *, identity_resolver=None):
        session = SimpleNamespace(recovery_path=tmp, source_device=None)
        return prepare_testdisk_execution(
            session,
            _valid_testdisk_config(),
            identity_resolver=identity_resolver or _identity(),
            command_resolver=fs.resolver(),
            geteuid=lambda: 0,
            stat_provider=fs.stat,
            statvfs_provider=fs.statvfs,
            lstat_provider=fs.lstat,
            source_environ={"TERM": "xterm-256color"},
            fs_ops=fs,
        )

    def _assert_testdisk_gate_refusal(self, result, *, state):
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["code"], "TESTDISK_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(result["display_args"], {"state": state})

    def test_missing_image_refuses_testdisk_preparation(self):
        fs = FakeExecFs()
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp)
            result = self._prepare(tmp, fs)

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["code"], "TESTDISK_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(result["display_args"], {"state": "no_acquisition"})

    def test_incomplete_acquisition_refuses_testdisk_preparation(self):
        fs = FakeExecFs()
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("incomplete", "?"),
            ):
                result = self._prepare(tmp, fs)

        self.assertEqual(result["code"], "TESTDISK_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(result["display_args"], {"state": "incomplete_ddrescue"})

    def test_fingerprint_missing_refuses_testdisk_preparation(self):
        fs = FakeExecFs()
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("finished", "+"),
            ):
                result = self._prepare(tmp, fs)

        self.assertEqual(result["code"], "TESTDISK_REFUSED_ACQUISITION_INCOMPLETE")
        self.assertEqual(
            result["display_args"],
            {"state": "imaging_complete_fingerprint_missing"},
        )

    def test_inconsistent_artifacts_refuses_testdisk_preparation(self):
        fs = FakeExecFs()
        identity_resolver = mock.Mock(
            side_effect=AssertionError(
                "TestDisk preparation must not continue beyond acquisition gate"
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, mapfile=True)
            result = self._prepare(tmp, fs, identity_resolver=identity_resolver)

        self._assert_testdisk_gate_refusal(
            result,
            state="inconsistent_artifacts",
        )
        identity_resolver.assert_not_called()

    def test_invalid_map_refuses_testdisk_preparation(self):
        fs = FakeExecFs()
        identity_resolver = mock.Mock(
            side_effect=AssertionError(
                "TestDisk preparation must not continue beyond acquisition gate"
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            _make_case(tmp, image=True, mapfile=True)
            with mock.patch.object(
                archive,
                "classify_ddrescue_map_status",
                return_value=_map_status("unreadable"),
            ):
                result = self._prepare(tmp, fs, identity_resolver=identity_resolver)

        self._assert_testdisk_gate_refusal(result, state="invalid_map")
        identity_resolver.assert_not_called()

    def test_completed_canonical_allows_testdisk_preparation(self):
        import test_testdisk_execution as exec_tests

        fs = exec_tests.FakeExecFs()
        result = exec_tests._prepare(fs)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["status"], "prepared")

    def test_canonical_symlink_protection_still_applies_after_gate(self):
        import test_testdisk_execution as exec_tests

        fs = exec_tests.FakeExecFs(canonical_is_symlink=True)
        result = exec_tests._prepare(fs)

        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_IS_SYMLINK")


class RecoveryRefusalLocalizationTests(unittest.TestCase):
    def test_photorec_refusal_messages_are_localized(self):
        result = {
            "code": "PHOTOREC_REFUSED_ACQUISITION_INCOMPLETE",
            "message": "Recovery refused: canonical acquisition is not complete.",
            "display_args": {"state": "incomplete_ddrescue"},
        }

        set_language("en", persist=False)
        self.assertEqual(
            operator_message(result, "archive"),
            tr(
                "archive.message.photorec_refused_acquisition_incomplete",
                state="incomplete_ddrescue",
            ),
        )

        set_language("de", persist=False)
        self.assertEqual(
            operator_message(result, "archive"),
            tr(
                "archive.message.photorec_refused_acquisition_incomplete",
                state="incomplete_ddrescue",
            ),
        )

    def test_testdisk_refusal_messages_are_localized(self):
        result = {
            "code": "TESTDISK_REFUSED_ACQUISITION_INCOMPLETE",
            "message": "Recovery refused: canonical acquisition is not complete.",
            "display_args": {"state": "no_acquisition"},
        }

        set_language("en", persist=False)
        self.assertIn(
            "no_acquisition",
            operator_message(result, "archive"),
        )

        set_language("de", persist=False)
        self.assertIn(
            "no_acquisition",
            operator_message(result, "archive"),
        )


if __name__ == "__main__":
    unittest.main()
