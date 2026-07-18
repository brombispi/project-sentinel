"""
HERMES reporting module.

HERMES is a presentation module only. It reads a Recovery Case and
formats existing case information into readable reports. HERMES does
not observe devices, assess safety, execute operations, modify case
state, or perform business logic.
"""

from datetime import datetime
from pathlib import Path

from core.session import RecoverySession
from modules.archive import (
    IMAGE_FILENAME,
    MAP_FILENAME,
    SHA256_FILENAME,
    AcquisitionSourceError,
    FingerprintEvidenceError,
    classify_acquisition_state,
    read_acquisition_source,
    read_fingerprint_evidence,
    summarize_recovered_artifacts,
)
from modules.argus import SmartEvidenceError, read_smart_evidence
from modules.echo import AuditLogError, read_audit_log
from modules.manifest import read_case_manifest
from modules.report_formatter import ReportFormatter

TECHNICIAN_REPORT_FILENAME = "technician_report.md"

TECHNICIAN_REPORT_SECTIONS = (
    "Case Information",
    "Customer Information",
    "Intake Summary",
    "Device Identity",
    "Assessment Results",
    "Imaging Details",
    "Integrity Verification",
    "Recovery Statistics",
    "Audit Timeline",
)

IMAGE_ARTIFACT_RELATIVE_PATH = f"images/{IMAGE_FILENAME}"
MAP_ARTIFACT_RELATIVE_PATH = f"images/{MAP_FILENAME}"
FINGERPRINT_ARTIFACT_RELATIVE_PATH = f"evidence/{SHA256_FILENAME}"


def _coerce_display_value(value):
    if value is None:
        return None

    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None

    return value


def _manifest_field(manifest, *keys):
    current = manifest

    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    return _coerce_display_value(current)


def _device_fields(manifest, block_key, prefix):
    device = manifest.get(block_key)
    if not isinstance(device, dict):
        return {
            f"{prefix} Path": None,
            f"{prefix} Model": None,
            f"{prefix} Serial": None,
            f"{prefix} Size": None,
            f"{prefix} Size (Bytes)": None,
            f"{prefix} Transport": None,
            f"{prefix} Filesystem": None,
            f"{prefix} Role": None,
        }

    return {
        f"{prefix} Path": _coerce_display_value(device.get("path")),
        f"{prefix} Model": _coerce_display_value(device.get("model")),
        f"{prefix} Serial": _coerce_display_value(device.get("serial")),
        f"{prefix} Size": _coerce_display_value(device.get("size")),
        f"{prefix} Size (Bytes)": _coerce_display_value(device.get("size_bytes")),
        f"{prefix} Transport": _coerce_display_value(device.get("transport")),
        f"{prefix} Filesystem": _coerce_display_value(device.get("filesystem")),
        f"{prefix} Role": _coerce_display_value(device.get("role")),
    }


class Hermes:
    """
    Presentation layer for recovery case reporting.

    HERMES accepts a RecoverySession and exposes report builders for
    different audiences. Callers should use build_report() as the
    primary entry point. It gathers and formats recorded case data;
    it does not decide, assess, or recover.
    """

    def __init__(self, session: RecoverySession):
        self.session = session

    def build_report(self, report_type: str):
        """
        Build a report for the given report type.

        Supported report types: technician, customer, partner.
        """
        builders = {
            "technician": self.build_technician_report,
            "customer": self.build_customer_report,
            "partner": self.build_partner_report,
        }

        try:
            builder = builders[report_type]
        except KeyError:
            raise ValueError(f"Unsupported report type: {report_type}") from None

        return builder()

    def _load_manifest(self):
        return read_case_manifest(Path(self.session.recovery_path))

    def _build_case_information(self, manifest, generated_at):
        return {
            "Case Number": _manifest_field(manifest, "session_id"),
            "Case Name": _manifest_field(manifest, "case_name"),
            "Creation Date": _manifest_field(manifest, "created_at"),
            "Current Status": _manifest_field(manifest, "status"),
            "Report Generation Date": generated_at,
        }

    def _build_customer_information(self, manifest):
        return {
            "Name": _manifest_field(manifest, "case_contact", "name"),
            "Telephone": _manifest_field(manifest, "case_contact", "phone"),
            "Email": _manifest_field(manifest, "case_contact", "email"),
        }

    def _build_intake_summary(self, manifest):
        return {
            "Requested Recovery": _manifest_field(
                manifest, "intake", "recovery_request"
            ),
            "Incident Description": _manifest_field(
                manifest, "intake", "incident_description"
            ),
            "Previous Recovery Attempts": _manifest_field(
                manifest, "intake", "previous_recovery_attempts"
            ),
            "Data Priority": _manifest_field(manifest, "intake", "data_priority"),
        }

    def _build_device_identity(self, manifest):
        fields = {
            **_device_fields(manifest, "device", "Source"),
            **_device_fields(manifest, "destination", "Destination"),
        }

        try:
            smart = read_smart_evidence(self.session.recovery_path)
        except SmartEvidenceError:
            fields["SMART Evidence"] = "Present but unreadable"
            return fields

        if smart is None:
            fields["SMART Evidence"] = "Not recorded"
            return fields

        if not smart.get("available"):
            fields["SMART Available"] = "No"
            return fields

        fields["SMART Available"] = "Yes"
        health = smart.get("overall_health")
        fields["SMART Overall Health"] = health if health else "Not reported"
        return fields

    def _build_assessment_results(self, manifest):
        return {
            "Decision": _manifest_field(manifest, "assessment", "decision"),
            "Reason": _manifest_field(manifest, "assessment", "reason"),
            "Risk": _manifest_field(manifest, "assessment", "risk"),
            "Confidence": _manifest_field(manifest, "assessment", "confidence"),
        }

    def _load_acquisition_state(self):
        return classify_acquisition_state(self.session.recovery_path)

    def _build_imaging_details(self, acquisition_state):
        fields = {
            "Acquisition State": _coerce_display_value(acquisition_state.get("state")),
            "Acquisition State Code": _coerce_display_value(
                acquisition_state.get("code")
            ),
            "Image Present": acquisition_state.get("image_exists"),
            "Map Present": acquisition_state.get("map_exists"),
            "Map Status": _coerce_display_value(acquisition_state.get("map_status")),
            "Map Current Status": _coerce_display_value(
                acquisition_state.get("current_status")
            ),
            "Image Path": IMAGE_ARTIFACT_RELATIVE_PATH,
            "Map Path": MAP_ARTIFACT_RELATIVE_PATH,
        }

        try:
            acquisition_source = read_acquisition_source(self.session.recovery_path)
        except AcquisitionSourceError:
            fields["Acquisition Source Evidence"] = "Present but unreadable"
            return fields

        if acquisition_source is None:
            fields["Acquisition Source Evidence"] = "Not recorded"
            return fields

        fields["Logical Sector Size"] = acquisition_source.get("logical_sector_size")
        fields["Physical Sector Size"] = acquisition_source.get(
            "physical_sector_size"
        )
        fields["Acquisition Timestamp"] = acquisition_source.get("timestamp")
        return fields

    def _build_integrity_verification(self, acquisition_state):
        fields = {
            "Fingerprint Present": acquisition_state.get("sha256_exists"),
            "Canonical Acquisition Complete": (
                acquisition_state.get("state") == "completed_canonical"
            ),
            "Fingerprint Path": FINGERPRINT_ARTIFACT_RELATIVE_PATH,
        }

        try:
            evidence = read_fingerprint_evidence(self.session.recovery_path)
        except FingerprintEvidenceError:
            fields["Fingerprint Evidence"] = "Present but unreadable"
            return fields

        if evidence is None:
            fields["Fingerprint Evidence"] = "Not recorded"
            return fields

        fields["Algorithm"] = evidence["algorithm"]
        fields["SHA-256 Digest"] = evidence["digest"]
        fields["Fingerprinted Image"] = evidence["image_filename"]
        fields["Image Size (Bytes)"] = evidence["image_size_bytes"]
        fields["Fingerprint Timestamp"] = evidence["timestamp"]
        return fields

    def _load_recovered_summary(self):
        return summarize_recovered_artifacts(self.session.recovery_path)

    def _build_recovery_statistics(self, recovered_summary):
        locations = recovered_summary["recup_directories"]

        if not locations:
            output_locations = "None recorded"
        elif len(locations) == 1:
            output_locations = locations[0]
        else:
            output_locations = list(locations)

        return {
            "Recovery Present": (
                "Yes" if recovered_summary["recovery_present"] else "No"
            ),
            "Recovered File Count": recovered_summary["recovered_file_count"],
            "Recovered Directory Count": recovered_summary[
                "recovered_directory_count"
            ],
            "Recovered Size (Bytes)": recovered_summary["recovered_size_bytes"],
            "Recovered Output Locations": output_locations,
        }

    def _build_audit_timeline(self):
        try:
            events = read_audit_log(self.session.recovery_path)
        except AuditLogError:
            return {"Events": "Present but unreadable"}

        if not events:
            return {"Events": "No audit events recorded"}

        return {"Events": events}

    def build_technician_report(self):
        """
        Build the technician report for the current recovery session.
        """
        manifest = self._load_manifest()
        generated_at = datetime.now()
        acquisition_state = self._load_acquisition_state()
        recovered_summary = self._load_recovered_summary()

        return {
            "Case Information": self._build_case_information(manifest, generated_at),
            "Customer Information": self._build_customer_information(manifest),
            "Intake Summary": self._build_intake_summary(manifest),
            "Device Identity": self._build_device_identity(manifest),
            "Assessment Results": self._build_assessment_results(manifest),
            "Imaging Details": self._build_imaging_details(acquisition_state),
            "Integrity Verification": self._build_integrity_verification(
                acquisition_state
            ),
            "Recovery Statistics": self._build_recovery_statistics(
                recovered_summary
            ),
            "Audit Timeline": self._build_audit_timeline(),
        }

    def build_technician_markdown(self):
        """
        Build a Markdown representation of the technician report.
        """
        report = self.build_technician_report()
        return ReportFormatter().format_markdown(
            "Technician Report",
            report,
            section_order=TECHNICIAN_REPORT_SECTIONS,
        )

    def save_technician_report(self) -> Path:
        """
        Write the technician report as Markdown into the case reports directory.

        Creates the reports directory when it does not exist. Raises
        FileExistsError when technician_report.md is already present.
        """
        reports_dir = Path(self.session.recovery_path) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        report_path = reports_dir / TECHNICIAN_REPORT_FILENAME
        if report_path.exists():
            raise FileExistsError(
                f"Technician report already exists: {report_path}"
            )

        report_path.write_text(self.build_technician_markdown(), encoding="utf-8")
        return report_path

    def build_customer_report(self):
        """
        Build the customer report for the current recovery session.
        """
        raise NotImplementedError

    def build_partner_report(self):
        """
        Build the partner report for the current recovery session.
        """
        raise NotImplementedError
