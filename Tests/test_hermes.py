import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from core.session import RecoverySession
from modules.hermes import Hermes, TECHNICIAN_REPORT_FILENAME
from modules.report_formatter import ReportFormatter

TECHNICIAN_REPORT_KEYS = (
    "Case ID",
    "Case Name",
    "Status",
    "Created At",
)


def _session(**overrides):
    values = {
        "session_id": "REC-2026-000001",
        "created_at": datetime(2026, 7, 16, 10, 0, 0),
        "status": "active",
        "recovery_path": "/tmp/recovery",
        "case_name": "Test Case",
    }
    values.update(overrides)
    return RecoverySession(**values)


class HermesTests(unittest.TestCase):
    def test_build_technician_report_returns_display_ready_keys_in_order(self):
        hermes = Hermes(_session())

        report = hermes.build_technician_report()

        self.assertEqual(list(report.keys()), list(TECHNICIAN_REPORT_KEYS))

    def test_build_technician_report_empty_values_become_none(self):
        hermes = Hermes(
            _session(
                session_id="",
                case_name="",
                status="",
                created_at=None,
            )
        )

        report = hermes.build_technician_report()

        self.assertIsNone(report["Case ID"])
        self.assertIsNone(report["Case Name"])
        self.assertIsNone(report["Status"])
        self.assertIsNone(report["Created At"])

    def test_build_report_technician_dispatches_to_technician_report(self):
        hermes = Hermes(_session())

        self.assertEqual(
            hermes.build_report("technician"),
            hermes.build_technician_report(),
        )

    def test_build_report_unsupported_type_raises_value_error(self):
        hermes = Hermes(_session())

        with self.assertRaises(ValueError):
            hermes.build_report("unknown")

    def test_build_technician_markdown_delegates_to_report_formatter(self):
        hermes = Hermes(_session())
        report = hermes.build_technician_report()
        real_formatter = ReportFormatter()

        with mock.patch(
            "modules.hermes.ReportFormatter",
            return_value=real_formatter,
        ) as formatter_cls:
            result = hermes.build_technician_markdown()

        formatter_cls.assert_called_once_with()
        expected = real_formatter.format_markdown("Technician Report", report)
        self.assertEqual(result, expected)

    def test_save_technician_report_writes_markdown_and_returns_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            hermes = Hermes(_session(recovery_path=temp_dir))

            report_path = hermes.save_technician_report()

            expected_path = Path(temp_dir) / "reports" / TECHNICIAN_REPORT_FILENAME
            self.assertEqual(report_path, expected_path)
            self.assertTrue(report_path.is_file())
            self.assertEqual(
                report_path.read_text(encoding="utf-8"),
                hermes.build_technician_markdown(),
            )

    def test_save_technician_report_creates_reports_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            hermes = Hermes(_session(recovery_path=temp_dir))

            hermes.save_technician_report()

            self.assertTrue((Path(temp_dir) / "reports").is_dir())

    def test_save_technician_report_raises_when_file_already_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            hermes = Hermes(_session(recovery_path=temp_dir))
            hermes.save_technician_report()

            with self.assertRaises(FileExistsError):
                hermes.save_technician_report()


if __name__ == "__main__":
    unittest.main()
