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
from modules.summary import format_bytes

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

CUSTOMER_REPORT_FILENAME = "customer_report.md"

CUSTOMER_REPORT_SECTIONS = (
    "Case Information",
    "Device Received",
    "Problem Description",
    "Work Performed",
    "Recovery Outcome",
    "Files Recovered",
    "Recommendations",
    "Disclaimer",
)

CUSTOMER_NOT_RECORDED = "Not recorded"

CUSTOMER_OUTCOME_NOT_RECORDED = "No recovery outcome has been recorded."

# Neutral, definition-only wording. HERMES does not infer reasons, quality,
# percentages, or case-specific limitations from the recorded outcome.
CUSTOMER_OUTCOME_WORDING = {
    "SUCCESSFUL": "The requested data was recovered successfully.",
    "PARTIAL": "Some of the requested data was recovered.",
    "UNSUCCESSFUL": "The requested data could not be recovered.",
}

# Three neutral customer-facing imaging states derived from the authoritative
# acquisition state. Only completed_canonical is the trusted complete state;
# no_acquisition means imaging was not performed; every other authoritative
# state means imaging was not completed. HERMES adds no detail beyond this.
CUSTOMER_IMAGING_COMPLETED = (
    "A complete forensic image of the device was created."
)
CUSTOMER_IMAGING_NOT_COMPLETED = (
    "The forensic image of the device was not completed."
)
CUSTOMER_IMAGING_NOT_PERFORMED = (
    "No forensic image of the device was created."
)

# Versioned, HERMES-owned policy content. This is not sourced from case.json.
CUSTOMER_POLICY_VERSION = "1.0"

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


def _customer_value(value):
    return value if value is not None else CUSTOMER_NOT_RECORDED


def _neutral_outcome(outcome):
    if outcome is None:
        return CUSTOMER_OUTCOME_NOT_RECORDED
    return CUSTOMER_OUTCOME_WORDING.get(outcome, CUSTOMER_OUTCOME_NOT_RECORDED)


def _customer_imaging(state):
    if state == "completed_canonical":
        return CUSTOMER_IMAGING_COMPLETED
    if state == "no_acquisition":
        return CUSTOMER_IMAGING_NOT_PERFORMED
    return CUSTOMER_IMAGING_NOT_COMPLETED


def _customer_capacity(manifest):
    size_bytes = _manifest_field(manifest, "device", "size_bytes")
    if isinstance(size_bytes, int) and not isinstance(size_bytes, bool) and size_bytes > 0:
        return format_bytes(size_bytes)
    return _customer_value(_manifest_field(manifest, "device", "size"))


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

    def _build_customer_case_information(self, manifest, generated_at):
        return {
            "Case Number": _customer_value(
                _manifest_field(manifest, "session_id")
            ),
            "Customer Name": _customer_value(
                _manifest_field(manifest, "case_contact", "name")
            ),
            "Case Completed": _customer_value(
                _manifest_field(manifest, "completed_at")
            ),
            "Report Generated": generated_at,
        }

    def _build_device_received(self, manifest):
        device = manifest.get("device")
        device_present = isinstance(device, dict) and bool(device)

        return {
            "Device": _customer_value(
                _manifest_field(manifest, "device", "model")
            ),
            "Capacity": _customer_capacity(manifest),
            "Number of Devices Received": 1 if device_present else 0,
        }

    def _build_problem_description(self, manifest):
        return {
            "Requested Recovery": _customer_value(
                _manifest_field(manifest, "intake", "recovery_request")
            ),
            "What Happened": _customer_value(
                _manifest_field(manifest, "intake", "incident_description")
            ),
            "Most Important Data": _customer_value(
                _manifest_field(manifest, "intake", "data_priority")
            ),
        }

    def _build_customer_work_performed(self, acquisition_state):
        return {
            "Imaging": _customer_imaging(acquisition_state.get("state")),
        }

    def _build_customer_recovery_outcome(self, manifest):
        outcome = _manifest_field(manifest, "recovery_outcome")
        return {
            "Outcome": _neutral_outcome(outcome),
        }

    def _build_files_recovered(self, recovered_summary):
        return {
            "Recovered Items": recovered_summary["recovered_file_count"],
            "Recovered Data": format_bytes(
                recovered_summary["recovered_size_bytes"]
            ),
        }

    def _build_customer_recommendations(self):
        return {
            "Guidance": CUSTOMER_RECOMMENDATIONS,
            "Policy Version": CUSTOMER_POLICY_VERSION,
        }

    def _build_customer_disclaimer(self):
        return {
            "Terms": CUSTOMER_DISCLAIMER,
            "Policy Version": CUSTOMER_POLICY_VERSION,
        }

    def build_customer_report(self):
        """
        Build the customer report for the current recovery session.

        The customer report presents only customer-visible facts. Work
        Performed states authoritative imaging facts only; it never infers a
        recovery operation from recovered artifacts. Recovered-file figures are
        observational and read through the owning ARCHIVE summary API.
        """
        manifest = self._load_manifest()
        generated_at = datetime.now()
        acquisition_state = self._load_acquisition_state()
        recovered_summary = self._load_recovered_summary()

        return {
            "Case Information": self._build_customer_case_information(
                manifest, generated_at
            ),
            "Device Received": self._build_device_received(manifest),
            "Problem Description": self._build_problem_description(manifest),
            "Work Performed": self._build_customer_work_performed(
                acquisition_state
            ),
            "Recovery Outcome": self._build_customer_recovery_outcome(manifest),
            "Files Recovered": self._build_files_recovered(recovered_summary),
            "Recommendations": self._build_customer_recommendations(),
            "Disclaimer": self._build_customer_disclaimer(),
        }

    def build_customer_markdown(self):
        """
        Build a Markdown representation of the customer report.
        """
        report = self.build_customer_report()
        return ReportFormatter().format_markdown(
            "Customer Report",
            report,
            section_order=CUSTOMER_REPORT_SECTIONS,
        )

    def save_customer_report(self) -> Path:
        """
        Write the customer report as Markdown into the case reports directory.

        Creates the reports directory when it does not exist. Raises
        FileExistsError when customer_report.md is already present.
        """
        reports_dir = Path(self.session.recovery_path) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        report_path = reports_dir / CUSTOMER_REPORT_FILENAME
        if report_path.exists():
            raise FileExistsError(
                f"Customer report already exists: {report_path}"
            )

        report_path.write_text(self.build_customer_markdown(), encoding="utf-8")
        return report_path

    def build_partner_report(self):
        """
        Build the partner report for the current recovery session.
        """
        raise NotImplementedError
