"""
Characterization tests for the presentation and routing behaviour of
bin/sentinel _run_acquisition_workflow().

These lock the CURRENT operator-visible output ordering, status
transitions, recovery-method-selection invocation and returned workflow
dict for the three paths that reach imaging-completion / integrity
verification:

- new imaging
- resume imaging
- fingerprint retry

The function is loaded in isolation via the existing AST-exec harness
(see test_i18n) so every collaborator is injected explicitly and no
production code is imported for the workflow itself. tr() is stubbed to
return its key, so assertions capture the exact translation keys and
values printed, in order.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import call

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


def _load_sentinel_function(function_name, namespace=None):
    namespace = {} if namespace is None else namespace
    exec(_extract_sentinel_function(function_name), namespace)
    return namespace[function_name]


# The real predicate, so branch selection is characterized faithfully.
_CONFIRMED_YES = _load_sentinel_function("_confirmed_yes")


class _RecoveryStatus:
    READY_FOR_IMAGING = "READY_FOR_IMAGING"
    IMAGING = "IMAGING"
    READY_FOR_RECOVERY = "READY_FOR_RECOVERY"


SOURCE_DEVICE = types.SimpleNamespace(path="/dev/sdb")
SESSION = types.SimpleNamespace(
    recovery_path="/tmp/case",
    source_device=SOURCE_DEVICE,
)
DEVICES = ("DEVICES_SENTINEL",)
ASSESSMENT = types.SimpleNamespace(tag="assessment")
INTAKE = {"intake": {}}
EXCLUDE = ["/mnt/x"]

# Opaque sentinels prove the workflow passes the recovery-selection tuple
# through verbatim (distinct from the False defaults used when it is not
# called).
RECOVERY_RETURN = ("RECOVERY_RESULT", "DECLINED_SENTINEL", "CANCELLED_SENTINEL")


def _imaging_success():
    return {
        "success": True,
        "status": "completed",
        "artifacts": ["images/source.img"],
    }


def _integrity_success():
    return {
        "success": True,
        "image_filename": "source.img",
        "algorithm": "SHA-256",
        "digest": "abc123",
        "evidence_path": "/tmp/case/evidence/source.sha256",
    }


def _integrity_failure():
    return {
        "success": False,
        "image_filename": "source.img",
        "algorithm": "SHA-256",
    }


class _AcquisitionWorkflowCase(unittest.TestCase):
    def _run(
        self,
        *,
        state,
        imaging_result=None,
        integrity_result=None,
        mount_gate=(True, EXCLUDE),
        identity=None,
        recovery_return=RECOVERY_RETURN,
        input_response="y",
    ):
        manager = mock.Mock()
        manager.input.return_value = input_response
        manager.execute_forensic_image.return_value = imaging_result
        manager.verify_forensic_image.return_value = integrity_result
        manager.run_recovery_method_selection.return_value = recovery_return
        manager.run_mount_safety_gate.return_value = mount_gate

        namespace = {
            "classify_acquisition_state": mock.Mock(
                return_value={"state": state}
            ),
            "log_info": mock.Mock(),
            "log_warning": mock.Mock(),
            "log_error": mock.Mock(),
            "log_operator": mock.Mock(),
            "tr": lambda key, **kwargs: key,
            "operator_message": lambda result, owner: "OPMSG",
            "print": manager.print,
            "input": manager.input,
            "update_status": manager.update_status,
            "RecoveryStatus": _RecoveryStatus,
            "_confirmed_yes": _CONFIRMED_YES,
            "verify_forensic_image": manager.verify_forensic_image,
            "validate_source_identity_for_resume": mock.Mock(
                return_value=identity
            ),
            "_print_identity_comparison": mock.Mock(),
            "_run_mount_safety_gate": manager.run_mount_safety_gate,
            "execute_forensic_image": manager.execute_forensic_image,
            "_run_recovery_method_selection": (
                manager.run_recovery_method_selection
            ),
        }

        # The workflow now delegates the integrity/completion sequence to
        # _run_integrity_and_completion. Load the REAL helper into the same
        # shared namespace so it resolves the already-injected collaborators;
        # this keeps every assertion below unchanged.
        _load_sentinel_function("_run_integrity_and_completion", namespace)

        workflow = _load_sentinel_function(
            "_run_acquisition_workflow", namespace
        )
        result = workflow(SESSION, DEVICES, ASSESSMENT, INTAKE)
        return result, manager

    def _assert_integrity_status_recovery_order(self, manager):
        calls = manager.mock_calls
        integrity_idx = calls.index(
            call.print("integrity.label.result", "integrity.result.recorded")
        )
        status_idx = calls.index(
            call.update_status(
                SESSION,
                _RecoveryStatus.READY_FOR_RECOVERY,
                SOURCE_DEVICE,
                ASSESSMENT,
                intake=INTAKE,
            )
        )
        recovery_idx = calls.index(
            call.run_recovery_method_selection(SESSION, ASSESSMENT, INTAKE)
        )
        self.assertLess(integrity_idx, status_idx)
        self.assertLess(status_idx, recovery_idx)


class NewImagingTests(_AcquisitionWorkflowCase):
    def test_new_imaging_success_integrity_success(self):
        imaging_result = _imaging_success()
        integrity_result = _integrity_success()

        result, manager = self._run(
            state="no_acquisition",
            imaging_result=imaging_result,
            integrity_result=integrity_result,
        )

        self.assertEqual(
            manager.mock_calls,
            [
                call.run_mount_safety_gate(SESSION, DEVICES),
                call.print(),
                call.print("imaging.new.title"),
                call.print(
                    "imaging.label.operation",
                    "oracle.step.create_forensic_image",
                ),
                call.print("imaging.label.source", "/dev/sdb"),
                call.print(
                    "imaging.label.output", "/tmp/case/images/source.img"
                ),
                call.print("imaging.label.map", "/tmp/case/images/source.map"),
                call.input("imaging.prompt.new"),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.IMAGING,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
                call.execute_forensic_image(
                    SESSION, resume=False, exclude_mount_targets=EXCLUDE
                ),
                call.print(),
                call.print("archive.title"),
                call.print("archive.label.status", "completed"),
                call.print("archive.label.result", "OPMSG"),
                call.print("archive.label.artifacts"),
                call.print("- images/source.img"),
                call.verify_forensic_image(SESSION),
                call.print(),
                call.print("integrity.title"),
                call.print("integrity.label.image", "source.img"),
                call.print("integrity.label.algorithm", "SHA-256"),
                call.print(
                    "integrity.label.result", "integrity.result.recorded"
                ),
                call.print("integrity.label.sha256", "abc123"),
                call.print(
                    "integrity.label.saved_to",
                    "/tmp/case/evidence/source.sha256",
                ),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.READY_FOR_RECOVERY,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
                call.run_recovery_method_selection(
                    SESSION, ASSESSMENT, INTAKE
                ),
            ],
        )

        self._assert_integrity_status_recovery_order(manager)

        self.assertEqual(
            result,
            {
                "imaging_result": imaging_result,
                "integrity_result": integrity_result,
                "recovery_result": "RECOVERY_RESULT",
                "imaging_declined": False,
                "recovery_declined": "DECLINED_SENTINEL",
                "recovery_selection_cancelled": "CANCELLED_SENTINEL",
            },
        )

    def test_new_imaging_success_integrity_failure(self):
        imaging_result = _imaging_success()
        integrity_result = _integrity_failure()

        result, manager = self._run(
            state="no_acquisition",
            imaging_result=imaging_result,
            integrity_result=integrity_result,
        )

        self.assertEqual(
            manager.mock_calls,
            [
                call.run_mount_safety_gate(SESSION, DEVICES),
                call.print(),
                call.print("imaging.new.title"),
                call.print(
                    "imaging.label.operation",
                    "oracle.step.create_forensic_image",
                ),
                call.print("imaging.label.source", "/dev/sdb"),
                call.print(
                    "imaging.label.output", "/tmp/case/images/source.img"
                ),
                call.print("imaging.label.map", "/tmp/case/images/source.map"),
                call.input("imaging.prompt.new"),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.IMAGING,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
                call.execute_forensic_image(
                    SESSION, resume=False, exclude_mount_targets=EXCLUDE
                ),
                call.print(),
                call.print("archive.title"),
                call.print("archive.label.status", "completed"),
                call.print("archive.label.result", "OPMSG"),
                call.print("archive.label.artifacts"),
                call.print("- images/source.img"),
                call.verify_forensic_image(SESSION),
                call.print(),
                call.print("integrity.title"),
                call.print("integrity.label.image", "source.img"),
                call.print("integrity.label.algorithm", "SHA-256"),
                call.print(
                    "integrity.label.result", "integrity.result.failed"
                ),
                call.print("integrity.label.error", "OPMSG"),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.READY_FOR_IMAGING,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
            ],
        )

        manager.run_recovery_method_selection.assert_not_called()

        self.assertEqual(
            result,
            {
                "imaging_result": imaging_result,
                "integrity_result": integrity_result,
                "recovery_result": None,
                "imaging_declined": False,
                "recovery_declined": False,
                "recovery_selection_cancelled": False,
            },
        )


class ResumeImagingTests(_AcquisitionWorkflowCase):
    def test_resume_imaging_success_integrity_success(self):
        imaging_result = _imaging_success()
        integrity_result = _integrity_success()

        result, manager = self._run(
            state="incomplete_ddrescue",
            identity={"valid": True},
            imaging_result=imaging_result,
            integrity_result=integrity_result,
        )

        self.assertEqual(
            manager.mock_calls,
            [
                call.print(),
                call.print("imaging.resume.title"),
                call.print(
                    "imaging.label.image", "/tmp/case/images/source.img"
                ),
                call.print("imaging.label.map", "/tmp/case/images/source.map"),
                call.print(),
                call.print(),
                call.run_mount_safety_gate(SESSION, DEVICES),
                call.print("imaging.ddrescue.continue"),
                call.input("imaging.prompt.resume"),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.IMAGING,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
                call.execute_forensic_image(
                    SESSION, resume=True, exclude_mount_targets=EXCLUDE
                ),
                call.print(),
                call.print("archive.title"),
                call.print("archive.label.status", "completed"),
                call.print("archive.label.result", "OPMSG"),
                call.print("archive.label.artifacts"),
                call.print("- images/source.img"),
                call.verify_forensic_image(SESSION),
                call.print(),
                call.print("integrity.title"),
                call.print("integrity.label.image", "source.img"),
                call.print("integrity.label.algorithm", "SHA-256"),
                call.print(
                    "integrity.label.result", "integrity.result.recorded"
                ),
                call.print("integrity.label.sha256", "abc123"),
                call.print(
                    "integrity.label.saved_to",
                    "/tmp/case/evidence/source.sha256",
                ),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.READY_FOR_RECOVERY,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
                call.run_recovery_method_selection(
                    SESSION, ASSESSMENT, INTAKE
                ),
            ],
        )

        self._assert_integrity_status_recovery_order(manager)

        self.assertEqual(
            result,
            {
                "imaging_result": imaging_result,
                "integrity_result": integrity_result,
                "recovery_result": "RECOVERY_RESULT",
                "imaging_declined": False,
                "recovery_declined": "DECLINED_SENTINEL",
                "recovery_selection_cancelled": "CANCELLED_SENTINEL",
            },
        )

    def test_resume_imaging_success_integrity_failure(self):
        imaging_result = _imaging_success()
        integrity_result = _integrity_failure()

        result, manager = self._run(
            state="incomplete_ddrescue",
            identity={"valid": True},
            imaging_result=imaging_result,
            integrity_result=integrity_result,
        )

        self.assertEqual(
            manager.mock_calls,
            [
                call.print(),
                call.print("imaging.resume.title"),
                call.print(
                    "imaging.label.image", "/tmp/case/images/source.img"
                ),
                call.print("imaging.label.map", "/tmp/case/images/source.map"),
                call.print(),
                call.print(),
                call.run_mount_safety_gate(SESSION, DEVICES),
                call.print("imaging.ddrescue.continue"),
                call.input("imaging.prompt.resume"),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.IMAGING,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
                call.execute_forensic_image(
                    SESSION, resume=True, exclude_mount_targets=EXCLUDE
                ),
                call.print(),
                call.print("archive.title"),
                call.print("archive.label.status", "completed"),
                call.print("archive.label.result", "OPMSG"),
                call.print("archive.label.artifacts"),
                call.print("- images/source.img"),
                call.verify_forensic_image(SESSION),
                call.print(),
                call.print("integrity.title"),
                call.print("integrity.label.image", "source.img"),
                call.print("integrity.label.algorithm", "SHA-256"),
                call.print(
                    "integrity.label.result", "integrity.result.failed"
                ),
                call.print("integrity.label.error", "OPMSG"),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.READY_FOR_IMAGING,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
            ],
        )

        manager.run_recovery_method_selection.assert_not_called()

        self.assertEqual(
            result,
            {
                "imaging_result": imaging_result,
                "integrity_result": integrity_result,
                "recovery_result": None,
                "imaging_declined": False,
                "recovery_declined": False,
                "recovery_selection_cancelled": False,
            },
        )


class FingerprintRetryTests(_AcquisitionWorkflowCase):
    def test_fingerprint_missing_approved_integrity_success(self):
        integrity_result = _integrity_success()

        result, manager = self._run(
            state="imaging_complete_fingerprint_missing",
            integrity_result=integrity_result,
        )

        self.assertEqual(
            manager.mock_calls,
            [
                call.print(),
                call.print("imaging.fingerprint.title"),
                call.print("imaging.fingerprint.missing"),
                call.print("imaging.fingerprint.no_rerun"),
                call.input("imaging.prompt.fingerprint"),
                call.verify_forensic_image(SESSION),
                call.print(),
                call.print("integrity.title"),
                call.print("integrity.label.image", "source.img"),
                call.print("integrity.label.algorithm", "SHA-256"),
                call.print(
                    "integrity.label.result", "integrity.result.recorded"
                ),
                call.print("integrity.label.sha256", "abc123"),
                call.print(
                    "integrity.label.saved_to",
                    "/tmp/case/evidence/source.sha256",
                ),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.READY_FOR_RECOVERY,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
                call.run_recovery_method_selection(
                    SESSION, ASSESSMENT, INTAKE
                ),
            ],
        )

        self._assert_integrity_status_recovery_order(manager)
        manager.execute_forensic_image.assert_not_called()
        manager.run_mount_safety_gate.assert_not_called()

        self.assertEqual(
            result,
            {
                "imaging_result": None,
                "integrity_result": integrity_result,
                "recovery_result": "RECOVERY_RESULT",
                "imaging_declined": False,
                "recovery_declined": "DECLINED_SENTINEL",
                "recovery_selection_cancelled": "CANCELLED_SENTINEL",
            },
        )

    def test_fingerprint_missing_approved_integrity_failure(self):
        integrity_result = _integrity_failure()

        result, manager = self._run(
            state="imaging_complete_fingerprint_missing",
            integrity_result=integrity_result,
        )

        self.assertEqual(
            manager.mock_calls,
            [
                call.print(),
                call.print("imaging.fingerprint.title"),
                call.print("imaging.fingerprint.missing"),
                call.print("imaging.fingerprint.no_rerun"),
                call.input("imaging.prompt.fingerprint"),
                call.verify_forensic_image(SESSION),
                call.print(),
                call.print("integrity.title"),
                call.print("integrity.label.image", "source.img"),
                call.print("integrity.label.algorithm", "SHA-256"),
                call.print(
                    "integrity.label.result", "integrity.result.failed"
                ),
                call.print("integrity.label.error", "OPMSG"),
                call.update_status(
                    SESSION,
                    _RecoveryStatus.READY_FOR_IMAGING,
                    SOURCE_DEVICE,
                    ASSESSMENT,
                    intake=INTAKE,
                ),
            ],
        )

        manager.run_recovery_method_selection.assert_not_called()
        manager.execute_forensic_image.assert_not_called()

        self.assertEqual(
            result,
            {
                "imaging_result": None,
                "integrity_result": integrity_result,
                "recovery_result": None,
                "imaging_declined": False,
                "recovery_declined": False,
                "recovery_selection_cancelled": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
