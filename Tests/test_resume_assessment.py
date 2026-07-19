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


class AegisDecisionAuditEventTests(unittest.TestCase):
    """SL-004: the AEGIS case-decision audit event is self-contained, carrying
    the decision's persisted forensic context (law, risk, confidence, reason)
    read verbatim from the Decision object via the real resume call site."""

    def _captured_aegis_event(self, device):
        captured = {}

        def _record(session, module, event):
            if module == "AEGIS":
                captured["event"] = event

        namespace = {
            "evaluate": evaluate,
            "update_status": mock.Mock(),
            "RecoveryStatus": _recovery_status(),
            "create_strategy": mock.Mock(return_value=mock.Mock()),
            "log_info": _record,
            "log_warning": _record,
            "_print_assessment_context": mock.Mock(),
        }
        refresh = _load_sentinel_function(
            "_refresh_assessment_on_resume",
            namespace,
        )

        session = mock.Mock()
        session.source_device = device
        refresh(session, mock.Mock(), {"intake": {}})
        return captured["event"]

    def test_stop_event_contains_all_fields(self):
        event = self._captured_aegis_event(_make_device(serial="Unknown"))
        self.assertIn("Decision: STOP", event)
        self.assertIn("law=SL-003", event)
        self.assertIn("risk=CRITICAL", event)
        self.assertIn("confidence=100", event)
        self.assertIn(
            "reason=Source device identity cannot be trusted.",
            event,
        )

    def test_sl_008_law_code_untranslated(self):
        event = self._captured_aegis_event(_make_device(mounted=True))
        self.assertIn("Decision: STOP", event)
        self.assertIn("law=SL-008", event)

    def test_non_stop_event_contains_all_fields(self):
        event = self._captured_aegis_event(_make_device(serial="SAFE-SERIAL-1"))
        self.assertIn("Decision: APPROVED", event)
        self.assertIn("law=NOT_RECORDED", event)
        self.assertIn("risk=LOW", event)
        self.assertIn("confidence=100", event)
        self.assertIn("reason=External device.", event)

    def test_missing_law_uses_stable_sentinel(self):
        event = self._captured_aegis_event(_make_device(serial="SAFE-SERIAL-2"))
        self.assertIn("law=NOT_RECORDED", event)
        # Never leak Python None into the audit trail.
        self.assertNotIn("law=None", event)

    def test_risk_and_confidence_come_from_decision_object(self):
        device = _make_device(serial="Unknown")
        decision = evaluate(device).decision
        event = self._captured_aegis_event(device)
        self.assertIn(f"risk={decision.risk}", event)
        self.assertIn(f"confidence={decision.confidence}", event)

    def _captured_pipeline_stop_event(self, device):
        captured = {}

        def _record(session, module, event):
            if module == "AEGIS":
                captured["event"] = event

        namespace = {
            "print_device_selection_list": mock.Mock(),
            "select_source_device": mock.Mock(),
            "evaluate": evaluate,
            "create_strategy": mock.Mock(return_value=mock.Mock()),
            "log_info": _record,
            "log_warning": _record,
            "_print_assessment_context": mock.Mock(),
            "RecoveryStatus": _recovery_status(),
            "update_status": mock.Mock(),
        }
        pipeline = _load_sentinel_function("_run_assessment_pipeline", namespace)

        session = mock.Mock()
        session.source_device = device
        session.assessment = evaluate(device)
        pipeline(session, {"intake": {}}, [])
        return captured["event"]

    def test_pipeline_stop_event_contains_all_fields(self):
        # Behavioural proof for the second call site: execute the real
        # _run_assessment_pipeline STOP branch and capture its emitted event.
        event = self._captured_pipeline_stop_event(_make_device(mounted=True))
        self.assertIn("Decision: STOP", event)
        self.assertIn("law=SL-008", event)
        self.assertIn("risk=CRITICAL", event)
        self.assertIn("confidence=100", event)
        self.assertIn(
            "reason=Source device is currently mounted.",
            event,
        )

    def test_both_call_sites_are_enriched(self):
        # Guard both production AEGIS decision log sites (resume + new-case
        # pipeline) against silent regression to the old status-only event.
        for function_name in (
            "_refresh_assessment_on_resume",
            "_run_assessment_pipeline",
        ):
            source = _extract_sentinel_function(function_name)
            with self.subTest(function=function_name):
                self.assertIn("law=", source)
                self.assertIn("risk=", source)
                self.assertIn("confidence=", source)
                self.assertIn("reason=", source)
                self.assertIn("NOT_RECORDED", source)


class NewCaseAssessmentPipelineTests(unittest.TestCase):
    def test_new_case_path_does_not_use_resume_refresh(self):
        new_case_source = _extract_sentinel_function("_run_new_case")
        self.assertNotIn("_refresh_assessment_on_resume", new_case_source)

    def test_new_case_pipeline_evaluates_when_source_unset(self):
        pipeline_source = _extract_sentinel_function("_run_assessment_pipeline")
        self.assertIn("if session.source_device is None:", pipeline_source)
        self.assertIn("assessment = evaluate(session.source_device)", pipeline_source)


def _device(*, name="sdb", model="Test Model", serial="TESTSERIAL",
            size="1TB", mounted=False, role="EXTERNAL DEVICE"):
    return Device(
        name=name,
        model=model,
        serial=serial,
        size=size,
        transport="usb",
        role=role,
        protected=False,
        mounted=mounted,
        filesystem="ext4",
        access_mode="READ_ONLY",
        mount_point="/mnt/x" if mounted else None,
    )


class OperatorDeviceSelectionAuditTests(unittest.TestCase):
    """Operator device selections (SOURCE / DESTINATION) are recorded as
    self-contained OPERATOR-level ECHO events by the orchestration layer,
    only when a device is genuinely newly selected (is None gates)."""

    def _run_pipeline(self, session, devices, **overrides):
        selection_events = []

        def _log_operator(session_, module, event):
            if module == "SENTINEL" and event.startswith("Device selected:"):
                selection_events.append(event)

        approved_dest = mock.Mock()
        approved_dest.approved = False
        approved_dest.risk = "LOW"

        namespace = {
            "print_device_selection_list": mock.Mock(),
            "select_source_device": mock.Mock(),
            "select_destination_device": mock.Mock(),
            "evaluate": mock.Mock(),
            "evaluate_destination": mock.Mock(return_value=approved_dest),
            "create_strategy": mock.Mock(return_value=mock.Mock()),
            "collect_smart_report": mock.Mock(return_value={
                "available": True,
                "health": "OK",
                "output_path": "/tmp/x.smart.txt",
                "warning": None,
            }),
            "display_smart_warning": mock.Mock(return_value=""),
            "display_janus_reason": mock.Mock(return_value=""),
            "operator_message": mock.Mock(return_value=""),
            "relocate_recovery_case": mock.Mock(),
            "_run_acquisition_workflow": mock.Mock(return_value={}),
            "get_runtime_recoveries_root": mock.Mock(
                return_value=Path("/nonexistent-root"),
            ),
            "save_case": mock.Mock(),
            "update_status": mock.Mock(),
            "RecoveryStatus": _recovery_status(),
            "log_info": mock.Mock(),
            "log_warning": mock.Mock(),
            "log_operator": _log_operator,
            "_print_assessment_context": mock.Mock(),
            "Path": Path,
            "tr": lambda key, **kwargs: key,
            "print": mock.Mock(),
        }
        namespace.update(overrides)
        pipeline = _load_sentinel_function("_run_assessment_pipeline", namespace)
        pipeline(session, {"intake": {}}, devices)
        return selection_events

    def _new_source_session(self):
        session = mock.Mock()
        session.source_device = None
        session.destination_device = _device(name="sdc")
        session.recovery_path = "/tmp/recovery"
        return session

    def _approved_source_session(self):
        session = mock.Mock()
        session.source_device = _device(name="sda", serial="SRC-1")
        session.destination_device = None
        session.recovery_path = "/tmp/recovery"
        assessment = mock.Mock()
        assessment.decision.status = "APPROVED"
        assessment.decision.law = None
        assessment.decision.risk = "LOW"
        assessment.decision.confidence = 100
        assessment.decision.reason = "External device."
        session.assessment = assessment
        return session

    def test_source_selection_emits_single_operator_event(self):
        chosen = _device(name="sdb", serial="MOUNTED", mounted=True)
        session = self._new_source_session()
        events = self._run_pipeline(
            session,
            [chosen],
            evaluate=evaluate,
            select_source_device=mock.Mock(return_value=chosen),
        )
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].startswith("Device selected: role=SOURCE"))

    def test_source_event_exact_format_and_values(self):
        chosen = _device(
            name="sdb", model="Samsung 860", serial="S4EWABC", size="500G",
            mounted=True,
        )
        session = self._new_source_session()
        events = self._run_pipeline(
            session,
            [chosen],
            evaluate=evaluate,
            select_source_device=mock.Mock(return_value=chosen),
        )
        self.assertEqual(
            events[0],
            "Device selected: role=SOURCE | path=/dev/sdb | "
            "model=Samsung 860 | serial=S4EWABC | size=500G",
        )

    def test_values_come_from_returned_device_not_list(self):
        listed = _device(name="sdz", serial="LIST-ONLY")
        returned = _device(name="sdb", serial="RETURNED", mounted=True)
        session = self._new_source_session()
        events = self._run_pipeline(
            session,
            [listed, returned],
            evaluate=evaluate,
            select_source_device=mock.Mock(return_value=returned),
        )
        self.assertIn("path=/dev/sdb", events[0])
        self.assertIn("serial=RETURNED", events[0])
        self.assertNotIn("LIST-ONLY", events[0])

    def test_missing_and_blank_values_become_not_recorded(self):
        chosen = _device(name="sdb", model="", serial="   ", size=None,
                         mounted=True)
        session = self._new_source_session()
        events = self._run_pipeline(
            session,
            [chosen],
            evaluate=evaluate,
            select_source_device=mock.Mock(return_value=chosen),
        )
        self.assertEqual(
            events[0],
            "Device selected: role=SOURCE | path=/dev/sdb | "
            "model=NOT_RECORDED | serial=NOT_RECORDED | size=NOT_RECORDED",
        )
        self.assertNotIn("None", events[0])

    def test_meaningful_reported_values_preserved(self):
        for token in ("Unknown", "N/A", "0B"):
            with self.subTest(token=token):
                chosen = _device(
                    name="sdb", model=token, serial=token, size=token,
                    mounted=True,
                )
                session = self._new_source_session()
                events = self._run_pipeline(
                    session,
                    [chosen],
                    evaluate=evaluate,
                    select_source_device=mock.Mock(return_value=chosen),
                )
                self.assertEqual(
                    events[0],
                    "Device selected: role=SOURCE | path=/dev/sdb | "
                    f"model={token} | serial={token} | size={token}",
                )

    def test_destination_selection_emits_single_operator_event(self):
        chosen = _device(name="sdc", serial="DEST-1")
        session = self._approved_source_session()
        events = self._run_pipeline(
            session,
            [session.source_device, chosen],
            select_destination_device=mock.Mock(return_value=chosen),
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0],
            "Device selected: role=DESTINATION | path=/dev/sdc | "
            "model=Test Model | serial=DEST-1 | size=1TB",
        )

    def test_populated_source_emits_no_selection_event(self):
        # Already-selected (e.g. mounted) source: STOP branch, no new SOURCE
        # selection is made, so no selection event is emitted.
        session = mock.Mock()
        session.source_device = _device(name="sda", serial="SRC", mounted=True)
        session.recovery_path = "/tmp/recovery"
        session.assessment = evaluate(session.source_device)
        events = self._run_pipeline(session, [], evaluate=evaluate)
        self.assertEqual(events, [])

    def test_populated_destination_emits_no_selection_event(self):
        session = self._approved_source_session()
        session.destination_device = _device(name="sdc", serial="RESTORED-DST")
        events = self._run_pipeline(session, [session.source_device])
        self.assertEqual(events, [])

    def test_resume_restored_devices_emit_no_selection_event(self):
        # A resumed case restores both devices from the manifest before the
        # pipeline runs; neither is None, so no "Device selected" is emitted.
        session = self._approved_source_session()
        session.source_device = _device(name="sda", serial="RESTORED-SRC")
        session.destination_device = _device(name="sdc", serial="RESTORED-DST")
        events = self._run_pipeline(session, [session.source_device])
        self.assertEqual(events, [])

    def test_no_event_when_selection_helper_does_not_return(self):
        # If the selection helper raises before returning, nothing is logged.
        session = self._new_source_session()
        with self.assertRaises(RuntimeError):
            self._run_pipeline(
                session,
                [_device(name="sdb")],
                select_source_device=mock.Mock(
                    side_effect=RuntimeError("aborted"),
                ),
            )

    def test_selection_logging_lives_inside_is_none_gates(self):
        # Supplementary source guard: both OPERATOR selection events exist and
        # are gated by the existing is None checks.
        source = _extract_sentinel_function("_run_assessment_pipeline")
        self.assertIn('_selected_device_audit_event("SOURCE"', source)
        self.assertIn('"DESTINATION"', source)
        self.assertIn('"Device selected: role=', source)
        self.assertEqual(source.count("Device selected: role="), 1)


if __name__ == "__main__":
    unittest.main()
