"""
Focused wiring tests for the root-mode TestDisk production integration in
bin/sentinel.

The two wiring functions (_run_recovery_method_selection routing and
_run_testdisk_recovery) are loaded in isolation via the same AST-exec harness
used by test_acquisition_workflow, so every collaborator is injected
explicitly and no production module is imported for the wiring itself. tr() is
stubbed to return its key so assertions capture exact translation keys.

These lock the operator-facing contract that matters most for safety:
preparation, configuration, and every confirmation complete BEFORE any
recovery-operation lifecycle mutation, and a failure/decline never appends an
operation, never changes status, and launches nothing.
"""

import ast
import contextlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

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


def _confirmed_yes(response):
    # Mirror the real predicate (bin/sentinel:_confirmed_yes).
    return response.strip().lower() in ("y", "j")


class _RecoveryStatus:
    RECOVERING = "RECOVERING"
    READY_FOR_RECOVERY = "READY_FOR_RECOVERY"


class _RecoveryOperationType:
    TESTDISK = types.SimpleNamespace(value="TESTDISK")
    PHOTOREC = types.SimpleNamespace(value="PHOTOREC")


SOURCE_DEVICE = types.SimpleNamespace(path="/dev/sdb")
ASSESSMENT = types.SimpleNamespace(tag="assessment")
INTAKE = {"intake": {}}


def _valid_config():
    return {
        "recovery_account": "sentinel-recovery",
        "forbidden_groups": ["disk", "sudo"],
        "privilege_drop_mechanism": "setpriv",
        "execution_mode": "root",
        "working_copy_safety_margin_bytes": 0,
    }


def _prepared(working_image):
    return {
        "success": True,
        "status": "prepared",
        "code": "TESTDISK_PREPARED",
        "message": "TestDisk root-mode execution prepared.",
        "working_image_path": str(working_image),
        "recovered_directory": "x",
        "argv": ["/usr/bin/setpriv"],
        "env": {"PATH": "/usr/bin"},
        "cwd": "x",
    }


def _exec_result(*, success, code):
    return {
        "success": success,
        "status": "ended" if success else "failed",
        "code": code,
        "message": f"TestDisk {code}",
        "artifacts": [],
        "recovered_directory_count": 1,
        "recovered_file_count": 2,
        "recovered_total_bytes": 3,
    }


class _TestdiskWiringHarness(unittest.TestCase):
    def _session(self, recovery_path):
        return types.SimpleNamespace(
            recovery_path=str(recovery_path),
            source_device=SOURCE_DEVICE,
            status="READY_FOR_RECOVERY",
            recovery_operations=[],
        )

    def _namespace(self, manager, *, os_module):
        namespace = {
            "Path": Path,
            "os": os_module,
            "tr": lambda key, **kwargs: key,
            "operator_message": lambda result, owner: "OPMSG",
            "_confirmed_yes": _confirmed_yes,
            "format_bytes": lambda n: f"{n}B",
            "PROJECT_ROOT": Path("/project"),
            "RecoveryStatus": _RecoveryStatus,
            "RecoveryOperationType": _RecoveryOperationType,
            "print": manager.print,
            "input": manager.input,
            "log_operator": manager.log_operator,
            "log_error": manager.log_error,
            "log_info": manager.log_info,
            "read_testdisk_config": manager.read_testdisk_config,
            "prepare_testdisk_execution": manager.prepare_testdisk_execution,
            "execute_testdisk_recovery": manager.execute_testdisk_recovery,
            "append_running_recovery_operation": (
                manager.append_running_recovery_operation
            ),
            "update_status": manager.update_status,
            "complete_recovery_operation": manager.complete_recovery_operation,
        }
        exec(_extract_sentinel_function("_testdisk_declined"), namespace)
        exec(_extract_sentinel_function("_run_testdisk_recovery"), namespace)
        return namespace

    def _run(self, *, inputs, config_return, prepare_return=None,
             exec_return=None, os_module=None, create=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for relative in create:
            target = root / relative
            if relative.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x")

        manager = mock.Mock()
        manager.input.side_effect = list(inputs)
        manager.read_testdisk_config.return_value = config_return
        manager.prepare_testdisk_execution.return_value = (
            prepare_return
            if prepare_return is not None
            else _prepared(root / "working" / "testdisk.img")
        )
        manager.execute_testdisk_recovery.return_value = (
            exec_return
            if exec_return is not None
            else _exec_result(success=True, code="TESTDISK_ENDED_NORMALLY")
        )

        import os as real_os

        namespace = self._namespace(manager, os_module=os_module or real_os)
        session = self._session(root)
        run = namespace["_run_testdisk_recovery"]
        result = run(session, ASSESSMENT, INTAKE)
        return result, manager, session

    def _assert_no_lifecycle(self, manager):
        manager.append_running_recovery_operation.assert_not_called()
        manager.complete_recovery_operation.assert_not_called()
        for status_call in manager.update_status.call_args_list:
            self.assertNotIn(
                _RecoveryStatus.RECOVERING, status_call.args
            )

    def _assert_not_launched(self, manager):
        manager.execute_testdisk_recovery.assert_not_called()


class ConfigurationHandlingTests(_TestdiskWiringHarness):
    def test_not_configured_returns_declined_without_lifecycle(self):
        result, manager, _ = self._run(inputs=[], config_return=None)
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_not_called()
        self._assert_no_lifecycle(manager)
        self._assert_not_launched(manager)
        manager.log_error.assert_called()

    def test_config_failure_shows_message_without_lifecycle(self):
        result, manager, _ = self._run(
            inputs=[],
            config_return={
                "success": False,
                "code": "TESTDISK_CONFIG_MALFORMED",
                "message": "bad json",
            },
        )
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_not_called()
        self._assert_no_lifecycle(manager)
        manager.print.assert_any_call("OPMSG")


class PreparationBeforeLifecycleTests(_TestdiskWiringHarness):
    def test_preparation_failure_leaves_status_and_operations_unchanged(self):
        result, manager, session = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
            prepare_return={
                "success": False,
                "code": "TESTDISK_REQUIRES_ROOT",
                "message": "root required",
            },
        )
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_called_once()
        self._assert_no_lifecycle(manager)
        self._assert_not_launched(manager)
        self.assertEqual(session.recovery_operations, [])
        manager.print.assert_any_call("OPMSG")

    def test_unprivileged_root_mode_aborts_before_lifecycle(self):
        # geteuid()!=0 surfaces as a preparation refusal (TESTDISK_REQUIRES_ROOT),
        # which must abort before any lifecycle mutation. The option is not
        # hidden; preparation reports the condition.
        result, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
            prepare_return={
                "success": False,
                "code": "TESTDISK_REQUIRES_ROOT",
                "message": "Root execution mode requires Sentinel to run as root.",
            },
        )
        self.assertEqual(result, (None, True, False))
        self._assert_no_lifecycle(manager)
        self._assert_not_launched(manager)

    def test_successful_preparation_precedes_operation_and_status_mutation(self):
        _, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
        )
        names = [c[0] for c in manager.mock_calls]
        prep_idx = names.index("prepare_testdisk_execution")
        append_idx = names.index("append_running_recovery_operation")
        # First RECOVERING update_status call.
        recovering_idx = next(
            i for i, c in enumerate(manager.mock_calls)
            if c[0] == "update_status"
            and _RecoveryStatus.RECOVERING in c.args
        )
        self.assertLess(prep_idx, append_idx)
        self.assertLess(prep_idx, recovering_idx)
        self.assertLess(append_idx, recovering_idx)


class ExecutionAndCompletionTests(_TestdiskWiringHarness):
    def test_normal_exit_completes_and_returns_ready(self):
        result, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
            exec_return=_exec_result(
                success=True, code="TESTDISK_ENDED_NORMALLY"
            ),
        )
        recovery_result, declined, cancelled = result
        self.assertTrue(recovery_result["success"])
        self.assertFalse(declined)
        self.assertFalse(cancelled)
        manager.append_running_recovery_operation.assert_called_once_with(
            mock.ANY, "TESTDISK"
        )
        manager.complete_recovery_operation.assert_called_once_with(
            mock.ANY, success=True
        )
        self.assertTrue(
            any(
                c[0] == "update_status"
                and _RecoveryStatus.READY_FOR_RECOVERY in c.args
                for c in manager.mock_calls
            )
        )

    def test_non_zero_exit_records_failed_still_ready(self):
        result, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
            exec_return=_exec_result(success=False, code="TESTDISK_EXIT_CODE"),
        )
        recovery_result, declined, cancelled = result
        self.assertFalse(recovery_result["success"])
        # Executed, not declined/cancelled.
        self.assertFalse(declined)
        self.assertFalse(cancelled)
        manager.complete_recovery_operation.assert_called_once_with(
            mock.ANY, success=False
        )
        self.assertTrue(
            any(
                c[0] == "update_status"
                and _RecoveryStatus.READY_FOR_RECOVERY in c.args
                for c in manager.mock_calls
            )
        )

    def test_launch_failure_records_failed_still_ready(self):
        result, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
            exec_return=_exec_result(
                success=False, code="TESTDISK_LAUNCH_FAILED"
            ),
        )
        _, declined, cancelled = result
        self.assertFalse(declined)
        self.assertFalse(cancelled)
        manager.append_running_recovery_operation.assert_called_once()
        manager.complete_recovery_operation.assert_called_once_with(
            mock.ANY, success=False
        )

    def test_echo_logging_start_and_result(self):
        _, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
        )
        manager.log_operator.assert_any_call(
            mock.ANY, "SENTINEL", "Recovery method selected: TestDisk."
        )
        started = [
            c for c in manager.log_info.call_args_list
            if "TestDisk session started" in str(c)
        ]
        self.assertTrue(started)


class ConfirmationTests(_TestdiskWiringHarness):
    def test_proceed_decline_launches_nothing(self):
        result, manager, _ = self._run(
            inputs=["n"],
            config_return={"success": True, "config": _valid_config()},
        )
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_not_called()
        self._assert_no_lifecycle(manager)
        self._assert_not_launched(manager)
        manager.log_operator.assert_any_call(
            mock.ANY, "SENTINEL", "TestDisk recovery declined."
        )

    def test_existing_working_image_decline_aborts(self):
        result, manager, _ = self._run(
            inputs=["n"],
            config_return={"success": True, "config": _valid_config()},
            create=("working/testdisk.img",),
        )
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_not_called()
        self._assert_no_lifecycle(manager)

    def test_existing_nonempty_output_decline_aborts(self):
        result, manager, _ = self._run(
            inputs=["n"],
            config_return={"success": True, "config": _valid_config()},
            create=("recovered/testdisk/prev.jpg",),
        )
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_not_called()
        self._assert_no_lifecycle(manager)

    def test_empty_output_directory_needs_no_confirmation(self):
        # An empty recovered/testdisk/ must NOT trigger the reuse prompt; the
        # only prompt is the final proceed confirmation.
        result, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
            create=("recovered/testdisk/",),
        )
        recovery_result, _, _ = result
        self.assertTrue(recovery_result["success"])
        self.assertEqual(manager.input.call_count, 1)

    def test_existing_log_decline_aborts(self):
        result, manager, _ = self._run(
            inputs=["n"],
            config_return={"success": True, "config": _valid_config()},
            create=("evidence/testdisk.log",),
        )
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_not_called()
        self._assert_no_lifecycle(manager)


class FailClosedOsErrorTests(_TestdiskWiringHarness):
    def test_output_check_oserror_fails_closed(self):
        class _Os:
            def scandir(self, path):
                raise OSError("scandir boom")

            def lstat(self, path):
                raise FileNotFoundError

        result, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
            os_module=_Os(),
        )
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_not_called()
        self._assert_no_lifecycle(manager)
        manager.log_error.assert_called()

    def test_log_check_oserror_fails_closed(self):
        class _Os:
            def scandir(self, path):
                return contextlib.nullcontext([])

            def lstat(self, path):
                raise OSError("lstat boom")

        result, manager, _ = self._run(
            inputs=["y"],
            config_return={"success": True, "config": _valid_config()},
            os_module=_Os(),
        )
        self.assertEqual(result, (None, True, False))
        manager.prepare_testdisk_execution.assert_not_called()
        self._assert_no_lifecycle(manager)


class MenuRoutingTests(unittest.TestCase):
    def _load_menu(self, manager):
        namespace = {
            "tr": lambda key, **kwargs: key,
            "print": manager.print,
            "input": manager.input,
            "log_info": manager.log_info,
            "log_operator": manager.log_operator,
            "recommend_recovery_method": lambda: {
                "recommended_operation": "photorec",
                "confidence": "LOW",
                "reason": "oracle.recovery.photorec_only",
            },
            "_run_testdisk_recovery": manager.run_testdisk_recovery,
        }
        exec(_extract_sentinel_function("_run_recovery_method_selection"),
             namespace)
        return namespace["_run_recovery_method_selection"]

    def _session(self):
        return types.SimpleNamespace(
            recovery_path="/tmp/case", source_device=SOURCE_DEVICE
        )

    def test_choice_two_routes_to_testdisk(self):
        manager = mock.Mock()
        manager.input.side_effect = ["2"]
        manager.run_testdisk_recovery.return_value = ("TD", False, False)
        run = self._load_menu(manager)
        result = run(self._session(), ASSESSMENT, INTAKE)
        self.assertEqual(result, ("TD", False, False))
        manager.run_testdisk_recovery.assert_called_once()

    def test_choice_three_cancels(self):
        manager = mock.Mock()
        manager.input.side_effect = ["3"]
        run = self._load_menu(manager)
        result = run(self._session(), ASSESSMENT, INTAKE)
        self.assertEqual(result, (None, False, True))
        manager.run_testdisk_recovery.assert_not_called()

    def test_invalid_then_cancel_retries(self):
        manager = mock.Mock()
        manager.input.side_effect = ["9", "3"]
        run = self._load_menu(manager)
        result = run(self._session(), ASSESSMENT, INTAKE)
        self.assertEqual(result, (None, False, True))
        manager.print.assert_any_call("validation.invalid_selection")


if __name__ == "__main__":
    unittest.main()
