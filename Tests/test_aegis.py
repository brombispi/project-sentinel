import sys
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from core.device import Device
from modules.aegis import RULES, evaluate


def _make_device(*, protected, mounted, serial="TESTSERIAL", filesystem="unknown"):
    return Device(
        name="test",
        model="Test Model",
        serial=serial,
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

    def test_mounted_source_device_is_stopped_under_sl_008(self):
        assessment = evaluate(_make_device(protected=False, mounted=True))
        decision = assessment.decision

        self.assertEqual(decision.status, "STOP")
        self.assertEqual(decision.law, "SL-008")
        self.assertEqual(decision.risk, "CRITICAL")
        self.assertEqual(decision.reason, "Source device is currently mounted.")
        self.assertFalse(assessment.is_approved())
        self.assertEqual(
            assessment.recommendations[0],
            "Unmount the source device before continuing.",
        )


class UnidentifiedDeviceTests(unittest.TestCase):
    UNTRUSTWORTHY_SERIALS = (
        "",
        "   ",
        "Unknown",
        "UNKNOWN",
        "unknown",
        "N/A",
        "n/a",
    )

    def test_untrustworthy_serials_are_stopped_under_sl_003(self):
        for serial in self.UNTRUSTWORTHY_SERIALS:
            with self.subTest(serial=serial):
                assessment = evaluate(
                    _make_device(protected=False, mounted=False, serial=serial)
                )
                decision = assessment.decision

                self.assertEqual(decision.status, "STOP")
                self.assertEqual(decision.law, "SL-003")
                self.assertEqual(decision.risk, "CRITICAL")
                self.assertEqual(
                    decision.reason,
                    "Source device identity cannot be trusted.",
                )
                self.assertFalse(assessment.is_approved())
                self.assertEqual(
                    assessment.recommendations[0],
                    "Verify the physical source device and obtain a trustworthy "
                    "serial before continuing.",
                )

    def test_trustworthy_serial_remains_approved(self):
        assessment = evaluate(
            _make_device(
                protected=False,
                mounted=False,
                serial="S4EWNF0M803123A",
            )
        )
        decision = assessment.decision

        self.assertEqual(decision.status, "APPROVED")
        self.assertIsNone(decision.law)
        self.assertTrue(assessment.is_approved())


class RulePriorityTests(unittest.TestCase):
    def test_sl_001_takes_priority_over_sl_003(self):
        assessment = evaluate(
            _make_device(protected=True, mounted=False, serial="Unknown")
        )

        self.assertEqual(assessment.decision.law, "SL-001")

    def test_sl_008_takes_priority_over_sl_003(self):
        assessment = evaluate(
            _make_device(protected=False, mounted=True, serial="Unknown")
        )

        self.assertEqual(assessment.decision.law, "SL-008")

    def test_rule_ordering(self):
        self.assertEqual(
            [rule.__name__ for rule in RULES],
            [
                "_rule_protect_recovery_engine",
                "_rule_source_must_be_unmounted",
                "_rule_device_must_be_identified",
            ],
        )


if __name__ == "__main__":
    unittest.main()
