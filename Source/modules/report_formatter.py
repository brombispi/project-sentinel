"""
Report formatting utilities.

ReportFormatter converts report data into readable output formats.
It performs presentation only. It does not apply business logic,
modify case data, or write files.
"""


class ReportFormatter:
    """
    Presentation utilities for report output.
    """

    def format_markdown(self, title: str, report: dict) -> str:
        """
        Format a report dictionary as Markdown.
        """
        lines = [
            f"# {title}",
            "",
        ]

        for key, value in report.items():
            lines.append(f"{key}: {value}")

        return "\n".join(lines)
