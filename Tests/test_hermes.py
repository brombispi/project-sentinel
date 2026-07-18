import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from core.session import RecoverySession
from modules.archive import (
    AcquisitionSourceError,
    FingerprintEvidenceError,
    IMAGE_FILENAME,
    MAP_FILENAME,
    SHA256_FILENAME,
)
from i18n import get_language, set_language
from modules.argus import SmartEvidenceError
from modules.echo import AuditLogError
from modules.hermes import (
    CUSTOMER_POLICY_VERSION,
    CUSTOMER_REPORT_SECTIONS,
    FINGERPRINT_ARTIFACT_RELATIVE_PATH,
    Hermes,
    IMAGE_ARTIFACT_RELATIVE_PATH,
    MAP_ARTIFACT_RELATIVE_PATH,
    TECHNICIAN_REPORT_SECTIONS,
    customer_report_filename,
    technician_report_filename,
)
from modules.manifest import ManifestError
from modules.report_formatter import ReportFormatter

FIXED_GENERATED_AT = datetime(2026, 7, 16, 12, 30, 0)

# English report wording used as test fixtures. These mirror the authoritative
# English strings in Source/i18n/en.json (the localization tests verify parity),
# and let these tests assert the exact default-language wording.
CUSTOMER_IMAGING_COMPLETED = "A complete forensic image of the device was created."
CUSTOMER_IMAGING_NOT_COMPLETED = "The forensic image of the device was not completed."
CUSTOMER_IMAGING_NOT_PERFORMED = "No forensic image of the device was created."

CUSTOMER_RECOMMENDATIONS = (
    "Verify that the recovered data is complete and opens correctly before "
    "relying on it.",
    "Contact us if you find missing or unreadable files in the recovered data.",
    "Keep at least two independent backups of important data in separate "
    "locations.",
    "Store recovered data on a different device from the one that was "
    "recovered.",
)

CUSTOMER_DISCLAIMER = (
    "This report summarizes the data recovery work performed for your case.",
    "Data recovery cannot be guaranteed, and results depend on the condition "
    "of the device.",
    "You are responsible for verifying the recovered data and maintaining your "
    "own backups.",
    "Recovered data is retained according to our data retention policy and is "
    "then securely removed.",
)

IMAGING_DETAILS_FIELDS = (
    "Acquisition State",
    "Acquisition State Code",
    "Image Present",
    "Map Present",
    "Map Status",
    "Map Current Status",
    "Image Path",
    "Map Path",
)

IMAGING_ACQUISITION_SOURCE_FIELDS = (
    "Logical Sector Size",
    "Physical Sector Size",
    "Acquisition Timestamp",
)

ACQUISITION_SOURCE = {
    "serial": "S4EWNF0M803123A",
    "model": "Samsung SSD 860",
    "size_bytes": 500107862016,
    "logical_sector_size": 512,
    "physical_sector_size": 4096,
    "path": "/dev/sdb",
    "timestamp": "2026-07-16T10:00:00",
}

INTEGRITY_VERIFICATION_FIELDS = (
    "Fingerprint Present",
    "Canonical Acquisition Complete",
    "Fingerprint Path",
)

INTEGRITY_FINGERPRINT_FIELDS = (
    "Algorithm",
    "SHA-256 Digest",
    "Fingerprinted Image",
    "Image Size (Bytes)",
    "Fingerprint Timestamp",
)

FINGERPRINT_EVIDENCE = {
    "algorithm": "SHA-256",
    "digest": "abc123def456",
    "image_filename": "source.img",
    "image_size_bytes": 500107862016,
    "timestamp": "2026-07-16 10:05:00",
}

RECOVERY_STATISTICS_FIELDS = (
    "Recovery Attempt Recorded",
    "Recovered File Count",
    "Recovered Directory Count",
    "Recovered Size (Bytes)",
    "Recovered Output Locations",
)


def _recovery_operation(state="COMPLETED", operation_type="PHOTOREC"):
    return {
        "type": operation_type,
        "state": state,
        "started_at": "2026-07-16T11:00:05",
        "finished_at": None if state == "RUNNING" else "2026-07-16T11:42:31",
    }

INCOMPLETE_DDRESCUE_MAP = (
    "# Mapfile. Created by GNU ddrescue\n"
    "# current_pos  current_status  current_pass\n"
    "0x00000000  ?  1\n"
    "0x00000000  0x3A9800000  ?\n"
)


def _case_dir(temp_dir, session_id="REC-2026-000001"):
    case_dir = Path(temp_dir) / session_id
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def _write_manifest(case_dir, manifest):
    manifest_path = case_dir / "case.json"
    manifest_path.write_text(json.dumps(manifest, indent=4) + "\n", encoding="utf-8")
    return manifest_path


def _minimal_manifest(session_id="REC-2026-000001", **overrides):
    manifest = {
        "session_id": session_id,
        "case_name": "",
        "created_at": "2026-07-16T10:00:00",
        "status": "NEW",
    }
    manifest.update(overrides)
    return manifest


def _populated_manifest(session_id="REC-2026-000001"):
    return {
        "session_id": session_id,
        "case_name": "Customer SSD Recovery",
        "created_at": "2026-07-16T10:00:00",
        "status": "COMPLETED",
        "case_contact": {
            "name": "Jane Example",
            "phone": "+49 170 0000000",
            "email": "jane@example.com",
        },
        "intake": {
            "recovery_request": "Recover family photos",
            "incident_description": "Drive stopped mounting after power loss",
            "previous_recovery_attempts": "None",
            "data_priority": "Photos and documents",
        },
        "device": {
            "path": "/dev/sdb",
            "model": "Samsung SSD 860",
            "serial": "S4EWNF0M803123A",
            "size": "500G",
            "size_bytes": 500107862016,
            "transport": "SATA",
            "filesystem": "ext4",
            "role": "EXTERNAL DEVICE",
        },
        "destination": {
            "path": "/dev/sdc",
            "model": "WD Elements",
            "serial": "WX12AB34CD56",
            "size": "2T",
            "size_bytes": 2000398934016,
            "transport": "USB",
            "filesystem": "ext4",
            "role": "EXTERNAL DEVICE",
        },
        "assessment": {
            "decision": "APPROVED",
            "reason": "External device.",
            "risk": "LOW",
            "confidence": 100,
        },
    }


def _session(case_dir, **overrides):
    values = {
        "session_id": case_dir.name,
        "created_at": datetime(2026, 7, 16, 10, 0, 0),
        "status": "NEW",
        "recovery_path": str(case_dir),
        "case_name": "",
    }
    values.update(overrides)
    return RecoverySession(**values)


def _write_incomplete_acquisition_artifacts(case_dir):
    images_dir = case_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / IMAGE_FILENAME).write_bytes(b"\x00")
    (images_dir / MAP_FILENAME).write_text(INCOMPLETE_DDRESCUE_MAP, encoding="utf-8")


def _write_canonical_acquisition_artifacts(case_dir):
    images_dir = case_dir / "images"
    evidence_dir = case_dir / "evidence"
    images_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / IMAGE_FILENAME).write_bytes(b"\x00")
    (images_dir / MAP_FILENAME).write_bytes(b"\x00")
    (evidence_dir / SHA256_FILENAME).write_text(
        "algorithm=SHA-256\n"
        "digest=abc123\n"
        "image=source.img\n"
        "size_bytes=1\n"
        "timestamp=2026-07-16 10:00:00\n",
        encoding="utf-8",
    )


def _write_recovered_artifacts(case_dir):
    recup_dir = case_dir / "recovered" / "recup.1"
    recup_dir.mkdir(parents=True, exist_ok=True)
    (recup_dir / "f1.jpg").write_bytes(b"12345")
    (recup_dir / "f2.jpg").write_bytes(b"678")


AUDIT_LOG_LINES = (
    "2026-07-16 10:00:00 [ARCHIVE][INFO] Recovery session created.",
    "2026-07-16 10:20:41 [ARCHIVE][INFO] Forensic imaging completed.",
    "2026-07-16 11:02:55 [SENTINEL][OPERATOR] Recovery finalization approved.",
)


def _write_audit_log(case_dir, lines=()):
    log_path = case_dir / "audit.log"
    log_path.write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )
    return log_path


class HermesTests(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()
        set_language("en", persist=False)
        self.datetime_patcher = mock.patch(
            "modules.hermes.datetime",
            wraps=datetime,
        )
        self.mock_datetime = self.datetime_patcher.start()
        self.mock_datetime.now.return_value = FIXED_GENERATED_AT

    def tearDown(self):
        self.datetime_patcher.stop()
        set_language(self._previous_language, persist=False)

    def test_build_technician_report_returns_sections_in_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            report = Hermes(_session(case_dir)).build_technician_report()

            self.assertEqual(list(report.keys()), list(TECHNICIAN_REPORT_SECTIONS))

    def test_build_technician_report_fully_populated_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())

            report = Hermes(_session(case_dir)).build_technician_report()

            self.assertEqual(report["Case Information"]["Case Number"], "REC-2026-000001")
            self.assertEqual(
                report["Case Information"]["Case Name"],
                "Customer SSD Recovery",
            )
            self.assertEqual(
                report["Case Information"]["Creation Date"],
                "2026-07-16T10:00:00",
            )
            self.assertEqual(report["Case Information"]["Current Status"], "COMPLETED")
            self.assertEqual(
                report["Case Information"]["Report Generation Date"],
                FIXED_GENERATED_AT,
            )

            self.assertEqual(report["Customer Information"]["Name"], "Jane Example")
            self.assertEqual(
                report["Customer Information"]["Telephone"],
                "+49 170 0000000",
            )
            self.assertEqual(
                report["Customer Information"]["Email"],
                "jane@example.com",
            )

            self.assertEqual(
                report["Intake Summary"]["Requested Recovery"],
                "Recover family photos",
            )
            self.assertEqual(
                report["Intake Summary"]["Incident Description"],
                "Drive stopped mounting after power loss",
            )
            self.assertEqual(
                report["Intake Summary"]["Previous Recovery Attempts"],
                "None",
            )
            self.assertEqual(
                report["Intake Summary"]["Data Priority"],
                "Photos and documents",
            )

            self.assertEqual(report["Device Identity"]["Source Path"], "/dev/sdb")
            self.assertEqual(
                report["Device Identity"]["Source Model"],
                "Samsung SSD 860",
            )
            self.assertEqual(
                report["Device Identity"]["Destination Path"],
                "/dev/sdc",
            )
            self.assertEqual(
                report["Device Identity"]["SMART Evidence"],
                "Not recorded",
            )

            self.assertEqual(report["Assessment Results"]["Decision"], "APPROVED")
            self.assertEqual(report["Assessment Results"]["Reason"], "External device.")
            self.assertEqual(report["Assessment Results"]["Risk"], "LOW")
            self.assertEqual(report["Assessment Results"]["Confidence"], 100)

    def test_build_technician_report_missing_manifest_raises_manifest_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            hermes = Hermes(_session(case_dir))

            with self.assertRaises(ManifestError) as context:
                hermes.build_technician_report()

            self.assertIn("case.json not found", str(context.exception))

    def test_build_technician_report_malformed_manifest_raises_manifest_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            (case_dir / "case.json").write_text("{not valid json", encoding="utf-8")
            hermes = Hermes(_session(case_dir))

            with self.assertRaises(ManifestError) as context:
                hermes.build_technician_report()

            self.assertIn("case.json is malformed", str(context.exception))

    def test_build_technician_report_missing_optional_sections_become_none(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            report = Hermes(_session(case_dir)).build_technician_report()

            self.assertEqual(report["Case Information"]["Case Number"], "REC-2026-000001")
            self.assertIsNone(report["Case Information"]["Case Name"])

            self.assertIsNone(report["Customer Information"]["Name"])
            self.assertIsNone(report["Customer Information"]["Telephone"])
            self.assertIsNone(report["Customer Information"]["Email"])

            self.assertIsNone(report["Intake Summary"]["Requested Recovery"])
            self.assertIsNone(report["Intake Summary"]["Incident Description"])
            self.assertIsNone(report["Intake Summary"]["Previous Recovery Attempts"])
            self.assertIsNone(report["Intake Summary"]["Data Priority"])

            self.assertIsNone(report["Device Identity"]["Source Path"])
            self.assertIsNone(report["Device Identity"]["Destination Path"])
            self.assertEqual(
                report["Device Identity"]["SMART Evidence"],
                "Not recorded",
            )

            self.assertIsNone(report["Assessment Results"]["Decision"])
            self.assertIsNone(report["Assessment Results"]["Reason"])
            self.assertIsNone(report["Assessment Results"]["Risk"])
            self.assertIsNone(report["Assessment Results"]["Confidence"])

            imaging = report["Imaging Details"]
            self.assertEqual(imaging["Acquisition State"], "no_acquisition")
            self.assertEqual(
                imaging["Acquisition State Code"],
                "ACQUISITION_NO_ARTIFACTS",
            )
            self.assertFalse(imaging["Image Present"])
            self.assertFalse(imaging["Map Present"])
            self.assertIsNone(imaging["Map Status"])
            self.assertIsNone(imaging["Map Current Status"])
            self.assertEqual(imaging["Image Path"], IMAGE_ARTIFACT_RELATIVE_PATH)
            self.assertEqual(imaging["Map Path"], MAP_ARTIFACT_RELATIVE_PATH)
            self.assertEqual(
                imaging["Acquisition Source Evidence"],
                "Not recorded",
            )
            self.assertEqual(
                list(imaging.keys()),
                list(IMAGING_DETAILS_FIELDS) + ["Acquisition Source Evidence"],
            )

            integrity = report["Integrity Verification"]
            self.assertFalse(integrity["Fingerprint Present"])
            self.assertFalse(integrity["Canonical Acquisition Complete"])
            self.assertEqual(
                integrity["Fingerprint Path"],
                FINGERPRINT_ARTIFACT_RELATIVE_PATH,
            )
            self.assertEqual(integrity["Fingerprint Evidence"], "Not recorded")
            self.assertEqual(
                list(integrity.keys()),
                list(INTEGRITY_VERIFICATION_FIELDS) + ["Fingerprint Evidence"],
            )

    def test_build_technician_report_empty_values_become_none(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(
                case_dir,
                _minimal_manifest(
                    case_name="",
                    case_contact={"name": " ", "phone": "", "email": ""},
                    intake={
                        "recovery_request": "",
                        "incident_description": " ",
                        "previous_recovery_attempts": "",
                        "data_priority": "",
                    },
                ),
            )

            report = Hermes(_session(case_dir)).build_technician_report()

            self.assertIsNone(report["Case Information"]["Case Name"])
            self.assertIsNone(report["Customer Information"]["Name"])
            self.assertIsNone(report["Intake Summary"]["Requested Recovery"])
            self.assertIsNone(report["Intake Summary"]["Incident Description"])

    def test_build_technician_report_prefers_persisted_manifest_over_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir, session_id="REC-MANIFEST-001")
            _write_manifest(
                case_dir,
                _populated_manifest(session_id="REC-MANIFEST-001"),
            )

            session = _session(
                case_dir,
                session_id="REC-SESSION-999",
                case_name="Session Case Name",
                status="NEW",
                created_at=datetime(2020, 1, 1, 0, 0, 0),
            )

            report = Hermes(session).build_technician_report()

            self.assertEqual(
                report["Case Information"]["Case Number"],
                "REC-MANIFEST-001",
            )
            self.assertEqual(
                report["Case Information"]["Case Name"],
                "Customer SSD Recovery",
            )
            self.assertEqual(
                report["Case Information"]["Creation Date"],
                "2026-07-16T10:00:00",
            )
            self.assertEqual(report["Case Information"]["Current Status"], "COMPLETED")
            self.assertEqual(report["Customer Information"]["Name"], "Jane Example")
            self.assertEqual(report["Assessment Results"]["Decision"], "APPROVED")

    def test_build_technician_markdown_includes_section_headings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            markdown = Hermes(_session(case_dir)).build_technician_markdown()

            self.assertTrue(markdown.startswith("# Technician Report\n"))
            for section_title in TECHNICIAN_REPORT_SECTIONS:
                self.assertIn(f"## {section_title}", markdown)

            self.assertIn("Case Number: REC-2026-000001", markdown)
            self.assertIn(
                f"Report Generation Date: {FIXED_GENERATED_AT}",
                markdown,
            )

    def test_build_report_technician_dispatches_to_technician_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            hermes = Hermes(_session(case_dir))

            self.assertEqual(
                hermes.build_report("technician"),
                hermes.build_technician_report(),
            )

    def test_build_report_unsupported_type_raises_value_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            hermes = Hermes(_session(case_dir))

            with self.assertRaises(ValueError):
                hermes.build_report("unknown")

    def test_build_technician_markdown_delegates_to_report_formatter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            hermes = Hermes(_session(case_dir))
            report = hermes.build_technician_report()
            real_formatter = ReportFormatter()

            with mock.patch(
                "modules.hermes.ReportFormatter",
                return_value=real_formatter,
            ) as formatter_cls:
                result = hermes.build_technician_markdown()

            formatter_cls.assert_called_once_with()
            expected = real_formatter.format_markdown(
                "Technician Report",
                report,
                section_order=TECHNICIAN_REPORT_SECTIONS,
            )
            self.assertEqual(result, expected)

    def test_save_technician_report_writes_markdown_and_returns_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            hermes = Hermes(_session(case_dir))

            report_path = hermes.save_technician_report()

            expected_path = (
                case_dir / "reports" / technician_report_filename("en")
            )
            self.assertEqual(report_path, expected_path)
            self.assertTrue(report_path.is_file())
            self.assertEqual(
                report_path.read_text(encoding="utf-8"),
                hermes.build_technician_markdown(),
            )

    def test_save_technician_report_creates_reports_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            hermes = Hermes(_session(case_dir))

            hermes.save_technician_report()

            self.assertTrue((case_dir / "reports").is_dir())

    def test_save_technician_report_raises_when_file_already_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            hermes = Hermes(_session(case_dir))
            hermes.save_technician_report()

            with self.assertRaises(FileExistsError):
                hermes.save_technician_report()

    def test_build_technician_report_incomplete_acquisition(self):
        if not shutil.which("ddrescuelog"):
            self.skipTest("ddrescuelog is required to classify incomplete maps")

        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            _write_incomplete_acquisition_artifacts(case_dir)

            report = Hermes(_session(case_dir)).build_technician_report()
            imaging = report["Imaging Details"]
            integrity = report["Integrity Verification"]

            self.assertEqual(imaging["Acquisition State"], "incomplete_ddrescue")
            self.assertEqual(
                imaging["Acquisition State Code"],
                "ACQUISITION_INCOMPLETE_DDRESCUE",
            )
            self.assertTrue(imaging["Image Present"])
            self.assertTrue(imaging["Map Present"])
            self.assertEqual(imaging["Map Status"], "incomplete")
            self.assertEqual(imaging["Map Current Status"], "?")
            self.assertEqual(imaging["Image Path"], IMAGE_ARTIFACT_RELATIVE_PATH)
            self.assertEqual(imaging["Map Path"], MAP_ARTIFACT_RELATIVE_PATH)
            self.assertEqual(
                imaging["Acquisition Source Evidence"],
                "Not recorded",
            )

            self.assertFalse(integrity["Fingerprint Present"])
            self.assertFalse(integrity["Canonical Acquisition Complete"])
            self.assertEqual(
                integrity["Fingerprint Path"],
                FINGERPRINT_ARTIFACT_RELATIVE_PATH,
            )
            self.assertEqual(integrity["Fingerprint Evidence"], "Not recorded")

    def test_build_technician_report_canonical_acquisition_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            _write_canonical_acquisition_artifacts(case_dir)

            report = Hermes(_session(case_dir)).build_technician_report()
            imaging = report["Imaging Details"]
            integrity = report["Integrity Verification"]

            self.assertEqual(imaging["Acquisition State"], "completed_canonical")
            self.assertEqual(
                imaging["Acquisition State Code"],
                "ACQUISITION_COMPLETED_CANONICAL",
            )
            self.assertTrue(imaging["Image Present"])
            self.assertTrue(imaging["Map Present"])
            self.assertIsNone(imaging["Map Status"])
            self.assertIsNone(imaging["Map Current Status"])
            self.assertEqual(imaging["Image Path"], IMAGE_ARTIFACT_RELATIVE_PATH)
            self.assertEqual(imaging["Map Path"], MAP_ARTIFACT_RELATIVE_PATH)
            self.assertEqual(
                imaging["Acquisition Source Evidence"],
                "Not recorded",
            )

            self.assertTrue(integrity["Fingerprint Present"])
            self.assertTrue(integrity["Canonical Acquisition Complete"])
            self.assertEqual(
                integrity["Fingerprint Path"],
                FINGERPRINT_ARTIFACT_RELATIVE_PATH,
            )
            self.assertEqual(integrity["Algorithm"], "SHA-256")
            self.assertEqual(integrity["SHA-256 Digest"], "abc123")
            self.assertEqual(integrity["Fingerprinted Image"], "source.img")
            self.assertEqual(integrity["Image Size (Bytes)"], 1)
            self.assertEqual(
                integrity["Fingerprint Timestamp"],
                "2026-07-16 10:00:00",
            )
            self.assertNotIn("Fingerprint Evidence", integrity)
            self.assertEqual(
                list(integrity.keys()),
                list(INTEGRITY_VERIFICATION_FIELDS)
                + list(INTEGRITY_FINGERPRINT_FIELDS),
            )

    def test_build_technician_markdown_imaging_sections_field_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            report = Hermes(_session(case_dir)).build_technician_report()
            markdown = Hermes(_session(case_dir)).build_technician_markdown()

            expected_imaging_keys = list(IMAGING_DETAILS_FIELDS) + [
                "Acquisition Source Evidence"
            ]
            self.assertEqual(
                list(report["Imaging Details"].keys()),
                expected_imaging_keys,
            )

            expected_integrity_keys = list(INTEGRITY_VERIFICATION_FIELDS) + [
                "Fingerprint Evidence"
            ]
            self.assertEqual(
                list(report["Integrity Verification"].keys()),
                expected_integrity_keys,
            )

            imaging_heading = markdown.index("## Imaging Details")
            integrity_heading = markdown.index("## Integrity Verification")
            self.assertLess(imaging_heading, integrity_heading)

            imaging_section = markdown[imaging_heading:integrity_heading]
            imaging_offsets = [
                imaging_section.index(f"{field}: ")
                for field in expected_imaging_keys
            ]
            self.assertEqual(imaging_offsets, sorted(imaging_offsets))

            statistics_heading = markdown.index("## Recovery Statistics")
            integrity_section = markdown[integrity_heading:statistics_heading]
            integrity_offsets = [
                integrity_section.index(f"{field}: ")
                for field in expected_integrity_keys
            ]
            self.assertEqual(integrity_offsets, sorted(integrity_offsets))


    def test_recovery_statistics_populated_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(
                case_dir,
                _minimal_manifest(
                    recovery_operations=[_recovery_operation("COMPLETED")]
                ),
            )
            _write_recovered_artifacts(case_dir)

            report = Hermes(_session(case_dir)).build_technician_report()
            statistics = report["Recovery Statistics"]

            self.assertEqual(
                list(statistics.keys()),
                list(RECOVERY_STATISTICS_FIELDS),
            )
            self.assertEqual(statistics["Recovery Attempt Recorded"], "Yes")
            self.assertEqual(statistics["Recovered File Count"], 2)
            self.assertEqual(statistics["Recovered Directory Count"], 1)
            self.assertEqual(statistics["Recovered Size (Bytes)"], 8)
            self.assertEqual(
                statistics["Recovered Output Locations"],
                "recovered/recup.1",
            )

    def test_recovery_statistics_empty_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            report = Hermes(_session(case_dir)).build_technician_report()
            statistics = report["Recovery Statistics"]

            self.assertEqual(
                list(statistics.keys()),
                list(RECOVERY_STATISTICS_FIELDS),
            )
            self.assertEqual(statistics["Recovery Attempt Recorded"], "No")
            self.assertEqual(statistics["Recovered File Count"], 0)
            self.assertEqual(statistics["Recovered Directory Count"], 0)
            self.assertEqual(statistics["Recovered Size (Bytes)"], 0)
            self.assertEqual(
                statistics["Recovered Output Locations"],
                "None recorded",
            )

    def test_recovery_attempt_recorded_completed_with_zero_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(
                case_dir,
                _minimal_manifest(
                    recovery_operations=[_recovery_operation("COMPLETED")]
                ),
            )

            statistics = (
                Hermes(_session(case_dir))
                .build_technician_report()["Recovery Statistics"]
            )

            # Completed execution that recovered nothing is still a recorded
            # attempt; artifact count stays a separate, observational fact.
            self.assertEqual(statistics["Recovery Attempt Recorded"], "Yes")
            self.assertEqual(statistics["Recovered File Count"], 0)

    def test_recovery_attempt_recorded_failed_with_zero_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(
                case_dir,
                _minimal_manifest(
                    recovery_operations=[_recovery_operation("FAILED")]
                ),
            )

            statistics = (
                Hermes(_session(case_dir))
                .build_technician_report()["Recovery Statistics"]
            )

            self.assertEqual(statistics["Recovery Attempt Recorded"], "Yes")
            self.assertEqual(statistics["Recovered File Count"], 0)

    def test_recovery_attempt_recorded_interrupted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(
                case_dir,
                _minimal_manifest(
                    recovery_operations=[_recovery_operation("INTERRUPTED")]
                ),
            )

            statistics = (
                Hermes(_session(case_dir))
                .build_technician_report()["Recovery Statistics"]
            )

            self.assertEqual(statistics["Recovery Attempt Recorded"], "Yes")

    def test_recovery_attempt_recorded_running(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(
                case_dir,
                _minimal_manifest(
                    recovery_operations=[_recovery_operation("RUNNING")]
                ),
            )

            statistics = (
                Hermes(_session(case_dir))
                .build_technician_report()["Recovery Statistics"]
            )

            self.assertEqual(statistics["Recovery Attempt Recorded"], "Yes")

    def test_recovery_attempt_recorded_legacy_artifacts_without_operations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            # Recovered artifacts exist on disk but no operation is recorded.
            # The field must not infer an attempt from artifact presence.
            _write_recovered_artifacts(case_dir)

            statistics = (
                Hermes(_session(case_dir))
                .build_technician_report()["Recovery Statistics"]
            )

            self.assertEqual(statistics["Recovery Attempt Recorded"], "No")
            self.assertEqual(statistics["Recovered File Count"], 2)

    def test_recovery_attempt_recorded_absent_field(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            # Legacy manifest without a recovery_operations key at all.
            _write_manifest(case_dir, _minimal_manifest())

            statistics = (
                Hermes(_session(case_dir))
                .build_technician_report()["Recovery Statistics"]
            )

            self.assertEqual(statistics["Recovery Attempt Recorded"], "No")

    def test_recovery_attempt_recorded_empty_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(
                case_dir, _minimal_manifest(recovery_operations=[])
            )

            statistics = (
                Hermes(_session(case_dir))
                .build_technician_report()["Recovery Statistics"]
            )

            self.assertEqual(statistics["Recovery Attempt Recorded"], "No")

    def test_recovery_statistics_uses_owner_api_not_filesystem(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            # Real recovered artifacts on disk that MUST be ignored because
            # HERMES has to delegate to the owner API, not traverse itself.
            _write_recovered_artifacts(case_dir)

            owner_summary = {
                "recovered_file_count": 42,
                "recovered_directory_count": 3,
                "recovered_size_bytes": 123456,
                "recup_directories": [
                    "recovered/recup.1",
                    "recovered/recup.2",
                ],
                "recovery_present": True,
            }

            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.summarize_recovered_artifacts",
                return_value=owner_summary,
            ) as summarize_mock:
                report = Hermes(session).build_technician_report()

            summarize_mock.assert_called_once_with(session.recovery_path)

            statistics = report["Recovery Statistics"]
            # Even though the owner summary reports recovery_present=True and
            # real artifacts exist on disk, no operation is recorded, so the
            # authoritative field is "No". Counts still come from the owner API.
            self.assertEqual(statistics["Recovery Attempt Recorded"], "No")
            self.assertEqual(statistics["Recovered File Count"], 42)
            self.assertEqual(statistics["Recovered Directory Count"], 3)
            self.assertEqual(statistics["Recovered Size (Bytes)"], 123456)
            self.assertEqual(
                statistics["Recovered Output Locations"],
                ["recovered/recup.1", "recovered/recup.2"],
            )

    def test_recovery_statistics_markdown_renders_after_integrity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(
                case_dir,
                _minimal_manifest(
                    recovery_operations=[_recovery_operation("COMPLETED")]
                ),
            )
            _write_recovered_artifacts(case_dir)

            markdown = Hermes(_session(case_dir)).build_technician_markdown()

            integrity_heading = markdown.index("## Integrity Verification")
            statistics_heading = markdown.index("## Recovery Statistics")
            self.assertLess(integrity_heading, statistics_heading)

            statistics_section = markdown[statistics_heading:]
            self.assertIn("Recovery Attempt Recorded: Yes", statistics_section)
            self.assertIn("Recovered File Count: 2", statistics_section)
            self.assertIn("Recovered Directory Count: 1", statistics_section)
            self.assertIn("Recovered Size (Bytes): 8", statistics_section)
            self.assertIn(
                "Recovered Output Locations: recovered/recup.1",
                statistics_section,
            )

            statistics_offsets = [
                statistics_section.index(f"{field}: ")
                for field in RECOVERY_STATISTICS_FIELDS
            ]
            self.assertEqual(statistics_offsets, sorted(statistics_offsets))

    def test_recovery_statistics_single_location_renders_inline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            _write_recovered_artifacts(case_dir)

            markdown = Hermes(_session(case_dir)).build_technician_markdown()

            self.assertIn(
                "Recovered Output Locations: recovered/recup.1",
                markdown,
            )

    def test_recovery_statistics_multiple_locations_render_as_bullets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            owner_summary = {
                "recovered_file_count": 5,
                "recovered_directory_count": 3,
                "recovered_size_bytes": 999,
                "recup_directories": [
                    "recovered/recup.1",
                    "recovered/recup.2",
                    "recovered/recup.3",
                ],
                "recovery_present": True,
            }

            with mock.patch(
                "modules.hermes.summarize_recovered_artifacts",
                return_value=owner_summary,
            ):
                markdown = Hermes(_session(case_dir)).build_technician_markdown()

            self.assertIn(
                "Recovered Output Locations:\n"
                "- recovered/recup.1\n"
                "- recovered/recup.2\n"
                "- recovered/recup.3",
                markdown,
            )

    def test_recovery_statistics_none_renders_inline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            markdown = Hermes(_session(case_dir)).build_technician_markdown()

            self.assertIn(
                "Recovered Output Locations: None recorded",
                markdown,
            )

    def test_audit_timeline_multiple_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            _write_audit_log(case_dir, AUDIT_LOG_LINES)

            report = Hermes(_session(case_dir)).build_technician_report()

            self.assertEqual(
                report["Audit Timeline"]["Events"],
                list(AUDIT_LOG_LINES),
            )

    def test_audit_timeline_single_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            _write_audit_log(case_dir, (AUDIT_LOG_LINES[0],))

            hermes = Hermes(_session(case_dir))
            report = hermes.build_technician_report()
            markdown = hermes.build_technician_markdown()

            self.assertEqual(
                report["Audit Timeline"]["Events"],
                [AUDIT_LOG_LINES[0]],
            )
            self.assertIn(
                f"Events:\n- {AUDIT_LOG_LINES[0]}",
                markdown,
            )

    def test_audit_timeline_empty_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            _write_audit_log(case_dir)

            report = Hermes(_session(case_dir)).build_technician_report()

            self.assertEqual(
                report["Audit Timeline"]["Events"],
                "No audit events recorded",
            )

    def test_audit_timeline_missing_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            report = Hermes(_session(case_dir)).build_technician_report()

            self.assertEqual(
                report["Audit Timeline"]["Events"],
                "No audit events recorded",
            )

    def test_audit_timeline_unreadable_renders_placeholder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_audit_log",
                side_effect=AuditLogError("unreadable"),
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)
            self.assertEqual(
                report["Audit Timeline"]["Events"],
                "Present but unreadable",
            )

    def test_audit_timeline_uses_owner_api_with_recovery_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            # Real audit.log on disk that MUST be ignored because HERMES has
            # to delegate to the owner API, not open the file itself.
            _write_audit_log(case_dir, AUDIT_LOG_LINES)
            session = _session(case_dir)

            owner_events = ["OWNER LINE A", "OWNER LINE B"]

            with mock.patch(
                "modules.hermes.read_audit_log",
                return_value=owner_events,
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)
            self.assertEqual(
                report["Audit Timeline"]["Events"],
                owner_events,
            )

    def test_audit_timeline_placed_after_recovery_statistics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            _write_audit_log(case_dir, AUDIT_LOG_LINES)

            hermes = Hermes(_session(case_dir))
            report = hermes.build_technician_report()
            markdown = hermes.build_technician_markdown()

            section_keys = list(report.keys())
            self.assertEqual(
                section_keys.index("Audit Timeline"),
                section_keys.index("Recovery Statistics") + 1,
            )

            self.assertLess(
                markdown.index("## Recovery Statistics"),
                markdown.index("## Audit Timeline"),
            )

    def test_device_identity_smart_available_passed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            smart_evidence = {
                "present": True,
                "available": True,
                "relative_path": "evidence/source.smart.txt",
                "overall_health": "PASSED",
            }

            with mock.patch(
                "modules.hermes.read_smart_evidence",
                return_value=smart_evidence,
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            device = report["Device Identity"]
            self.assertEqual(device["SMART Available"], "Yes")
            self.assertEqual(device["SMART Overall Health"], "PASSED")
            self.assertNotIn("SMART Evidence", device)

    def test_device_identity_smart_available_unknown_health(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            smart_evidence = {
                "present": True,
                "available": True,
                "relative_path": "evidence/source.smart.txt",
                "overall_health": None,
            }

            with mock.patch(
                "modules.hermes.read_smart_evidence",
                return_value=smart_evidence,
            ):
                report = Hermes(session).build_technician_report()

            device = report["Device Identity"]
            self.assertEqual(device["SMART Available"], "Yes")
            self.assertEqual(device["SMART Overall Health"], "Not reported")
            self.assertNotIn("SMART Evidence", device)

    def test_device_identity_smart_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            smart_evidence = {
                "present": True,
                "available": False,
                "relative_path": "evidence/source.smart.txt",
                "overall_health": None,
            }

            with mock.patch(
                "modules.hermes.read_smart_evidence",
                return_value=smart_evidence,
            ):
                report = Hermes(session).build_technician_report()

            device = report["Device Identity"]
            self.assertEqual(device["SMART Available"], "No")
            self.assertNotIn("SMART Overall Health", device)
            self.assertNotIn("SMART Evidence", device)

    def test_device_identity_smart_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_smart_evidence",
                return_value=None,
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            device = report["Device Identity"]
            self.assertEqual(device["SMART Evidence"], "Not recorded")
            self.assertNotIn("SMART Available", device)
            self.assertNotIn("SMART Overall Health", device)

    def test_device_identity_smart_malformed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_smart_evidence",
                side_effect=SmartEvidenceError("malformed"),
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            device = report["Device Identity"]
            self.assertEqual(device["SMART Evidence"], "Present but unreadable")
            self.assertNotIn("SMART Available", device)
            self.assertNotIn("SMART Overall Health", device)

    def test_device_identity_smart_markdown_renders_after_device_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            session = _session(case_dir)

            smart_evidence = {
                "present": True,
                "available": True,
                "relative_path": "evidence/source.smart.txt",
                "overall_health": "PASSED",
            }

            with mock.patch(
                "modules.hermes.read_smart_evidence",
                return_value=smart_evidence,
            ):
                markdown = Hermes(session).build_technician_markdown()

            device_heading = markdown.index("## Device Identity")
            assessment_heading = markdown.index("## Assessment Results")
            device_section = markdown[device_heading:assessment_heading]

            self.assertIn("SMART Available: Yes", device_section)
            self.assertIn("SMART Overall Health: PASSED", device_section)

            available_offset = device_section.index("SMART Available: ")
            source_path_offset = device_section.index("Source Path: ")
            self.assertLess(source_path_offset, available_offset)

    def test_imaging_details_valid_acquisition_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_acquisition_source",
                return_value=dict(ACQUISITION_SOURCE),
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            imaging = report["Imaging Details"]
            self.assertEqual(imaging["Logical Sector Size"], 512)
            self.assertEqual(imaging["Physical Sector Size"], 4096)
            self.assertEqual(
                imaging["Acquisition Timestamp"],
                "2026-07-16T10:00:00",
            )
            self.assertNotIn("Acquisition Source Evidence", imaging)
            # Model, Serial, and Device Size must never be duplicated here.
            self.assertNotIn("Model", imaging)
            self.assertNotIn("Serial", imaging)
            self.assertNotIn("Device Size", imaging)
            self.assertEqual(
                list(imaging.keys()),
                list(IMAGING_DETAILS_FIELDS)
                + list(IMAGING_ACQUISITION_SOURCE_FIELDS),
            )

    def test_imaging_details_missing_acquisition_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_acquisition_source",
                return_value=None,
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            imaging = report["Imaging Details"]
            self.assertEqual(
                imaging["Acquisition Source Evidence"],
                "Not recorded",
            )
            for field in IMAGING_ACQUISITION_SOURCE_FIELDS:
                self.assertNotIn(field, imaging)

    def test_imaging_details_malformed_acquisition_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_acquisition_source",
                side_effect=AcquisitionSourceError("malformed"),
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            imaging = report["Imaging Details"]
            self.assertEqual(
                imaging["Acquisition Source Evidence"],
                "Present but unreadable",
            )
            for field in IMAGING_ACQUISITION_SOURCE_FIELDS:
                self.assertNotIn(field, imaging)

    def test_imaging_details_markdown_renders_acquisition_source_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_acquisition_source",
                return_value=dict(ACQUISITION_SOURCE),
            ):
                markdown = Hermes(session).build_technician_markdown()

            imaging_heading = markdown.index("## Imaging Details")
            integrity_heading = markdown.index("## Integrity Verification")
            imaging_section = markdown[imaging_heading:integrity_heading]

            self.assertIn("Logical Sector Size: 512", imaging_section)
            self.assertIn("Physical Sector Size: 4096", imaging_section)
            self.assertIn(
                "Acquisition Timestamp: 2026-07-16T10:00:00",
                imaging_section,
            )
            self.assertNotIn("Acquisition Source Evidence:", imaging_section)

    def test_integrity_verification_valid_fingerprint_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_fingerprint_evidence",
                return_value=dict(FINGERPRINT_EVIDENCE),
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            integrity = report["Integrity Verification"]
            self.assertEqual(integrity["Algorithm"], "SHA-256")
            self.assertEqual(integrity["SHA-256 Digest"], "abc123def456")
            self.assertEqual(integrity["Fingerprinted Image"], "source.img")
            self.assertEqual(integrity["Image Size (Bytes)"], 500107862016)
            self.assertEqual(
                integrity["Fingerprint Timestamp"],
                "2026-07-16 10:05:00",
            )
            self.assertNotIn("Fingerprint Evidence", integrity)
            self.assertEqual(
                list(integrity.keys()),
                list(INTEGRITY_VERIFICATION_FIELDS)
                + list(INTEGRITY_FINGERPRINT_FIELDS),
            )

    def test_integrity_verification_missing_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_fingerprint_evidence",
                return_value=None,
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            integrity = report["Integrity Verification"]
            self.assertEqual(integrity["Fingerprint Evidence"], "Not recorded")
            for field in INTEGRITY_FINGERPRINT_FIELDS:
                self.assertNotIn(field, integrity)

    def test_integrity_verification_malformed_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_fingerprint_evidence",
                side_effect=FingerprintEvidenceError("malformed"),
            ) as read_mock:
                report = Hermes(session).build_technician_report()

            read_mock.assert_called_once_with(session.recovery_path)

            integrity = report["Integrity Verification"]
            self.assertEqual(
                integrity["Fingerprint Evidence"],
                "Present but unreadable",
            )
            for field in INTEGRITY_FINGERPRINT_FIELDS:
                self.assertNotIn(field, integrity)

    def test_integrity_verification_markdown_renders_fingerprint_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            session = _session(case_dir)

            with mock.patch(
                "modules.hermes.read_fingerprint_evidence",
                return_value=dict(FINGERPRINT_EVIDENCE),
            ):
                markdown = Hermes(session).build_technician_markdown()

            integrity_heading = markdown.index("## Integrity Verification")
            statistics_heading = markdown.index("## Recovery Statistics")
            integrity_section = markdown[integrity_heading:statistics_heading]

            self.assertIn("Algorithm: SHA-256", integrity_section)
            self.assertIn("SHA-256 Digest: abc123def456", integrity_section)
            self.assertIn("Fingerprinted Image: source.img", integrity_section)
            self.assertIn("Image Size (Bytes): 500107862016", integrity_section)
            self.assertIn(
                "Fingerprint Timestamp: 2026-07-16 10:05:00",
                integrity_section,
            )
            self.assertNotIn("Fingerprint Evidence:", integrity_section)


class HermesCustomerReportTests(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()
        set_language("en", persist=False)
        self.datetime_patcher = mock.patch(
            "modules.hermes.datetime",
            wraps=datetime,
        )
        self.mock_datetime = self.datetime_patcher.start()
        self.mock_datetime.now.return_value = FIXED_GENERATED_AT

    def tearDown(self):
        self.datetime_patcher.stop()
        set_language(self._previous_language, persist=False)

    def _completed_manifest(self, session_id="REC-2026-000001", outcome="SUCCESSFUL"):
        manifest = _populated_manifest(session_id)
        manifest["completed_at"] = "2026-07-17T09:00:00"
        manifest["recovery_outcome"] = outcome
        manifest["intake"]["previous_recovery_attempts"] = (
            "Sent to another lab called DataMedic first"
        )
        return manifest

    def _work_performed_section(self, markdown):
        start = markdown.index("## Work Performed")
        end = markdown.index("## Recovery Outcome")
        return markdown[start:end]

    def test_customer_report_sections_and_field_mappings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, self._completed_manifest())

            report = Hermes(_session(case_dir)).build_customer_report()

            self.assertEqual(list(report.keys()), list(CUSTOMER_REPORT_SECTIONS))

            case_info = report["Case Information"]
            self.assertEqual(case_info["Case Number"], "REC-2026-000001")
            self.assertEqual(case_info["Customer Name"], "Jane Example")

            device = report["Device Received"]
            self.assertEqual(device["Device"], "Samsung SSD 860")
            # Capacity reuses the existing format_bytes helper over the recorded
            # size_bytes, producing natural units instead of the raw "500G".
            self.assertEqual(device["Capacity"], "465.8 GB")
            self.assertEqual(device["Number of Devices Received"], 1)

            problem = report["Problem Description"]
            self.assertEqual(
                problem["Requested Recovery"], "Recover family photos"
            )
            self.assertEqual(
                problem["What Happened"],
                "Drive stopped mounting after power loss",
            )
            self.assertEqual(
                problem["Most Important Data"], "Photos and documents"
            )

            self.assertEqual(
                report["Recommendations"]["Guidance"], CUSTOMER_RECOMMENDATIONS
            )
            self.assertEqual(
                report["Recommendations"]["Policy Version"],
                CUSTOMER_POLICY_VERSION,
            )
            self.assertEqual(report["Disclaimer"]["Terms"], CUSTOMER_DISCLAIMER)
            self.assertEqual(
                report["Disclaimer"]["Policy Version"], CUSTOMER_POLICY_VERSION
            )

    def test_recovery_outcome_neutral_wording(self):
        cases = {
            "SUCCESSFUL": "The requested data was recovered successfully.",
            "PARTIAL": "Some of the requested data was recovered.",
            "UNSUCCESSFUL": "The requested data could not be recovered.",
        }
        for outcome, wording in cases.items():
            with self.subTest(outcome=outcome):
                with tempfile.TemporaryDirectory() as temp_dir:
                    case_dir = _case_dir(temp_dir)
                    _write_manifest(
                        case_dir, self._completed_manifest(outcome=outcome)
                    )

                    report = Hermes(_session(case_dir)).build_customer_report()

                    self.assertEqual(
                        report["Recovery Outcome"]["Outcome"], wording
                    )

    def test_recovery_outcome_missing_is_neutral(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            report = Hermes(_session(case_dir)).build_customer_report()

            self.assertEqual(
                report["Recovery Outcome"]["Outcome"],
                "No recovery outcome has been recorded.",
            )

    def test_separate_completed_and_generated_timestamps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, self._completed_manifest())

            report = Hermes(_session(case_dir)).build_customer_report()
            case_info = report["Case Information"]

            self.assertEqual(case_info["Case Completed"], "2026-07-17T09:00:00")
            self.assertEqual(case_info["Report Generated"], FIXED_GENERATED_AT)
            self.assertNotEqual(
                case_info["Case Completed"], case_info["Report Generated"]
            )

    def test_destination_not_exposed_as_received(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, self._completed_manifest())

            hermes = Hermes(_session(case_dir))
            report = hermes.build_customer_report()
            markdown = hermes.build_customer_markdown()

            self.assertEqual(
                report["Device Received"]["Number of Devices Received"], 1
            )
            self.assertNotIn("WD Elements", markdown)
            self.assertNotIn("/dev/sdc", markdown)
            self.assertNotIn("WX12AB34CD56", markdown)
            self.assertNotIn("Destination", markdown)

    def test_internal_and_technical_fields_not_exposed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, self._completed_manifest())
            _write_canonical_acquisition_artifacts(case_dir)
            _write_recovered_artifacts(case_dir)

            markdown = Hermes(_session(case_dir)).build_customer_markdown()

            forbidden = [
                "/dev/sdb",
                "S4EWNF0M803123A",
                "SATA",
                "ext4",
                "APPROVED",
                "External device.",
                "LOW",
                "+49 170 0000000",
                "jane@example.com",
                "Sent to another lab called DataMedic first",
                "source.img",
                "SHA-256",
                "abc123",
                "PhotoRec",
                "photorec",
                "recup",
                "SMART",
                "audit",
            ]
            for term in forbidden:
                self.assertNotIn(term, markdown)

    def test_no_recovery_operation_claim_from_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            # Recovered artifacts exist on disk but no image and no operation
            # record. HERMES must not claim a recovery operation occurred.
            _write_recovered_artifacts(case_dir)

            hermes = Hermes(_session(case_dir))
            report = hermes.build_customer_report()
            markdown = hermes.build_customer_markdown()

            self.assertEqual(
                list(report["Work Performed"].keys()), ["Imaging"]
            )
            self.assertEqual(
                report["Work Performed"]["Imaging"],
                CUSTOMER_IMAGING_NOT_PERFORMED,
            )

            self.assertEqual(report["Files Recovered"]["Recovered Items"], 2)

            work_section = self._work_performed_section(markdown)
            self.assertNotIn("PhotoRec", work_section)
            self.assertNotIn("recovery operation", work_section.lower())
            self.assertNotIn("recovered", work_section.lower())

    def test_work_performed_reports_canonical_imaging(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, self._completed_manifest())
            _write_canonical_acquisition_artifacts(case_dir)

            report = Hermes(_session(case_dir)).build_customer_report()

            self.assertEqual(
                report["Work Performed"]["Imaging"], CUSTOMER_IMAGING_COMPLETED
            )

    def test_work_performed_all_acquisition_states_distinguishable(self):
        # Every authoritative acquisition state must map to exactly one of the
        # three neutral customer-facing imaging statements.
        state_expectations = {
            "no_acquisition": CUSTOMER_IMAGING_NOT_PERFORMED,
            "inconsistent_artifacts": CUSTOMER_IMAGING_NOT_COMPLETED,
            "invalid_map": CUSTOMER_IMAGING_NOT_COMPLETED,
            "imaging_complete_fingerprint_missing": (
                CUSTOMER_IMAGING_NOT_COMPLETED
            ),
            "incomplete_ddrescue": CUSTOMER_IMAGING_NOT_COMPLETED,
            "completed_canonical": CUSTOMER_IMAGING_COMPLETED,
        }

        for state, expected in state_expectations.items():
            with self.subTest(state=state):
                with tempfile.TemporaryDirectory() as temp_dir:
                    case_dir = _case_dir(temp_dir)
                    _write_manifest(case_dir, self._completed_manifest())
                    session = _session(case_dir)

                    with mock.patch(
                        "modules.hermes.classify_acquisition_state",
                        return_value={"state": state},
                    ) as classify_mock:
                        report = Hermes(session).build_customer_report()

                    classify_mock.assert_called_once_with(session.recovery_path)
                    self.assertEqual(
                        report["Work Performed"]["Imaging"], expected
                    )

        # The three customer-facing statements are themselves distinct.
        self.assertEqual(
            len(
                {
                    CUSTOMER_IMAGING_COMPLETED,
                    CUSTOMER_IMAGING_NOT_COMPLETED,
                    CUSTOMER_IMAGING_NOT_PERFORMED,
                }
            ),
            3,
        )

    def test_missing_data_placeholders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())

            report = Hermes(_session(case_dir)).build_customer_report()

            case_info = report["Case Information"]
            self.assertEqual(case_info["Case Number"], "REC-2026-000001")
            self.assertEqual(case_info["Customer Name"], "Not recorded")
            self.assertEqual(case_info["Case Completed"], "Not recorded")

            device = report["Device Received"]
            self.assertEqual(device["Device"], "Not recorded")
            self.assertEqual(device["Capacity"], "Not recorded")
            self.assertEqual(device["Number of Devices Received"], 0)

            problem = report["Problem Description"]
            self.assertEqual(problem["Requested Recovery"], "Not recorded")
            self.assertEqual(problem["What Happened"], "Not recorded")
            self.assertEqual(problem["Most Important Data"], "Not recorded")

            self.assertEqual(
                report["Recovery Outcome"]["Outcome"],
                "No recovery outcome has been recorded.",
            )
            self.assertEqual(
                report["Work Performed"]["Imaging"],
                CUSTOMER_IMAGING_NOT_PERFORMED,
            )
            self.assertEqual(report["Files Recovered"]["Recovered Items"], 0)
            self.assertEqual(report["Files Recovered"]["Recovered Data"], "0 B")

    def test_build_report_customer_dispatches_to_customer_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, self._completed_manifest())
            hermes = Hermes(_session(case_dir))

            self.assertEqual(
                hermes.build_report("customer"), hermes.build_customer_report()
            )

    def test_refuse_overwrite_existing_customer_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, self._completed_manifest())
            hermes = Hermes(_session(case_dir))
            hermes.save_customer_report()

            with self.assertRaises(FileExistsError):
                hermes.save_customer_report()

    def test_customer_report_saved_at_correct_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, self._completed_manifest())
            hermes = Hermes(_session(case_dir))

            report_path = hermes.save_customer_report()

            expected_path = case_dir / "reports" / customer_report_filename("en")
            self.assertEqual(report_path, expected_path)
            self.assertTrue(report_path.is_file())
            self.assertEqual(
                report_path.read_text(encoding="utf-8"),
                hermes.build_customer_markdown(),
            )


if __name__ == "__main__":
    unittest.main()
