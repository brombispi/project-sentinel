import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from core.device import Device
from modules.aegis import evaluate

SENTINEL_SOURCE = (SOURCE_ROOT / "bin" / "sentinel").read_text(
    encoding="utf-8"
)


def _extract_sentinel_function(function_name):
    module = ast.parse(SENTINEL_SOURCE)
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            segment = ast.get_source_segment(SENTINEL_SOURCE, node)
            if segment is None:
                raise ValueError(f"Could not extract {function_name}")
            return segment
    raise ValueError(f"Function {function_name} not found in sentinel")


def _load_sentinel_function(function_name, namespace=None):
    namespace = {} if namespace is None else namespace
    exec(_extract_sentinel_function(function_name), namespace)
    return namespace[function_name]


def _make_device(*, protected=False, mounted=False, serial="TESTSERIAL"):
    return Device(
        name="test",
        model="Test Model",
        serial=serial,
        size="1TB",
        transport="usb",
        role="RECOVERY ENGINE" if protected else "EXTERNAL DEVICE",
        protected=protected,
        mounted=mounted,
        filesystem="unknown",
        access_mode="READ_WRITE",
        mount_point="/mnt/test" if mounted else None,
    )


def _recovery_status():
    return type(
        "RecoveryStatus",
        (),
        {
            "NEW": "NEW",
            "ASSESSING": "ASSESSING",
            "AWAITING_CUSTOMER_RESPONSE": "AWAITING_CUSTOMER_RESPONSE",
            "READY_FOR_IMAGING": "READY_FOR_IMAGING",
            "IMAGING": "IMAGING",
            "READY_FOR_RECOVERY": "READY_FOR_RECOVERY",
            "RECOVERING": "RECOVERING",
            "ON_HOLD": "ON_HOLD",
            "COMPLETED": "COMPLETED",
            "CANCELLED": "CANCELLED",
        },
    )()


def _persisted_approved_assessment(device):
    assessment = mock.Mock()
    assessment.decision.status = "APPROVED"
    assessment.decision.reason = "Loaded from persisted case."
    assessment.device = device
    return assessment


class RefreshAssessmentOnResumeTests(unittest.TestCase):
    def _load_refresh(self, **overrides):
        namespace = {
            "evaluate": mock.Mock(),
            "update_status": mock.Mock(),
            "RecoveryStatus": _recovery_status(),
            "create_strategy": mock.Mock(return_value=mock.Mock()),
            "log_info": mock.Mock(),
            "log_warning": mock.Mock(),
            "_print_assessment_context": mock.Mock(),
        }
        namespace.update(overrides)
        refresh = _load_sentinel_function(
            "_refresh_assessment_on_resume",
            namespace,
        )
        return refresh, namespace

    def test_replaces_in_memory_assessment_with_fresh_evaluate(self):
        device = _make_device()
        session = mock.Mock()
        session.source_device = device
        persisted = _persisted_approved_assessment(device)
        fresh = mock.Mock()
        fresh.decision.status = "APPROVED"

        refresh, namespace = self._load_refresh()
        namespace["evaluate"].return_value = fresh

        result, stopped = refresh(session, persisted, {"intake": {}})

        namespace["evaluate"].assert_called_once_with(device)
        self.assertIs(session.assessment, fresh)
        self.assertIs(result, fresh)
        self.assertFalse(stopped)
        namespace["update_status"].assert_not_called()

    def test_stop_reuses_existing_on_hold_handling(self):
        device = _make_device()
        session = mock.Mock()
        session.source_device = device
        persisted = _persisted_approved_assessment(device)
        fresh = mock.Mock()
        fresh.decision.status = "STOP"
        fresh.decision.reason = "Source device is currently mounted."

        refresh, namespace = self._load_refresh()
        namespace["evaluate"].return_value = fresh

        result, stopped = refresh(session, persisted, {"intake": {}})

        namespace["update_status"].assert_called_once_with(
            session,
            "ON_HOLD",
            device,
            fresh,
            intake={"intake": {}},
        )
        namespace["_print_assessment_context"].assert_called_once()
        namespace["log_warning"].assert_called_once()
        self.assertTrue(stopped)
        self.assertIs(result, fresh)


class RouteCaseResumeAssessmentTests(unittest.TestCase):
    def _load_route_case(self, **overrides):
        namespace = {
            "log_warning": mock.Mock(),
            "log_info": mock.Mock(),
            "log_error": mock.Mock(),
            "log_operator": mock.Mock(),
            "tr": lambda key, **kwargs: key,
            "print": mock.Mock(),
            "input": mock.Mock(return_value="y"),
            "_confirmed_yes": lambda value: value in ("y", "yes"),
            "_run_delivery_workflow": mock.Mock(),
            "resolve_resume_status": mock.Mock(
                return_value="READY_FOR_RECOVERY",
            ),
            "_require_assessment": mock.Mock(
                side_effect=lambda session, assessment, workflow_name: assessment
            ),
            "update_status": mock.Mock(),
            "RecoveryStatus": _recovery_status(),
            "collect_case_intake": mock.Mock(),
            "_run_assessment_pipeline": mock.Mock(),
            "_finish_session": mock.Mock(),
            "classify_acquisition_state": mock.Mock(
                return_value={"state": "none"},
            ),
            "create_strategy": mock.Mock(return_value=mock.Mock()),
            "_run_acquisition_workflow": mock.Mock(),
            "_run_recovery_method_selection": mock.Mock(
                return_value=(None, False, True),
            ),
            "resolve_interrupted_recovery_operation": mock.Mock(),
            "evaluate": evaluate,
            "_print_assessment_context": mock.Mock(),
        }
        refresh = _load_sentinel_function(
            "_refresh_assessment_on_resume",
            namespace,
        )
        namespace["_refresh_assessment_on_resume"] = refresh
        namespace.update(overrides)
        route_case = _load_sentinel_function("route_case", namespace)
        return route_case, namespace

    def _session(self, *, status="READY_FOR_IMAGING", device=None):
        session = mock.Mock()
        session.status = status
        session.session_id = "REC-2026-000001"
        session.case_name = "Test Case"
        session.recovery_path = "/tmp/recovery"
        session.source_device = device or _make_device()
        session.assessment = None
        return session

    def test_stored_approval_is_not_blindly_reused(self):
        route_case, namespace = self._load_route_case()
        device = _make_device(mounted=True)
        session = self._session(device=device)
        persisted = _persisted_approved_assessment(device)
        intake = {"intake": {}}

        route_case(session, intake, persisted, [], [])

        namespace["_run_acquisition_workflow"].assert_not_called()
        namespace["update_status"].assert_called_once_with(
            session,
            "ON_HOLD",
            device,
            mock.ANY,
            intake=intake,
        )
        stopped_assessment = namespace["update_status"].call_args.args[3]
        self.assertEqual(stopped_assessment.decision.status, "STOP")
        self.assertEqual(stopped_assessment.decision.law, "SL-008")

    def test_aegis_is_evaluated_again_on_resume(self):
        evaluate_mock = mock.Mock(side_effect=evaluate)
        route_case, namespace = self._load_route_case(evaluate=evaluate_mock)
        device = _make_device(serial="RESUME-SERIAL-007")
        session = self._session(status="READY_FOR_IMAGING", device=device)
        persisted = _persisted_approved_assessment(device)

        route_case(session, {"intake": {}}, persisted, [], [])

        evaluate_mock.assert_called_once_with(device)
        self.assertIs(session.assessment.device, device)

    def test_newly_mounted_source_causes_sl_008_stop(self):
        route_case, namespace = self._load_route_case()
        device = _make_device(mounted=True, serial="MOUNTED-SERIAL")
        session = self._session(device=device)
        persisted = _persisted_approved_assessment(device)

        route_case(session, {"intake": {}}, persisted, [], [])

        namespace["_run_acquisition_workflow"].assert_not_called()
        stopped_assessment = namespace["update_status"].call_args.args[3]
        self.assertEqual(stopped_assessment.decision.law, "SL-008")
        self.assertEqual(
            stopped_assessment.decision.reason,
            "Source device is currently mounted.",
        )

    def test_newly_unidentified_source_causes_sl_003_stop(self):
        route_case, namespace = self._load_route_case()
        device = _make_device(mounted=False, serial="Unknown")
        session = self._session(device=device)
        persisted = _persisted_approved_assessment(device)

        route_case(session, {"intake": {}}, persisted, [], [])

        namespace["_run_acquisition_workflow"].assert_not_called()
        stopped_assessment = namespace["update_status"].call_args.args[3]
        self.assertEqual(stopped_assessment.decision.law, "SL-003")
        self.assertEqual(
            stopped_assessment.decision.reason,
            "Source device identity cannot be trusted.",
        )

    def test_safe_source_continues_to_imaging_workflow(self):
        route_case, namespace = self._load_route_case()
        device = _make_device(mounted=False, serial="SAFE-SERIAL-007")
        session = self._session(device=device)
        persisted = _persisted_approved_assessment(device)

        route_case(session, {"intake": {}}, persisted, [], [])

        namespace["_run_acquisition_workflow"].assert_called_once()
        passed_assessment = (
            namespace["_run_acquisition_workflow"].call_args.args[2]
        )
        self.assertEqual(passed_assessment.decision.status, "APPROVED")
        self.assertIs(session.assessment, passed_assessment)

    def test_completed_case_defers_refresh_until_after_reopen(self):
        evaluate_mock = mock.Mock(side_effect=evaluate)
        route_case, namespace = self._load_route_case(evaluate=evaluate_mock)
        device = _make_device(mounted=False, serial="COMPLETED-SERIAL")
        session = self._session(status="COMPLETED", device=device)
        persisted = _persisted_approved_assessment(device)
        intake = {"intake": {}}

        route_case(session, intake, persisted, [], [])

        namespace["_run_delivery_workflow"].assert_called_once_with(
            session,
            persisted,
            intake,
            recovery_result=None,
        )
        evaluate_mock.assert_called_once_with(device)
        namespace["_run_recovery_method_selection"].assert_called_once()


class NewCaseAssessmentPipelineTests(unittest.TestCase):
    def test_new_case_path_does_not_use_resume_refresh(self):
        new_case_source = _extract_sentinel_function("_run_new_case")
        self.assertNotIn("_refresh_assessment_on_resume", new_case_source)

    def test_new_case_pipeline_evaluates_when_source_unset(self):
        pipeline_source = _extract_sentinel_function("_run_assessment_pipeline")
        self.assertIn("if session.source_device is None:", pipeline_source)
        self.assertIn("assessment = evaluate(session.source_device)", pipeline_source)


if __name__ == "__main__":
    unittest.main()
