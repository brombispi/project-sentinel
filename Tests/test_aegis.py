import sys
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from core.device import Device
from modules.aegis import evaluate


def _make_device(*, protected, mounted, filesystem="unknown"):
    return Device(
        name="test",
        model="Test Model",
        serial="TESTSERIAL",
        size="1TB",
        transport="usb",
        role="RECOVERY ENGINE" if protected else "EXTERNAL DEVICE",
        protected=protected,
        mounted=mounted,
        filesystem=filesystem,
        access_mode="READ_WRITE",
        mount_point="/mnt/test" if mounted else None,
    )


class ProtectedEngineTests(unittest.TestCase):
    def test_protected_engine_is_stopped_under_sl_001(self):
        assessment = evaluate(_make_device(protected=True, mounted=False))
        decision = assessment.decision

        self.assertEqual(decision.status, "STOP")
        self.assertEqual(decision.law, "SL-001")
        self.assertEqual(decision.risk, "CRITICAL")
        self.assertEqual(decision.reason, "Target is the Recovery Engine.")
        self.assertFalse(assessment.is_approved())
        self.assertEqual(
            assessment.recommendations[0],
            "Select an external customer storage device.",
        )


class ExternalDeviceTests(unittest.TestCase):
    def test_unmounted_external_device_is_approved(self):
        assessment = evaluate(_make_device(protected=False, mounted=False))
        decision = assessment.decision

        self.assertEqual(decision.status, "APPROVED")
        self.assertEqual(decision.risk, "LOW")
        self.assertIsNone(decision.law)
        self.assertTrue(assessment.is_approved())

    def test_mounted_external_device_is_approved_with_warning(self):
        assessment = evaluate(_make_device(protected=False, mounted=True))

        self.assertTrue(assessment.is_approved())
        self.assertTrue(assessment.has_warnings())
        self.assertTrue(
            any("mounted" in warning.lower() for warning in assessment.warnings)
        )


if __name__ == "__main__":
    unittest.main()
