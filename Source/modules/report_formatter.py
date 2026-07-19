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

    def format_markdown(self, title: str, report: dict, *, section_order=()) -> str:
        """
        Format a sectioned report dictionary as Markdown.

        Each top-level key is a section title mapping to a field dictionary.
        section_order selects which sections to render and in what order.
        """
        lines = [
            f"# {title}",
            "",
        ]

        if section_order:
            sections = section_order
        else:
            sections = report.keys()

        for section_title in sections:
            fields = report.get(section_title, {})
            lines.append(f"## {section_title}")
            lines.append("")

            for key, value in fields.items():
                if isinstance(value, (list, tuple)):
                    lines.append(f"{key}:")
                    for item in value:
                        lines.append(f"- {item}")
                else:
                    lines.append(f"{key}: {value}")

            lines.append("")

        if lines[-1] == "":
            lines.pop()

        return "\n".join(lines)

    def format_plaintext(self, title: str, report: dict, *, section_order=()) -> str:
        """
        Format a sectioned report dictionary as plain text.

        Uses the same section and field structure as format_markdown but
        without Markdown heading markers.
        """
        lines = [
            title,
            "",
        ]

        if section_order:
            sections = section_order
        else:
            sections = report.keys()

        for section_title in sections:
            fields = report.get(section_title, {})
            lines.append(section_title)
            lines.append("")

            for key, value in fields.items():
                if isinstance(value, (list, tuple)):
                    lines.append(f"{key}:")
                    for item in value:
                        lines.append(f"- {item}")
                else:
                    lines.append(f"{key}: {value}")

            lines.append("")

        if lines[-1] == "":
            lines.pop()

        return "\n".join(lines)
