"""
HERMES reporting module.

HERMES is a presentation module only. It reads a Recovery Case and
formats existing case information into readable reports. HERMES does
not observe devices, assess safety, execute operations, modify case
state, or perform business logic.
"""

from pathlib import Path

from core.session import RecoverySession
from modules.report_formatter import ReportFormatter

TECHNICIAN_REPORT_FILENAME = "technician_report.md"


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

    def build_technician_report(self):
        """
        Build the technician report for the current recovery session.
        """
        session = self.session

        return {
            "Case ID": session.session_id or None,
            "Case Name": session.case_name or None,
            "Status": session.status or None,
            "Created At": session.created_at or None,
        }

    def build_technician_markdown(self):
        """
        Build a Markdown representation of the technician report.
        """
        report = self.build_technician_report()
        return ReportFormatter().format_markdown("Technician Report", report)

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
