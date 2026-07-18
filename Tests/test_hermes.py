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
from modules.argus import SmartEvidenceError
from modules.hermes import (
    FINGERPRINT_ARTIFACT_RELATIVE_PATH,
    Hermes,
    IMAGE_ARTIFACT_RELATIVE_PATH,
    MAP_ARTIFACT_RELATIVE_PATH,
    TECHNICIAN_REPORT_FILENAME,
    TECHNICIAN_REPORT_SECTIONS,
)
from modules.manifest import ManifestError
from modules.report_formatter import ReportFormatter

FIXED_GENERATED_AT = datetime(2026, 7, 16, 12, 30, 0)

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
    "Recovery Present",
    "Recovered File Count",
    "Recovered Directory Count",
    "Recovered Size (Bytes)",
    "Recovered Output Locations",
)

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


class HermesTests(unittest.TestCase):
    def setUp(self):
        self.datetime_patcher = mock.patch(
            "modules.hermes.datetime",
            wraps=datetime,
        )
        self.mock_datetime = self.datetime_patcher.start()
        self.mock_datetime.now.return_value = FIXED_GENERATED_AT

    def tearDown(self):
        self.datetime_patcher.stop()

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

            expected_path = case_dir / "reports" / TECHNICIAN_REPORT_FILENAME
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
            _write_manifest(case_dir, _minimal_manifest())
            _write_recovered_artifacts(case_dir)

            report = Hermes(_session(case_dir)).build_technician_report()
            statistics = report["Recovery Statistics"]

            self.assertEqual(
                list(statistics.keys()),
                list(RECOVERY_STATISTICS_FIELDS),
            )
            self.assertEqual(statistics["Recovery Present"], "Yes")
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
            self.assertEqual(statistics["Recovery Present"], "No")
            self.assertEqual(statistics["Recovered File Count"], 0)
            self.assertEqual(statistics["Recovered Directory Count"], 0)
            self.assertEqual(statistics["Recovered Size (Bytes)"], 0)
            self.assertEqual(
                statistics["Recovered Output Locations"],
                "None recorded",
            )

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
            self.assertEqual(statistics["Recovery Present"], "Yes")
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
            _write_manifest(case_dir, _minimal_manifest())
            _write_recovered_artifacts(case_dir)

            markdown = Hermes(_session(case_dir)).build_technician_markdown()

            integrity_heading = markdown.index("## Integrity Verification")
            statistics_heading = markdown.index("## Recovery Statistics")
            self.assertLess(integrity_heading, statistics_heading)

            statistics_section = markdown[statistics_heading:]
            self.assertIn("Recovery Present: Yes", statistics_section)
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


if __name__ == "__main__":
    unittest.main()
