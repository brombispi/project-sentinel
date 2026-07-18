import sys
import tempfile
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from modules.argus import (
    SMART_EVIDENCE_FILENAME,
    SmartEvidenceError,
    read_smart_evidence,
)

SMART_EVIDENCE_HEALTHY = (
    "smartctl 7.3 2022-02-28 r5338 [x86_64-linux] (local build)\n"
    "=== START OF READ SMART DATA SECTION ===\n"
    "SMART overall-health self-assessment test result: PASSED\n"
)

SMART_EVIDENCE_FAILED = (
    "=== START OF READ SMART DATA SECTION ===\n"
    "SMART overall-health self-assessment test result: FAILED\n"
)

SMART_EVIDENCE_NO_HEALTH = (
    "smartctl 7.3 2022-02-28 r5338 [x86_64-linux] (local build)\n"
    "Device Model:     WDC WD10EZEX-00BN5A0\n"
    "Serial Number:    WD-WCC3F0000000\n"
)


def _write_smart_evidence(case_dir, content, *, encoding="utf-8"):
    evidence_dir = case_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / SMART_EVIDENCE_FILENAME

    if isinstance(content, bytes):
        evidence_path.write_bytes(content)
    else:
        evidence_path.write_text(content, encoding=encoding)

    return evidence_path


class ReadSmartEvidenceTests(unittest.TestCase):
    def test_returns_parsed_health_for_valid_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_smart_evidence(case_dir, SMART_EVIDENCE_HEALTHY)

            result = read_smart_evidence(case_dir)

            self.assertEqual(
                result,
                {
                    "present": True,
                    "available": True,
                    "relative_path": f"evidence/{SMART_EVIDENCE_FILENAME}",
                    "overall_health": "PASSED",
                },
            )

    def test_returns_failed_health_for_valid_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_smart_evidence(case_dir, SMART_EVIDENCE_FAILED)

            result = read_smart_evidence(case_dir)

            self.assertTrue(result["available"])
            self.assertEqual(result["overall_health"], "FAILED")

    def test_available_with_unknown_health(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_smart_evidence(case_dir, SMART_EVIDENCE_NO_HEALTH)

            result = read_smart_evidence(case_dir)

            self.assertTrue(result["present"])
            self.assertTrue(result["available"])
            self.assertIsNone(result["overall_health"])

    def test_unavailable_marker_reports_not_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_smart_evidence(case_dir, "smartctl is not installed.\n")

            result = read_smart_evidence(case_dir)

            self.assertTrue(result["present"])
            self.assertFalse(result["available"])
            self.assertIsNone(result["overall_health"])

    def test_returns_none_when_evidence_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)

            self.assertIsNone(read_smart_evidence(case_dir))

    def test_empty_evidence_raises_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_smart_evidence(case_dir, "   \n\t\n")

            with self.assertRaises(SmartEvidenceError):
                read_smart_evidence(case_dir)

    def test_invalid_utf8_raises_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_smart_evidence(case_dir, b"\xff\xfe invalid smart bytes")

            with self.assertRaises(SmartEvidenceError):
                read_smart_evidence(case_dir)

    def test_non_regular_evidence_path_raises_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            evidence_dir = case_dir / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            # A directory at the evidence path exists but is not a regular
            # file, so it must be reported as an error, not as missing.
            (evidence_dir / SMART_EVIDENCE_FILENAME).mkdir()

            with self.assertRaises(SmartEvidenceError):
                read_smart_evidence(case_dir)


if __name__ == "__main__":
    unittest.main()
