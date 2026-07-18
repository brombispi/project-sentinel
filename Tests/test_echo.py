import sys
import tempfile
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from modules.echo import read_audit_log

AUDIT_LOG_LINES = (
    "2026-07-16 10:00:00 [ARCHIVE][INFO] Forensic imaging completed.",
    "2026-07-16 10:05:00 [ARCHIVE][INFO] Forensic image fingerprint recorded.",
    "2026-07-16 10:10:00 [SENTINEL][OPERATOR] Recovery finalization approved.",
)


def _write_audit_log(case_dir, lines=()):
    log_path = case_dir / "audit.log"
    log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return log_path


class ReadAuditLogTests(unittest.TestCase):
    def test_read_audit_log_returns_populated_lines_in_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_audit_log(case_dir, AUDIT_LOG_LINES)

            result = read_audit_log(case_dir)

            self.assertEqual(result, list(AUDIT_LOG_LINES))

    def test_read_audit_log_returns_empty_list_for_empty_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)
            _write_audit_log(case_dir)

            result = read_audit_log(case_dir)

            self.assertEqual(result, [])

    def test_read_audit_log_returns_empty_list_when_file_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir)

            result = read_audit_log(case_dir)

            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
