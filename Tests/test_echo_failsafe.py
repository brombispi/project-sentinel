import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

import modules.echo as echo
from modules.echo import (
    CODE_AUDIT_LOG_WRITE_FAILED,
    CODE_AUDIT_LOG_WRITTEN,
    log_event,
    log_info,
    read_audit_log,
    set_log_failure_handler,
)


class _FakeSession:
    def __init__(self, recovery_path):
        self.recovery_path = str(recovery_path)


class SuccessfulLoggingTests(unittest.TestCase):
    def test_successful_append_is_byte_for_byte_compatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)

            with mock.patch.object(echo, "datetime") as fake_datetime:
                fake_datetime.now.return_value.strftime.return_value = (
                    "2026-07-18 08:47:00"
                )
                log_event(session, "ARCHIVE", "INFO", "Forensic imaging completed.")
                log_event(session, "SENTINEL", "OPERATOR", "Recovery approved.")

            log_path = Path(temp_dir) / "audit.log"
            expected = (
                "2026-07-18 08:47:00 [ARCHIVE][INFO] Forensic imaging completed.\n"
                "2026-07-18 08:47:00 [SENTINEL][OPERATOR] Recovery approved.\n"
            )
            self.assertEqual(log_path.read_bytes(), expected.encode("utf-8"))

    def test_successful_log_event_returns_success_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)

            result = log_event(session, "ARCHIVE", "INFO", "ok")

            self.assertEqual(
                result,
                {"success": True, "code": CODE_AUDIT_LOG_WRITTEN},
            )

    def test_append_only_preserves_prior_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)
            log_path = Path(temp_dir) / "audit.log"
            log_path.write_text("existing line\n", encoding="utf-8")

            log_info(session, "ARGUS", "new line")

            self.assertTrue(
                log_path.read_text(encoding="utf-8").startswith("existing line\n")
            )
            self.assertIn("[ARGUS][INFO] new line", log_path.read_text(encoding="utf-8"))


class MissingParentDirectoryTests(unittest.TestCase):
    def test_missing_parent_directory_does_not_raise_and_is_not_created(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # ECHO does not own directory creation (ARCHIVE creates case dirs).
            missing_case_dir = Path(temp_dir) / "does_not_exist"
            session = _FakeSession(missing_case_dir)

            result = log_event(session, "ARCHIVE", "INFO", "event")

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], CODE_AUDIT_LOG_WRITE_FAILED)
            # ECHO must not create the missing parent directory.
            self.assertFalse(missing_case_dir.exists())


class FailSafeTests(unittest.TestCase):
    def _session(self, temp_dir):
        return _FakeSession(temp_dir)

    def test_open_failure_does_not_raise_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._session(temp_dir)

            with mock.patch("builtins.open", side_effect=OSError("open failed")):
                result = log_event(session, "ARCHIVE", "ERROR", "event")

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], CODE_AUDIT_LOG_WRITE_FAILED)
            self.assertEqual(result["recovery_path"], str(temp_dir))

    def test_write_failure_does_not_raise_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._session(temp_dir)

            fake_file = mock.MagicMock()
            fake_file.__enter__.return_value = fake_file
            fake_file.write.side_effect = OSError("write failed")

            with mock.patch("builtins.open", return_value=fake_file):
                result = log_event(session, "ARCHIVE", "ERROR", "event")

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], CODE_AUDIT_LOG_WRITE_FAILED)

    def test_flush_failure_does_not_raise_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._session(temp_dir)

            fake_file = mock.MagicMock()
            fake_file.__enter__.return_value = fake_file
            fake_file.flush.side_effect = OSError("flush failed")

            with mock.patch("builtins.open", return_value=fake_file):
                result = log_event(session, "ARCHIVE", "ERROR", "event")

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], CODE_AUDIT_LOG_WRITE_FAILED)

    def test_close_failure_does_not_raise_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._session(temp_dir)

            fake_file = mock.MagicMock()
            fake_file.__enter__.return_value = fake_file
            # A failure during context-manager exit (close) must be caught.
            fake_file.__exit__.side_effect = OSError("close failed")

            with mock.patch("builtins.open", return_value=fake_file):
                result = log_event(session, "ARCHIVE", "ERROR", "event")

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], CODE_AUDIT_LOG_WRITE_FAILED)

    def test_encoding_failure_does_not_raise_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._session(temp_dir)

            fake_file = mock.MagicMock()
            fake_file.__enter__.return_value = fake_file
            fake_file.write.side_effect = UnicodeError("encode failed")

            with mock.patch("builtins.open", return_value=fake_file):
                result = log_event(session, "ARCHIVE", "ERROR", "event")

            self.assertFalse(result["success"])
            self.assertEqual(result["code"], CODE_AUDIT_LOG_WRITE_FAILED)


class FailureHandlerTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_log_failure_handler, None)

    def test_failure_handler_is_notified_with_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)
            received = []
            set_log_failure_handler(received.append)

            with mock.patch("builtins.open", side_effect=OSError("boom")):
                result = log_event(session, "ARCHIVE", "ERROR", "event")

            self.assertEqual(len(received), 1)
            self.assertIs(received[0], result)
            self.assertFalse(received[0]["success"])

    def test_success_does_not_notify_handler(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)
            received = []
            set_log_failure_handler(received.append)

            log_event(session, "ARCHIVE", "INFO", "ok")

            self.assertEqual(received, [])

    def test_failing_handler_never_propagates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)

            def broken_handler(_result):
                raise RuntimeError("handler exploded")

            set_log_failure_handler(broken_handler)

            with mock.patch("builtins.open", side_effect=OSError("boom")):
                # Must not raise despite the broken handler.
                result = log_event(session, "ARCHIVE", "ERROR", "event")

            self.assertFalse(result["success"])


class ReadAuditLogContractTests(unittest.TestCase):
    def test_read_audit_log_reads_lines_written_by_log_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _FakeSession(temp_dir)
            log_info(session, "ARCHIVE", "one")
            log_info(session, "ARCHIVE", "two")

            lines = read_audit_log(temp_dir)

            self.assertEqual(len(lines), 2)
            self.assertIn("[ARCHIVE][INFO] one", lines[0])
            self.assertIn("[ARCHIVE][INFO] two", lines[1])

    def test_read_audit_log_missing_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(read_audit_log(temp_dir), [])


class OperatorWarningPathTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_log_failure_handler, None)

    def test_orchestrator_handler_warns_once_per_case_without_raising(self):
        # The orchestrator script has no .py extension, so load it explicitly.
        import importlib.util
        from importlib.machinery import SourceFileLoader

        sentinel_path = SOURCE_ROOT / "bin" / "sentinel"
        loader = SourceFileLoader("sentinel_cli", str(sentinel_path))
        spec = importlib.util.spec_from_loader("sentinel_cli", loader)
        sentinel = importlib.util.module_from_spec(spec)
        loader.exec_module(sentinel)

        sentinel._warned_audit_failures.clear()
        self.addCleanup(sentinel._warned_audit_failures.clear)

        failure = {
            "success": False,
            "code": CODE_AUDIT_LOG_WRITE_FAILED,
            "recovery_path": "/some/case",
            "detail": "disk full",
        }

        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            sentinel._handle_audit_log_failure(failure)
            sentinel._handle_audit_log_failure(failure)

        output = buffer.getvalue()
        # Warned exactly once for the same case despite two failures.
        self.assertEqual(output.count("audit log entry"), 1)
        # Must not claim the primary operation succeeded or failed.
        self.assertNotIn("succeeded", output.lower())
        # Must not leak the raw error detail (no traceback / internals).
        self.assertNotIn("disk full", output)
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()
