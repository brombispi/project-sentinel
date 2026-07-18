"""
HERMES reporting module.

HERMES is a presentation module only. It reads a Recovery Case and
formats existing case information into readable reports. HERMES does
not observe devices, assess safety, execute operations, modify case
state, or perform business logic.

Report prose (titles, section headings, field labels, presentational
values, placeholders, customer sentences, recommendations, and the
disclaimer) is localized through the i18n Translator using an explicit,
per-report language. Recorded case facts (identifiers, timestamps,
paths, hashes, byte sizes, filesystem names, acquisition state codes,
and raw audit lines) are never translated. Rendering never mutates the
process-global UI language and never writes translated prose into
case.json.
"""

from datetime import datetime
from pathlib import Path

from core.session import RecoverySession
from i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, get_language, translate
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
from modules.pdf_report_formatter import PdfReportFormatter
from modules.report_formatter import ReportFormatter
from modules.summary import format_bytes

TECHNICIAN_REPORT_FILENAME_STEM = "technician_report"
CUSTOMER_REPORT_FILENAME_STEM = "customer_report"


def technician_report_filename(language):
    """Language-qualified technician report filename, e.g. technician_report.en.md."""
    return f"{TECHNICIAN_REPORT_FILENAME_STEM}.{language}.md"


def customer_report_filename(language):
    """Language-qualified customer report filename, e.g. customer_report.de.md."""
    return f"{CUSTOMER_REPORT_FILENAME_STEM}.{language}.md"


def technician_report_pdf_filename(language):
    """Language-qualified technician report PDF filename, e.g. technician_report.en.pdf."""
    return f"{TECHNICIAN_REPORT_FILENAME_STEM}.{language}.pdf"


def customer_report_pdf_filename(language):
    """Language-qualified customer report PDF filename, e.g. customer_report.de.pdf."""
    return f"{CUSTOMER_REPORT_FILENAME_STEM}.{language}.pdf"


# Ordered translation keys for the technician report sections. Rendering derives
# both the section dict keys and the section order from these, so headings and
# order stay consistent in every language.
TECHNICIAN_SECTION_KEYS = (
    "report.section.case_information",
    "report.section.customer_information",
    "report.section.intake_summary",
    "report.section.device_identity",
    "report.section.assessment_results",
    "report.section.imaging_details",
    "report.section.integrity_verification",
    "report.section.recovery_statistics",
    "report.section.audit_timeline",
)

CUSTOMER_SECTION_KEYS = (
    "report.section.case_information",
    "report.section.device_received",
    "report.section.problem_description",
    "report.section.work_performed",
    "report.section.recovery_outcome",
    "report.section.files_recovered",
    "report.section.recommendations",
    "report.section.disclaimer",
)

# Canonical English section titles, kept for reference and as stable test
# anchors. The authoritative, localized headings are rendered from the section
# keys above via the Translator (verified equal in English by the localization
# tests).
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

IMAGE_ARTIFACT_RELATIVE_PATH = f"images/{IMAGE_FILENAME}"
MAP_ARTIFACT_RELATIVE_PATH = f"images/{MAP_FILENAME}"
FINGERPRINT_ARTIFACT_RELATIVE_PATH = f"evidence/{SHA256_FILENAME}"

# Number of localized customer recommendation and disclaimer lines. These map to
# report.customer.recommendation.{n} and report.customer.disclaimer.{n} keys.
CUSTOMER_RECOMMENDATION_COUNT = 4
CUSTOMER_DISCLAIMER_COUNT = 4

# Versioned, HERMES-owned policy content. This is not sourced from case.json and
# is a version identifier, not translated prose.
CUSTOMER_POLICY_VERSION = "1.0"


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


class Hermes:
    """
    Presentation layer for recovery case reporting.

    HERMES accepts a RecoverySession and exposes report builders for
    different audiences. Callers should use build_report() as the
    primary entry point. It gathers and formats recorded case data;
    it does not decide, assess, or recover.

    The report language is resolved once per instance. When no language is
    supplied it defaults to the current operator UI language. An unsupported
    language resolves safely to English. Resolving the language never mutates
    the process-global UI language.
    """

    def __init__(self, session: RecoverySession, language: str | None = None):
        self.session = session
        self.language = self._resolve_report_language(language)

    @staticmethod
    def _resolve_report_language(language):
        if language is None:
            language = get_language()
        normalized = str(language).strip().lower()
        if normalized in SUPPORTED_LANGUAGES:
            return normalized
        return DEFAULT_LANGUAGE

    def _t(self, key, **kwargs):
        return translate(key, self.language, **kwargs)

    def _yes_no(self, value):
        return self._t("report.value.yes") if value else self._t("report.value.no")

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

    def _case_name(self):
        """Recorded case name (a case fact, never translated) for the PDF title
        block. Returns None when the manifest records no case name."""
        return _manifest_field(self._load_manifest(), "case_name")

    def _build_case_information(self, manifest, generated_at):
        return {
            self._t("report.field.case_number"): _manifest_field(
                manifest, "session_id"
            ),
            self._t("report.field.case_name"): _manifest_field(manifest, "case_name"),
            self._t("report.field.creation_date"): _manifest_field(
                manifest, "created_at"
            ),
            self._t("report.field.current_status"): _manifest_field(
                manifest, "status"
            ),
            self._t("report.field.report_generation_date"): generated_at,
        }

    def _build_customer_information(self, manifest):
        return {
            self._t("report.field.name"): _manifest_field(
                manifest, "case_contact", "name"
            ),
            self._t("report.field.telephone"): _manifest_field(
                manifest, "case_contact", "phone"
            ),
            self._t("report.field.email"): _manifest_field(
                manifest, "case_contact", "email"
            ),
        }

    def _build_intake_summary(self, manifest):
        return {
            self._t("report.field.requested_recovery"): _manifest_field(
                manifest, "intake", "recovery_request"
            ),
            self._t("report.field.incident_description"): _manifest_field(
                manifest, "intake", "incident_description"
            ),
            self._t("report.field.previous_recovery_attempts"): _manifest_field(
                manifest, "intake", "previous_recovery_attempts"
            ),
            self._t("report.field.data_priority"): _manifest_field(
                manifest, "intake", "data_priority"
            ),
        }

    def _device_fields(self, manifest, block_key, role):
        device = manifest.get(block_key)
        if not isinstance(device, dict):
            device = {}

        return {
            self._t(f"report.field.{role}_path"): _coerce_display_value(
                device.get("path")
            ),
            self._t(f"report.field.{role}_model"): _coerce_display_value(
                device.get("model")
            ),
            self._t(f"report.field.{role}_serial"): _coerce_display_value(
                device.get("serial")
            ),
            self._t(f"report.field.{role}_size"): _coerce_display_value(
                device.get("size")
            ),
            self._t(f"report.field.{role}_size_bytes"): _coerce_display_value(
                device.get("size_bytes")
            ),
            self._t(f"report.field.{role}_transport"): _coerce_display_value(
                device.get("transport")
            ),
            self._t(f"report.field.{role}_filesystem"): _coerce_display_value(
                device.get("filesystem")
            ),
            self._t(f"report.field.{role}_role"): _coerce_display_value(
                device.get("role")
            ),
        }

    def _build_device_identity(self, manifest):
        fields = {
            **self._device_fields(manifest, "device", "source"),
            **self._device_fields(manifest, "destination", "destination"),
        }

        try:
            smart = read_smart_evidence(self.session.recovery_path)
        except SmartEvidenceError:
            fields[self._t("report.field.smart_evidence")] = self._t(
                "report.placeholder.unreadable"
            )
            return fields

        if smart is None:
            fields[self._t("report.field.smart_evidence")] = self._t(
                "report.placeholder.not_recorded"
            )
            return fields

        if not smart.get("available"):
            fields[self._t("report.field.smart_available")] = self._yes_no(False)
            return fields

        fields[self._t("report.field.smart_available")] = self._yes_no(True)
        health = smart.get("overall_health")
        fields[self._t("report.field.smart_overall_health")] = (
            health if health else self._t("report.value.not_reported")
        )
        return fields

    def _build_assessment_results(self, manifest):
        return {
            self._t("report.field.decision"): _manifest_field(
                manifest, "assessment", "decision"
            ),
            self._t("report.field.reason"): _manifest_field(
                manifest, "assessment", "reason"
            ),
            self._t("report.field.risk"): _manifest_field(
                manifest, "assessment", "risk"
            ),
            self._t("report.field.confidence"): _manifest_field(
                manifest, "assessment", "confidence"
            ),
        }

    def _load_acquisition_state(self):
        return classify_acquisition_state(self.session.recovery_path)

    def _build_imaging_details(self, acquisition_state):
        fields = {
            self._t("report.field.acquisition_state"): _coerce_display_value(
                acquisition_state.get("state")
            ),
            self._t("report.field.acquisition_state_code"): _coerce_display_value(
                acquisition_state.get("code")
            ),
            self._t("report.field.image_present"): acquisition_state.get(
                "image_exists"
            ),
            self._t("report.field.map_present"): acquisition_state.get("map_exists"),
            self._t("report.field.map_status"): _coerce_display_value(
                acquisition_state.get("map_status")
            ),
            self._t("report.field.map_current_status"): _coerce_display_value(
                acquisition_state.get("current_status")
            ),
            self._t("report.field.image_path"): IMAGE_ARTIFACT_RELATIVE_PATH,
            self._t("report.field.map_path"): MAP_ARTIFACT_RELATIVE_PATH,
        }

        try:
            acquisition_source = read_acquisition_source(self.session.recovery_path)
        except AcquisitionSourceError:
            fields[self._t("report.field.acquisition_source_evidence")] = self._t(
                "report.placeholder.unreadable"
            )
            return fields

        if acquisition_source is None:
            fields[self._t("report.field.acquisition_source_evidence")] = self._t(
                "report.placeholder.not_recorded"
            )
            return fields

        fields[self._t("report.field.logical_sector_size")] = acquisition_source.get(
            "logical_sector_size"
        )
        fields[self._t("report.field.physical_sector_size")] = acquisition_source.get(
            "physical_sector_size"
        )
        fields[self._t("report.field.acquisition_timestamp")] = acquisition_source.get(
            "timestamp"
        )
        return fields

    def _build_integrity_verification(self, acquisition_state):
        fields = {
            self._t("report.field.fingerprint_present"): acquisition_state.get(
                "sha256_exists"
            ),
            self._t("report.field.canonical_acquisition_complete"): (
                acquisition_state.get("state") == "completed_canonical"
            ),
            self._t("report.field.fingerprint_path"): (
                FINGERPRINT_ARTIFACT_RELATIVE_PATH
            ),
        }

        try:
            evidence = read_fingerprint_evidence(self.session.recovery_path)
        except FingerprintEvidenceError:
            fields[self._t("report.field.fingerprint_evidence")] = self._t(
                "report.placeholder.unreadable"
            )
            return fields

        if evidence is None:
            fields[self._t("report.field.fingerprint_evidence")] = self._t(
                "report.placeholder.not_recorded"
            )
            return fields

        fields[self._t("report.field.algorithm")] = evidence["algorithm"]
        fields[self._t("report.field.sha256_digest")] = evidence["digest"]
        fields[self._t("report.field.fingerprinted_image")] = evidence[
            "image_filename"
        ]
        fields[self._t("report.field.image_size_bytes")] = evidence[
            "image_size_bytes"
        ]
        fields[self._t("report.field.fingerprint_timestamp")] = evidence["timestamp"]
        return fields

    def _load_recovered_summary(self):
        return summarize_recovered_artifacts(self.session.recovery_path)

    def _recovery_attempt_recorded(self, recovery_operations):
        # Authoritative: a recovery attempt exists iff recovery_operations holds
        # at least one recorded entry. Any valid state counts (RUNNING,
        # COMPLETED, FAILED, INTERRUPTED); success and recovered artifacts are
        # separate facts (see RecoveryOperationReporting.md).
        return self._yes_no(bool(recovery_operations))

    def _build_recovery_statistics(self, recovered_summary, recovery_operations):
        locations = recovered_summary["recup_directories"]

        if not locations:
            output_locations = self._t("report.placeholder.none_recorded")
        elif len(locations) == 1:
            output_locations = locations[0]
        else:
            output_locations = list(locations)

        return {
            self._t("report.field.recovery_attempt_recorded"): (
                self._recovery_attempt_recorded(recovery_operations)
            ),
            self._t("report.field.recovered_file_count"): recovered_summary[
                "recovered_file_count"
            ],
            self._t("report.field.recovered_directory_count"): recovered_summary[
                "recovered_directory_count"
            ],
            self._t("report.field.recovered_size_bytes"): recovered_summary[
                "recovered_size_bytes"
            ],
            self._t("report.field.recovered_output_locations"): output_locations,
        }

    def _build_audit_timeline(self):
        try:
            events = read_audit_log(self.session.recovery_path)
        except AuditLogError:
            return {
                self._t("report.field.events"): self._t(
                    "report.placeholder.unreadable"
                )
            }

        if not events:
            return {
                self._t("report.field.events"): self._t(
                    "report.placeholder.no_audit_events"
                )
            }

        return {self._t("report.field.events"): events}

    def build_technician_report(self, *, generated_at=None):
        """
        Build the technician report for the current recovery session.

        The generation timestamp defaults to the current time. It may be
        supplied explicitly (e.g. so a PDF footer and the report field share one
        timestamp, or to pin the value in tests); this does not change the
        default behavior of existing callers.
        """
        manifest = self._load_manifest()
        if generated_at is None:
            generated_at = datetime.now()
        acquisition_state = self._load_acquisition_state()
        recovered_summary = self._load_recovered_summary()
        recovery_operations = manifest.get("recovery_operations") or []

        return {
            self._t("report.section.case_information"): self._build_case_information(
                manifest, generated_at
            ),
            self._t(
                "report.section.customer_information"
            ): self._build_customer_information(manifest),
            self._t("report.section.intake_summary"): self._build_intake_summary(
                manifest
            ),
            self._t("report.section.device_identity"): self._build_device_identity(
                manifest
            ),
            self._t(
                "report.section.assessment_results"
            ): self._build_assessment_results(manifest),
            self._t("report.section.imaging_details"): self._build_imaging_details(
                acquisition_state
            ),
            self._t(
                "report.section.integrity_verification"
            ): self._build_integrity_verification(acquisition_state),
            self._t(
                "report.section.recovery_statistics"
            ): self._build_recovery_statistics(
                recovered_summary, recovery_operations
            ),
            self._t("report.section.audit_timeline"): self._build_audit_timeline(),
        }

    def build_technician_markdown(self):
        """
        Build a Markdown representation of the technician report.
        """
        report = self.build_technician_report()
        return ReportFormatter().format_markdown(
            self._t("report.title.technician"),
            report,
            section_order=tuple(report.keys()),
        )

    def save_technician_report(self) -> Path:
        """
        Write the technician report as Markdown into the case reports directory.

        Creates the reports directory when it does not exist. The filename is
        language-qualified (e.g. technician_report.en.md). Raises
        FileExistsError when that language's report is already present, so each
        language version has independent overwrite protection.
        """
        reports_dir = Path(self.session.recovery_path) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        report_path = reports_dir / technician_report_filename(self.language)
        if report_path.exists():
            raise FileExistsError(
                f"Technician report already exists: {report_path}"
            )

        report_path.write_text(self.build_technician_markdown(), encoding="utf-8")
        return report_path

    def build_technician_pdf(self, *, generated_at=None, invariant=False) -> bytes:
        """
        Build a PDF representation of the technician report.

        Renders the same structured, localized report that Markdown uses; it
        does not parse Markdown and introduces no new report facts. A single
        generation timestamp is shared by the report field and the PDF footer.
        ``invariant`` (reproducible output) is off by default and enabled only
        by determinism tests.
        """
        if generated_at is None:
            generated_at = datetime.now()
        report = self.build_technician_report(generated_at=generated_at)
        return PdfReportFormatter().format_pdf(
            title=self._t("report.title.technician"),
            report=report,
            section_order=tuple(report.keys()),
            language=self.language,
            report_kind="technician",
            case_identifier=self.session.session_id,
            case_name=self._case_name(),
            generated_at=generated_at,
            invariant=invariant,
        )

    def save_technician_pdf(self, *, generated_at=None, invariant=False) -> Path:
        """
        Write the technician report as a PDF into the case reports directory.

        Mirrors save_technician_report: creates the reports directory, uses a
        language- and format-qualified filename (technician_report.<lang>.pdf),
        and raises FileExistsError when that file already exists, so overwrite
        protection is independent per (report type, language, format). The PDF
        is rendered fully in memory before writing, so a rendering failure never
        writes a partial file and never affects the Markdown report.
        """
        reports_dir = Path(self.session.recovery_path) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        report_path = reports_dir / technician_report_pdf_filename(self.language)
        if report_path.exists():
            raise FileExistsError(
                f"Technician report PDF already exists: {report_path}"
            )

        pdf_bytes = self.build_technician_pdf(
            generated_at=generated_at, invariant=invariant
        )
        report_path.write_bytes(pdf_bytes)
        return report_path

    def _customer_value(self, value):
        return value if value is not None else self._t(
            "report.placeholder.not_recorded"
        )

    def _customer_capacity(self, manifest):
        size_bytes = _manifest_field(manifest, "device", "size_bytes")
        if (
            isinstance(size_bytes, int)
            and not isinstance(size_bytes, bool)
            and size_bytes > 0
        ):
            return format_bytes(size_bytes)
        return self._customer_value(_manifest_field(manifest, "device", "size"))

    def _neutral_outcome(self, outcome):
        # Neutral, definition-only wording. HERMES does not infer reasons,
        # quality, percentages, or case-specific limitations from the outcome.
        outcome_keys = {
            "SUCCESSFUL": "report.customer.outcome.successful",
            "PARTIAL": "report.customer.outcome.partial",
            "UNSUCCESSFUL": "report.customer.outcome.unsuccessful",
        }
        key = outcome_keys.get(outcome)
        if key is None:
            return self._t("report.customer.outcome.not_recorded")
        return self._t(key)

    def _customer_imaging(self, state):
        # Three neutral customer-facing imaging states derived from the
        # authoritative acquisition state. Only completed_canonical is the
        # trusted complete state; no_acquisition means imaging was not
        # performed; every other authoritative state means it was not completed.
        if state == "completed_canonical":
            return self._t("report.customer.imaging.completed")
        if state == "no_acquisition":
            return self._t("report.customer.imaging.not_performed")
        return self._t("report.customer.imaging.not_completed")

    def _build_customer_case_information(self, manifest, generated_at):
        return {
            self._t("report.field.case_number"): self._customer_value(
                _manifest_field(manifest, "session_id")
            ),
            self._t("report.field.customer_name"): self._customer_value(
                _manifest_field(manifest, "case_contact", "name")
            ),
            self._t("report.field.case_completed"): self._customer_value(
                _manifest_field(manifest, "completed_at")
            ),
            self._t("report.field.report_generated"): generated_at,
        }

    def _build_device_received(self, manifest):
        device = manifest.get("device")
        device_present = isinstance(device, dict) and bool(device)

        return {
            self._t("report.field.device"): self._customer_value(
                _manifest_field(manifest, "device", "model")
            ),
            self._t("report.field.capacity"): self._customer_capacity(manifest),
            self._t("report.field.number_of_devices_received"): (
                1 if device_present else 0
            ),
        }

    def _build_problem_description(self, manifest):
        return {
            self._t("report.field.requested_recovery"): self._customer_value(
                _manifest_field(manifest, "intake", "recovery_request")
            ),
            self._t("report.field.what_happened"): self._customer_value(
                _manifest_field(manifest, "intake", "incident_description")
            ),
            self._t("report.field.most_important_data"): self._customer_value(
                _manifest_field(manifest, "intake", "data_priority")
            ),
        }

    def _build_customer_work_performed(self, acquisition_state):
        return {
            self._t("report.field.imaging"): self._customer_imaging(
                acquisition_state.get("state")
            ),
        }

    def _build_customer_recovery_outcome(self, manifest):
        outcome = _manifest_field(manifest, "recovery_outcome")
        return {
            self._t("report.field.outcome"): self._neutral_outcome(outcome),
        }

    def _build_files_recovered(self, recovered_summary):
        return {
            self._t("report.field.recovered_items"): recovered_summary[
                "recovered_file_count"
            ],
            self._t("report.field.recovered_data"): format_bytes(
                recovered_summary["recovered_size_bytes"]
            ),
        }

    def _customer_recommendations(self):
        return tuple(
            self._t(f"report.customer.recommendation.{index}")
            for index in range(1, CUSTOMER_RECOMMENDATION_COUNT + 1)
        )

    def _customer_disclaimer(self):
        return tuple(
            self._t(f"report.customer.disclaimer.{index}")
            for index in range(1, CUSTOMER_DISCLAIMER_COUNT + 1)
        )

    def _build_customer_recommendations(self):
        return {
            self._t("report.field.guidance"): self._customer_recommendations(),
            self._t("report.field.policy_version"): CUSTOMER_POLICY_VERSION,
        }

    def _build_customer_disclaimer(self):
        return {
            self._t("report.field.terms"): self._customer_disclaimer(),
            self._t("report.field.policy_version"): CUSTOMER_POLICY_VERSION,
        }

    def build_customer_report(self, *, generated_at=None):
        """
        Build the customer report for the current recovery session.

        The customer report presents only customer-visible facts. Work
        Performed states authoritative imaging facts only; it never infers a
        recovery operation from recovered artifacts. Recovered-file figures are
        observational and read through the owning ARCHIVE summary API.

        The generation timestamp defaults to the current time and may be
        supplied explicitly; this does not change existing caller behavior.
        """
        manifest = self._load_manifest()
        if generated_at is None:
            generated_at = datetime.now()
        acquisition_state = self._load_acquisition_state()
        recovered_summary = self._load_recovered_summary()

        return {
            self._t(
                "report.section.case_information"
            ): self._build_customer_case_information(manifest, generated_at),
            self._t("report.section.device_received"): self._build_device_received(
                manifest
            ),
            self._t(
                "report.section.problem_description"
            ): self._build_problem_description(manifest),
            self._t(
                "report.section.work_performed"
            ): self._build_customer_work_performed(acquisition_state),
            self._t(
                "report.section.recovery_outcome"
            ): self._build_customer_recovery_outcome(manifest),
            self._t("report.section.files_recovered"): self._build_files_recovered(
                recovered_summary
            ),
            self._t(
                "report.section.recommendations"
            ): self._build_customer_recommendations(),
            self._t("report.section.disclaimer"): self._build_customer_disclaimer(),
        }

    def build_customer_markdown(self):
        """
        Build a Markdown representation of the customer report.
        """
        report = self.build_customer_report()
        return ReportFormatter().format_markdown(
            self._t("report.title.customer"),
            report,
            section_order=tuple(report.keys()),
        )

    def save_customer_report(self) -> Path:
        """
        Write the customer report as Markdown into the case reports directory.

        Creates the reports directory when it does not exist. The filename is
        language-qualified (e.g. customer_report.en.md). Raises FileExistsError
        when that language's report is already present, so each language version
        has independent overwrite protection.
        """
        reports_dir = Path(self.session.recovery_path) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        report_path = reports_dir / customer_report_filename(self.language)
        if report_path.exists():
            raise FileExistsError(
                f"Customer report already exists: {report_path}"
            )

        report_path.write_text(self.build_customer_markdown(), encoding="utf-8")
        return report_path

    def build_customer_pdf(self, *, generated_at=None, invariant=False) -> bytes:
        """
        Build a PDF representation of the customer report.

        Renders the same structured, localized report that Markdown uses; it
        does not parse Markdown and introduces no new report facts. ``invariant``
        (reproducible output) is off by default and enabled only by determinism
        tests.
        """
        if generated_at is None:
            generated_at = datetime.now()
        report = self.build_customer_report(generated_at=generated_at)
        return PdfReportFormatter().format_pdf(
            title=self._t("report.title.customer"),
            report=report,
            section_order=tuple(report.keys()),
            language=self.language,
            report_kind="customer",
            case_identifier=self.session.session_id,
            # The internal case name is deliberately omitted from the
            # customer-facing PDF; only the Technician PDF shows it.
            case_name=None,
            generated_at=generated_at,
            invariant=invariant,
        )

    def save_customer_pdf(self, *, generated_at=None, invariant=False) -> Path:
        """
        Write the customer report as a PDF into the case reports directory.

        Mirrors save_customer_report with a language- and format-qualified
        filename (customer_report.<lang>.pdf) and independent overwrite
        protection per (report type, language, format). Rendered in memory
        before writing, so a failure never writes a partial file and never
        affects the Markdown report.
        """
        reports_dir = Path(self.session.recovery_path) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        report_path = reports_dir / customer_report_pdf_filename(self.language)
        if report_path.exists():
            raise FileExistsError(
                f"Customer report PDF already exists: {report_path}"
            )

        pdf_bytes = self.build_customer_pdf(
            generated_at=generated_at, invariant=invariant
        )
        report_path.write_bytes(pdf_bytes)
        return report_path

    def build_partner_report(self):
        """
        Build the partner report for the current recovery session.
        """
        raise NotImplementedError
