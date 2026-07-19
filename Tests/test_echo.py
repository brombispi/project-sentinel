import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from modules.echo import log_event, read_audit_log

AUDIT_LOG_LINES = (
    "2026-07-16 10:00:00 [ARCHIVE][INFO] Forensic imaging completed.",
    "2026-07-16 10:05:00 [ARCHIVE][INFO] Forensic image fingerprint recorded.",
    "2026-07-16 10:10:00 [SENTINEL][OPERATOR] Recovery finalization approved.",
)

# ISO 8601, second precision, explicit +00:00 UTC offset, then the unchanged
# "[MODULE][LEVEL] event" structure.
UTC_TIMESTAMP_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00) "
    r"\[(?P<module>[^\]]+)\]\[(?P<level>[^\]]+)\] (?P<event>.*)$"
)


class _FakeSession:
    def __init__(self, recovery_path):
        self.recovery_path = str(recovery_path)


def _write_audit_log(case_dir, lines=()):
    log_path = case_dir / "audit.log"
    log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return log_path


class UtcTimestampFormatTests(unittest.TestCase):
    """SL-004 audit quality: ECHO writes timezone-aware UTC ISO 8601
    timestamps. These tests parse the generated value rather than comparing to
    the wall clock, so they are independent of the current time."""

    def _emit_and_read_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)
            log_event(session, "ARCHIVE", "INFO", "Forensic imaging completed.")
            lines = read_audit_log(temp_dir)
        self.assertEqual(len(lines), 1)
        return lines[0]

    def _timestamp_token(self, line):
        # The ISO 8601 timestamp contains no space, so the first field is it.
        return line.split(" ", 1)[0]

    def test_line_matches_iso8601_utc_structure(self):
        line = self._emit_and_read_line()
        self.assertRegex(line, UTC_TIMESTAMP_LINE)

    def test_timestamp_includes_explicit_utc_offset(self):
        token = self._timestamp_token(self._emit_and_read_line())
        self.assertTrue(token.endswith("+00:00"))

    def test_timestamp_uses_second_precision(self):
        token = self._timestamp_token(self._emit_and_read_line())
        # No fractional-second component at second precision.
        self.assertNotIn(".", token)
        parsed = datetime.fromisoformat(token)
        self.assertEqual(parsed.microsecond, 0)

    def test_timestamp_parses_as_timezone_aware(self):
        token = self._timestamp_token(self._emit_and_read_line())
        parsed = datetime.fromisoformat(token)
        self.assertIsNotNone(parsed.tzinfo)

    def test_parsed_utc_offset_is_zero(self):
        token = self._timestamp_token(self._emit_and_read_line())
        parsed = datetime.fromisoformat(token)
        self.assertEqual(parsed.utcoffset(), timedelta(0))

    def test_line_structure_after_timestamp_unchanged(self):
        line = self._emit_and_read_line()
        match = UTC_TIMESTAMP_LINE.match(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.group("module"), "ARCHIVE")
        self.assertEqual(match.group("level"), "INFO")
        self.assertEqual(match.group("event"), "Forensic imaging completed.")

    def test_enriched_pipe_delimited_event_preserves_utc_line_structure(self):
        # The enriched AEGIS event uses " | field=value" segments; ECHO must
        # still produce a valid UTC line and keep the event text verbatim.
        enriched = (
            "Decision: STOP | law=SL-003 | risk=CRITICAL | confidence=100 | "
            "reason=Source device identity cannot be trusted."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)
            log_event(session, "AEGIS", "WARNING", enriched)
            lines = read_audit_log(temp_dir)

        self.assertEqual(len(lines), 1)
        match = UTC_TIMESTAMP_LINE.match(lines[0])
        self.assertIsNotNone(match)
        self.assertEqual(match.group("module"), "AEGIS")
        self.assertEqual(match.group("level"), "WARNING")
        self.assertEqual(match.group("event"), enriched)

    def test_append_preserves_prior_content_with_utc_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)
            log_path = Path(temp_dir) / "audit.log"
            log_path.write_text("existing line\n", encoding="utf-8")

            log_event(session, "ARGUS", "INFO", "new line")

            content = log_path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("existing line\n"))
            new_line = content.splitlines()[-1]
            self.assertRegex(new_line, UTC_TIMESTAMP_LINE)
            self.assertTrue(new_line.endswith("[ARGUS][INFO] new line"))


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
