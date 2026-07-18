"""
Characterization tests for source-identity logic:

- archive.validate_source_identity_for_resume() (resume authorization)
- case_loader._identity_matches_device() (load-time re-identification)

These two implementations are intentionally duplicated in the current code.
These tests capture CURRENT behaviour of BOTH so a future consolidation can
be verified against a known specification.
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
import modules.case_loader as case_loader

CURRENT_SIZE = 500107862016


class _FakeDevice:
    def __init__(
        self,
        path="/dev/sdb",
        serial="SERIAL123",
        model="Samsung SSD 860",
        role="EXTERNAL DEVICE",
        mount_point=None,
        filesystem="ext4",
        access_mode="READ_WRITE",
    ):
        self.path = path
        self.serial = serial
        self.model = model
        self.role = role
        self.mount_point = mount_point
        self.filesystem = filesystem
        self.access_mode = access_mode


class _FakeSession:
    def __init__(self, recovery_path, source_device):
        self.recovery_path = str(recovery_path)
        self.source_device = source_device


def _write_acquisition_source(case_dir, payload):
    evidence_dir = Path(case_dir) / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / archive.ACQUISITION_SOURCE_FILENAME).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _recorded(serial="SERIAL123", model="Samsung SSD 860", size=CURRENT_SIZE,
              path="/dev/sdb"):
    return {
        "serial": serial,
        "model": model,
        "size_bytes": size,
        "path": path,
    }


class ValidateSourceIdentityForResumeTests(unittest.TestCase):
    def _validate(self, tmp, device, current_size=CURRENT_SIZE):
        session = _FakeSession(tmp, device)
        with mock.patch.object(
            archive, "get_block_device_size_bytes", return_value=current_size
        ):
            return archive.validate_source_identity_for_resume(session)

    def test_exact_match_is_valid_without_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded())
            result = self._validate(tmp, _FakeDevice())

            self.assertTrue(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_MATCHES")
            self.assertEqual(result["warnings"], [])
            self.assertEqual(result["recorded"], _recorded())
            self.assertEqual(result["current"]["size_bytes"], CURRENT_SIZE)

    def test_missing_acquisition_source_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._validate(tmp, _FakeDevice())

            self.assertFalse(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_SOURCE_MISSING")
            self.assertIsNone(result["recorded"])

    def test_undetermined_current_size_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded())
            result = self._validate(tmp, _FakeDevice(), current_size=None)

            self.assertFalse(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_SIZE_UNDETERMINED")

    def test_size_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded(size=123))
            result = self._validate(tmp, _FakeDevice())

            self.assertFalse(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_SIZE_MISMATCH")

    def test_serial_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded(serial="OTHER"))
            result = self._validate(tmp, _FakeDevice(serial="SERIAL123"))

            self.assertFalse(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_SERIAL_MISMATCH")

    def test_serial_trusted_on_one_side_only_refuses_as_unstable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded(serial="SERIAL123"))
            result = self._validate(tmp, _FakeDevice(serial="Unknown"))

            self.assertFalse(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_SERIAL_UNSTABLE")

    def test_serial_untrusted_on_both_sides_refuses_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded(serial="Unknown"))
            result = self._validate(tmp, _FakeDevice(serial="Unknown"))

            self.assertFalse(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_SERIAL_UNAVAILABLE")

    def test_empty_serial_both_sides_refuses_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded(serial=""))
            result = self._validate(tmp, _FakeDevice(serial=""))

            self.assertFalse(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_SERIAL_UNAVAILABLE")

    def test_model_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded(model="WDC WD5000"))
            result = self._validate(tmp, _FakeDevice(model="Samsung SSD 860"))

            self.assertFalse(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_MODEL_MISMATCH")

    def test_path_change_is_valid_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_acquisition_source(tmp, _recorded(path="/dev/sdb"))
            result = self._validate(tmp, _FakeDevice(path="/dev/sdc"))

            self.assertTrue(result["valid"])
            self.assertEqual(result["code"], "IDENTITY_MATCHES")
            self.assertEqual(len(result["warnings"]), 1)
            self.assertEqual(result["warnings"][0]["code"], "IDENTITY_PATH_CHANGED")
            self.assertEqual(
                result["warnings"][0]["display_args"],
                {"recorded_path": "/dev/sdb", "current_path": "/dev/sdc"},
            )


class IdentityMatchesDeviceTests(unittest.TestCase):
    def _match(self, device, identity, current_size=CURRENT_SIZE):
        with mock.patch.object(
            case_loader, "get_block_device_size_bytes", return_value=current_size
        ):
            return case_loader._identity_matches_device(device, identity)

    def test_exact_match_returns_true(self):
        self.assertTrue(self._match(_FakeDevice(), _recorded()))

    def test_undetermined_device_size_returns_false(self):
        self.assertFalse(self._match(_FakeDevice(), _recorded(), current_size=None))

    def test_recorded_size_absent_returns_false(self):
        identity = _recorded()
        identity["size_bytes"] = None
        self.assertFalse(self._match(_FakeDevice(), identity))

    def test_size_mismatch_returns_false(self):
        self.assertFalse(self._match(_FakeDevice(), _recorded(size=999)))

    def test_serial_mismatch_returns_false(self):
        self.assertFalse(
            self._match(_FakeDevice(serial="SERIAL123"), _recorded(serial="OTHER"))
        )

    def test_serial_trusted_one_side_only_returns_false(self):
        self.assertFalse(
            self._match(_FakeDevice(serial="Unknown"), _recorded(serial="SERIAL123"))
        )

    def test_serial_untrusted_both_sides_returns_false(self):
        # Load-time matching requires a trustworthy serial on BOTH sides.
        self.assertFalse(
            self._match(_FakeDevice(serial="Unknown"), _recorded(serial="Unknown"))
        )

    def test_model_mismatch_returns_false(self):
        self.assertFalse(
            self._match(_FakeDevice(model="A"), _recorded(model="B"))
        )

    def test_path_difference_alone_still_matches(self):
        # Path is not part of identity matching (it only warns at the caller).
        self.assertTrue(
            self._match(_FakeDevice(path="/dev/sdc"), _recorded(path="/dev/sdb"))
        )

    def test_non_identity_attributes_are_ignored(self):
        # Filesystem/label/UUID/mount are not represented in identity;
        # only serial, model and exact size_bytes determine a match.
        device = _FakeDevice(
            filesystem="ntfs",
            mount_point="/mnt/elsewhere",
            access_mode="READ_ONLY",
        )
        self.assertTrue(self._match(device, _recorded()))


if __name__ == "__main__":
    unittest.main()
