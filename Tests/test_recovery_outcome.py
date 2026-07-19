import ast
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from core.session import RecoverySession
from core.status import RecoveryOutcome, RecoveryStatus
from modules import case_loader, session_manager
from modules.case_discovery import archive_case

SENTINEL_SOURCE = (SOURCE_ROOT / "bin" / "sentinel").read_text(encoding="utf-8")


def _extract_sentinel_function(function_name):
    module = ast.parse(SENTINEL_SOURCE)
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            segment = ast.get_source_segment(SENTINEL_SOURCE, node)
            if segment is None:
                raise ValueError(f"Could not extract {function_name}")
            return segment
    raise ValueError(f"Function {function_name} not found in sentinel")


def _load_sentinel_function(function_name, namespace):
    exec(_extract_sentinel_function(function_name), namespace)
    return namespace[function_name]


def _fake_device():
    return SimpleNamespace(
        path="/dev/testsrc",
        model="Test Model",
        serial="TESTSERIAL",
        size="1G",
        transport="USB",
        filesystem="ext4",
        role="EXTERNAL DEVICE",
    )


def _fake_assessment():
    decision = SimpleNamespace(
        status="APPROVED",
        reason="External device.",
        law=None,
        risk="LOW",
        confidence=100,
    )
    return SimpleNamespace(decision=decision)


def _session(recovery_path, **overrides):
    values = {
        "session_id": "REC-2026-000001",
        "created_at": datetime(2026, 7, 16, 10, 0, 0),
        "status": RecoveryStatus.READY_FOR_RECOVERY,
        "recovery_path": str(recovery_path),
        "case_name": "Test Case",
    }
    values.update(overrides)
    session = RecoverySession(**values)
    session.source_device = _fake_device()
    session.assessment = _fake_assessment()
    return session


def _read_manifest(recovery_path):
    return json.loads((Path(recovery_path) / "case.json").read_text(encoding="utf-8"))


def _write_manifest(recovery_path, manifest):
    (Path(recovery_path) / "case.json").write_text(
        json.dumps(manifest, indent=4) + "\n",
        encoding="utf-8",
    )


class RecoveryOutcomeEnumTests(unittest.TestCase):
    def test_enum_defines_only_the_three_allowed_values(self):
        self.assertEqual(
            [member.value for member in RecoveryOutcome],
            ["SUCCESSFUL", "PARTIAL", "UNSUCCESSFUL"],
        )

    def test_enum_members_are_strings(self):
        self.assertIsInstance(RecoveryOutcome.SUCCESSFUL, str)
        self.assertEqual(RecoveryOutcome.SUCCESSFUL.value, "SUCCESSFUL")


class ParseRecoveryOutcomeTests(unittest.TestCase):
    def setUp(self):
        namespace = {"RecoveryOutcome": RecoveryOutcome}
        self.parse = _load_sentinel_function("_parse_recovery_outcome", namespace)

    def test_valid_choices_map_to_enum(self):
        self.assertEqual(self.parse("1"), RecoveryOutcome.SUCCESSFUL)
        self.assertEqual(self.parse("2"), RecoveryOutcome.PARTIAL)
        self.assertEqual(self.parse("3"), RecoveryOutcome.UNSUCCESSFUL)
        self.assertEqual(self.parse("  2  "), RecoveryOutcome.PARTIAL)

    def test_invalid_choices_return_none(self):
        for value in ("", "0", "4", "x", "successful", "SUCCESSFUL"):
            with self.subTest(value=value):
                self.assertIsNone(self.parse(value))


class PromptRecoveryOutcomeTests(unittest.TestCase):
    def _load_prompt(self, input_mock):
        namespace = {
            "RecoveryOutcome": RecoveryOutcome,
            "tr": lambda key, **kwargs: key,
            "print": mock.Mock(),
            "input": input_mock,
        }
        _load_sentinel_function("_parse_recovery_outcome", namespace)
        prompt = _load_sentinel_function("_prompt_recovery_outcome", namespace)
        return prompt, namespace

    def test_returns_selected_outcome(self):
        input_mock = mock.Mock(side_effect=["3"])
        prompt, _ = self._load_prompt(input_mock)
        self.assertEqual(prompt(), RecoveryOutcome.UNSUCCESSFUL)

    def test_reprompts_on_invalid_input_until_valid(self):
        input_mock = mock.Mock(side_effect=["9", "x", "2"])
        prompt, namespace = self._load_prompt(input_mock)

        self.assertEqual(prompt(), RecoveryOutcome.PARTIAL)
        self.assertEqual(input_mock.call_count, 3)

        printed = [
            call.args[0] for call in namespace["print"].call_args_list if call.args
        ]
        self.assertIn("validation.invalid_selection", printed)


class OfferFinalizeRecoveryOutcomeTests(unittest.TestCase):
    def _recovery_status(self):
        return type(
            "RecoveryStatus",
            (),
            {
                "READY_FOR_RECOVERY": "READY_FOR_RECOVERY",
                "COMPLETED": "COMPLETED",
            },
        )()

    def _load_finalize(self, input_mock):
        namespace = {
            "RecoveryOutcome": RecoveryOutcome,
            "RecoveryStatus": self._recovery_status(),
            "tr": lambda key, **kwargs: key,
            "print": mock.Mock(),
            "input": input_mock,
            "log_operator": mock.Mock(),
            "update_status": mock.Mock(),
            "_run_delivery_workflow": mock.Mock(),
            "_confirmed_yes": _load_sentinel_function("_confirmed_yes", {}),
        }
        _load_sentinel_function("_parse_recovery_outcome", namespace)
        _load_sentinel_function("_prompt_recovery_outcome", namespace)
        finalize = _load_sentinel_function("_offer_finalize_recovery", namespace)
        return finalize, namespace

    def test_first_finalization_prompts_records_and_completes(self):
        input_mock = mock.Mock(side_effect=["y", "1"])
        finalize, namespace = self._load_finalize(input_mock)
        session = SimpleNamespace(
            status="READY_FOR_RECOVERY",
            source_device=object(),
            recovery_outcome=None,
        )
        assessment = object()
        intake = {"intake": {}}

        finalize(session, assessment, intake, {"success": True})

        self.assertEqual(session.recovery_outcome, "SUCCESSFUL")
        namespace["update_status"].assert_called_once_with(
            session,
            "COMPLETED",
            session.source_device,
            assessment,
            intake=intake,
        )
        namespace["log_operator"].assert_any_call(
            session,
            "SENTINEL",
            "Recovery outcome recorded: SUCCESSFUL",
        )

    def test_repeated_completion_does_not_reprompt_or_overwrite(self):
        # Only the finalize confirmation is provided; if the outcome were
        # re-prompted, input would be exhausted and raise StopIteration.
        input_mock = mock.Mock(side_effect=["y"])
        finalize, namespace = self._load_finalize(input_mock)
        session = SimpleNamespace(
            status="READY_FOR_RECOVERY",
            source_device=object(),
            recovery_outcome="PARTIAL",
        )
        assessment = object()
        intake = {"intake": {}}

        finalize(session, assessment, intake, {"success": True})

        self.assertEqual(session.recovery_outcome, "PARTIAL")
        self.assertEqual(input_mock.call_count, 1)
        namespace["update_status"].assert_called_once_with(
            session,
            "COMPLETED",
            session.source_device,
            assessment,
            intake=intake,
        )


class RecoveryOutcomePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.size_patcher = mock.patch(
            "modules.manifest.get_block_device_size_bytes",
            return_value=123456,
        )
        self.size_patcher.start()

    def tearDown(self):
        self.size_patcher.stop()

    def test_recorded_outcome_is_persisted_on_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session.recovery_outcome = RecoveryOutcome.SUCCESSFUL.value

            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )

            manifest = _read_manifest(temp_dir)
            self.assertEqual(manifest["recovery_outcome"], "SUCCESSFUL")

    def test_absent_outcome_is_not_written(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)

            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )

            manifest = _read_manifest(temp_dir)
            self.assertNotIn("recovery_outcome", manifest)

    def test_reopen_status_change_preserves_outcome(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(temp_dir)
            session.recovery_outcome = RecoveryOutcome.PARTIAL.value
            session_manager.update_status(
                session,
                RecoveryStatus.COMPLETED,
                session.source_device,
                session.assessment,
            )

            session_manager.update_status(
                session,
                RecoveryStatus.READY_FOR_RECOVERY,
                session.source_device,
                session.assessment,
            )

            self.assertEqual(session.recovery_outcome, "PARTIAL")
            manifest = _read_manifest(temp_dir)
            self.assertEqual(manifest["recovery_outcome"], "PARTIAL")

    def test_archive_preserves_outcome_on_disk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            recoveries_root = Path(temp_dir) / "Recoveries"
            case_dir = recoveries_root / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Test Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.COMPLETED,
                    "recovery_outcome": "UNSUCCESSFUL",
                },
            )
            session = RecoverySession(
                session_id="REC-2026-000001",
                created_at=datetime(2026, 7, 16, 10, 0, 0),
                status=RecoveryStatus.COMPLETED,
                recovery_path=str(case_dir),
                case_name="Test Case",
            )

            result = archive_case(session)
            self.assertTrue(result["success"])

            moved = _read_manifest(session.recovery_path)
            self.assertEqual(moved["recovery_outcome"], "UNSUCCESSFUL")


class RecoveryOutcomeLoadTests(unittest.TestCase):
    def test_legacy_case_without_outcome_loads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Legacy Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.COMPLETED,
                },
            )

            with mock.patch(
                "modules.case_loader.enumerate_all_permitted_roots",
                return_value=[{"path": Path(temp_dir).resolve()}],
            ):
                result = case_loader.load_case(case_dir, devices=[])

            self.assertTrue(result["success"])
            self.assertIsNone(result["session"].recovery_outcome)

    def test_load_hydrates_outcome_from_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "REC-2026-000001"
            case_dir.mkdir(parents=True)
            _write_manifest(
                case_dir,
                {
                    "session_id": "REC-2026-000001",
                    "case_name": "Completed Case",
                    "created_at": "2026-07-16T10:00:00",
                    "status": RecoveryStatus.COMPLETED,
                    "recovery_outcome": "SUCCESSFUL",
                },
            )

            with mock.patch(
                "modules.case_loader.enumerate_all_permitted_roots",
                return_value=[{"path": Path(temp_dir).resolve()}],
            ):
                result = case_loader.load_case(case_dir, devices=[])

            self.assertTrue(result["success"])
            self.assertEqual(result["session"].recovery_outcome, "SUCCESSFUL")


if __name__ == "__main__":
    unittest.main()
