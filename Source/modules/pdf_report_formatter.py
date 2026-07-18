"""
PDF rendering for HERMES reports.

PdfReportFormatter is a presentation-only sibling of ReportFormatter. It renders
the same ordered, localized structured report dictionary that HERMES builds for
Markdown into a restrained A4 PDF. It creates no facts, reads no case data, and
never mutates case state, case.json, or the global UI language.

ReportLab is an optional, third-party dependency. It is imported lazily inside
the rendering path so that Sentinel startup, case handling, and Markdown report
generation continue to work when ReportLab is not installed. A missing ReportLab
library, or a missing/unreadable bundled font, raises a clear, PDF-specific
error. Host fonts are never discovered or substituted: the bundled DejaVu fonts
under Source/assets/fonts/ are the only fonts used, resolved relative to this
module's installed location (not the current working directory).
"""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

from i18n import translate

# Fonts are resolved relative to the installed Source package, never the CWD.
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
FONT_MONO = "DejaVuSansMono"

_FONT_FILES = {
    FONT_REGULAR: "DejaVuSans.ttf",
    FONT_BOLD: "DejaVuSans-Bold.ttf",
    FONT_MONO: "DejaVuSansMono.ttf",
}

# report.field.* keys whose values are technical tokens (paths, hashes, device
# identifiers, byte/sector sizes, state codes) and the raw audit-timeline lines.
# These render in the monospaced font. Classification is by the localized label,
# resolved per language from the same keys HERMES uses, so it stays consistent
# with the report content and never depends on parsing values.
_TECHNICAL_FIELD_KEYS = (
    "report.field.source_path",
    "report.field.source_serial",
    "report.field.source_size_bytes",
    "report.field.destination_path",
    "report.field.destination_serial",
    "report.field.destination_size_bytes",
    "report.field.acquisition_state_code",
    "report.field.logical_sector_size",
    "report.field.physical_sector_size",
    "report.field.image_path",
    "report.field.map_path",
    "report.field.fingerprint_path",
    "report.field.sha256_digest",
    "report.field.fingerprinted_image",
    "report.field.image_size_bytes",
    "report.field.recovered_size_bytes",
    "report.field.recovered_output_locations",
    "report.field.events",
)


class PdfReportError(Exception):
    """Base class for PDF export failures.

    Every subclass carries a ``message_key`` that the presentation layer maps to
    a localized operator message. PDF failures are isolated: raising one never
    mutates case state and never touches an existing report file.
    """

    message_key = "report.pdf.error.rendering"


class PdfDependencyError(PdfReportError):
    """Raised when the optional ReportLab dependency is not available."""

    message_key = "report.pdf.error.dependency"


class PdfFontError(PdfReportError):
    """Raised when a required bundled font is missing or unreadable."""

    message_key = "report.pdf.error.font"


class PdfRenderingError(PdfReportError):
    """Raised when rendering the PDF fails for any other reason."""

    message_key = "report.pdf.error.rendering"


def _load_reportlab():
    """Import ReportLab lazily and return the symbols the renderer needs.

    Raises PdfDependencyError (never a bare ImportError) so the presentation
    layer can report a clear, PDF-specific failure and continue safely.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
        from reportlab.platypus import (
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise PdfDependencyError(
            "PDF export requires the ReportLab library, which is not installed."
        ) from error

    return SimpleNamespace(
        A4=A4,
        mm=mm,
        colors=colors,
        ParagraphStyle=ParagraphStyle,
        pdfmetrics=pdfmetrics,
        TTFont=TTFont,
        canvas=canvas,
        KeepTogether=KeepTogether,
        Paragraph=Paragraph,
        SimpleDocTemplate=SimpleDocTemplate,
        Spacer=Spacer,
        Table=Table,
        TableStyle=TableStyle,
    )


def _register_fonts(rl):
    """Register the bundled DejaVu fonts, failing clearly if any are missing.

    Font files are verified to exist on every call (not only on first
    registration) so a removed/unreadable font is always reported as a
    PdfFontError rather than silently falling back to a host font.
    """
    resolved = {}
    for name, filename in _FONT_FILES.items():
        path = FONTS_DIR / filename
        if not path.is_file():
            raise PdfFontError(f"Required PDF font not found: {path}")
        resolved[name] = path

    registered = set(rl.pdfmetrics.getRegisteredFontNames())
    for name, path in resolved.items():
        if name in registered:
            continue
        try:
            rl.pdfmetrics.registerFont(rl.TTFont(name, str(path)))
        except Exception as error:  # pragma: no cover - defensive
            raise PdfFontError(f"Required PDF font could not be loaded: {path}") from error

    rl.pdfmetrics.registerFontFamily(
        FONT_REGULAR,
        normal=FONT_REGULAR,
        bold=FONT_BOLD,
        italic=FONT_REGULAR,
        boldItalic=FONT_BOLD,
    )


class PdfReportFormatter:
    """Renders a structured HERMES report dictionary to a PDF document."""

    def format_pdf(
        self,
        *,
        title,
        report,
        section_order,
        language,
        report_kind,
        case_identifier,
        case_name=None,
        generated_at,
        invariant=False,
    ) -> bytes:
        """Render a report to PDF bytes.

        The document is built entirely in memory and only the fully rendered
        bytes are returned, so a rendering failure never leaves a partial file.
        ``invariant`` enables ReportLab's reproducible mode (fixed internal
        metadata date and document id) so identical inputs yield identical
        bytes; the visible generation timestamp is unaffected. It is off by
        default so delivered PDFs carry honest metadata, and is enabled
        explicitly by determinism tests.
        """
        rl = _load_reportlab()
        _register_fonts(rl)

        styles = self._styles(rl)

        confidential_text = self._confidential_text(language, report_kind)
        generated_text = translate(
            "report.pdf.footer.generated",
            language,
            timestamp=self._format_timestamp(generated_at),
        )

        story = self._title_block(
            rl,
            styles,
            title=title,
            case_identifier=case_identifier,
            case_name=case_name,
            generated_text=generated_text,
            language=language,
        ) + self._build_story(rl, styles, report, section_order, language)

        safe_title = escape(str(title))
        safe_case = escape(str(case_identifier)) if case_identifier is not None else ""

        def draw_furniture(canvas_obj, doc):
            self._draw_furniture(
                canvas_obj,
                rl=rl,
                title=safe_title,
                case_identifier=safe_case,
                confidential_text=confidential_text,
                generated_text=generated_text,
            )

        def page_label(current, total):
            return translate(
                "report.pdf.footer.page",
                language,
                current=current,
                total=total,
            )

        canvas_maker = self._numbered_canvas_maker(rl, page_label, invariant)

        buffer = BytesIO()
        doc = rl.SimpleDocTemplate(
            buffer,
            pagesize=rl.A4,
            title=str(title),
            leftMargin=20 * rl.mm,
            rightMargin=20 * rl.mm,
            topMargin=26 * rl.mm,
            bottomMargin=20 * rl.mm,
        )

        try:
            doc.build(
                story,
                onFirstPage=draw_furniture,
                onLaterPages=draw_furniture,
                canvasmaker=canvas_maker,
            )
        except PdfReportError:
            raise
        except Exception as error:
            raise PdfRenderingError(
                f"PDF rendering failed: {error}"
            ) from error

        return buffer.getvalue()

    # -- Story construction (the ReportLab flowable boundary) --------------

    def build_story(self, report, section_order, language):
        """Return the ordered ReportLab flowables for a report.

        Exposed as a testable boundary: callers can prove that localized and
        technical values reach real ReportLab flowables with the expected fonts,
        without inspecting compressed PDF bytes.
        """
        rl = _load_reportlab()
        _register_fonts(rl)
        return self._build_story(rl, self._styles(rl), report, section_order, language)

    def _build_story(self, rl, styles, report, section_order, language):
        technical_labels = {
            translate(key, language) for key in _TECHNICAL_FIELD_KEYS
        }

        story = []
        sections = section_order if section_order else tuple(report.keys())

        for section_title in sections:
            fields = report.get(section_title, {})
            heading = rl.Paragraph(escape(str(section_title)), styles["heading"])
            body = self._section_body(rl, styles, fields, technical_labels)

            if body:
                # Keep the heading with its first content flowable so a heading
                # never strands alone at the bottom of a page.
                story.append(rl.KeepTogether([heading, body[0]]))
                story.extend(body[1:])
            else:
                story.append(heading)

            story.append(rl.Spacer(1, 6 * rl.mm))

        return story

    def _section_body(self, rl, styles, fields, technical_labels):
        """Return the flowables for one section's fields.

        Consecutive scalar fields are grouped into a single key/value table;
        list-valued fields (recommendations, disclaimer, output locations, audit
        events) render as a bold label followed by bulleted paragraphs.
        """
        body = []
        pending_rows = []

        def flush_rows():
            if pending_rows:
                body.append(self._kv_table(rl, styles, list(pending_rows)))
                pending_rows.clear()

        for label, value in fields.items():
            is_technical = label in technical_labels
            if isinstance(value, (list, tuple)):
                flush_rows()
                body.append(rl.Paragraph(escape(str(label)), styles["field_label"]))
                bullet_style = (
                    styles["mono_bullet"] if is_technical else styles["bullet"]
                )
                for item in value:
                    body.append(
                        rl.Paragraph("\u2022&nbsp;" + escape(str(item)), bullet_style)
                    )
            else:
                label_para = rl.Paragraph(escape(str(label)), styles["label"])
                value_style = styles["mono_value"] if is_technical else styles["value"]
                value_para = rl.Paragraph(escape(str(value)), value_style)
                pending_rows.append((label_para, value_para))

        flush_rows()
        return body

    def _title_block(
        self,
        rl,
        styles,
        *,
        title,
        case_identifier,
        case_name,
        generated_text,
        language,
    ):
        """First-page title block: large report title, case number and name,
        and the generation timestamp (per PdfReportExport.md §5.1)."""
        block = [rl.Paragraph(escape(str(title)), styles["title"])]

        if case_identifier:
            block.append(
                rl.Paragraph(
                    escape(
                        "%s: %s"
                        % (translate("report.field.case_number", language), case_identifier)
                    ),
                    styles["title_meta"],
                )
            )
        if case_name:
            block.append(
                rl.Paragraph(
                    escape(
                        "%s: %s"
                        % (translate("report.field.case_name", language), case_name)
                    ),
                    styles["title_meta"],
                )
            )
        block.append(rl.Paragraph(escape(str(generated_text)), styles["title_meta"]))
        block.append(rl.Spacer(1, 8 * rl.mm))
        return block

    def _kv_table(self, rl, styles, rows):
        content_width = rl.A4[0] - 40 * rl.mm
        label_width = 55 * rl.mm
        value_width = content_width - label_width

        data = [[label, value] for label, value in rows]
        table = rl.Table(data, colWidths=[label_width, value_width])
        table.setStyle(
            rl.TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        # Explicit inspection seam for tests, without depending on ReportLab
        # internals: the original (label, value) flowables for each row.
        table._hermes_rows = rows
        return table

    # -- Page furniture ----------------------------------------------------

    def _draw_furniture(
        self,
        canvas_obj,
        *,
        rl,
        title,
        case_identifier,
        confidential_text,
        generated_text,
    ):
        page_width, page_height = rl.A4
        left = 20 * rl.mm
        right = page_width - 20 * rl.mm

        # Header: report title (left), case identifier (right), separating rule.
        canvas_obj.setFont(FONT_BOLD, 8)
        canvas_obj.drawString(left, page_height - 14 * rl.mm, title)
        if case_identifier:
            canvas_obj.setFont(FONT_REGULAR, 8)
            canvas_obj.drawRightString(right, page_height - 14 * rl.mm, case_identifier)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(
            left, page_height - 16 * rl.mm, right, page_height - 16 * rl.mm
        )

        # Footer: generation timestamp (left), confidentiality marking (center,
        # Technician Report only). "Page X of Y" is drawn by the numbered canvas.
        canvas_obj.setFont(FONT_REGULAR, 7.5)
        canvas_obj.drawString(left, 12 * rl.mm, generated_text)
        if confidential_text:
            canvas_obj.setFont(FONT_BOLD, 7.5)
            canvas_obj.drawCentredString(
                page_width / 2.0, 12 * rl.mm, confidential_text
            )

    def _numbered_canvas_maker(self, rl, page_label, invariant):
        page_width = rl.A4[0]

        class _NumberedCanvas(rl.canvas.Canvas):
            """Canvas that stamps 'Page X of Y' once the total is known."""

            def __init__(self, *args, **kwargs):
                if invariant:
                    kwargs["invariant"] = 1
                super().__init__(*args, **kwargs)
                self._saved_page_states = []

            def showPage(self):
                self._saved_page_states.append(dict(self.__dict__))
                self._startPage()

            def save(self):
                total = len(self._saved_page_states)
                for state in self._saved_page_states:
                    self.__dict__.update(state)
                    self._draw_page_number(total)
                    super().showPage()
                super().save()

            def _draw_page_number(self, total):
                self.setFont(FONT_REGULAR, 7.5)
                self.drawRightString(
                    page_width - 20 * rl.mm,
                    12 * rl.mm,
                    page_label(self._pageNumber, total),
                )

        return _NumberedCanvas

    # -- Helpers -----------------------------------------------------------

    def _confidential_text(self, language, report_kind):
        """Localized Internal/Confidential marking, Technician Reports only."""
        if report_kind == "technician":
            return translate("report.pdf.confidential.technician", language)
        return None

    def _format_timestamp(self, generated_at):
        if isinstance(generated_at, datetime):
            return generated_at.strftime("%Y-%m-%d %H:%M:%S")
        return str(generated_at)

    def _styles(self, rl):
        return {
            "title": rl.ParagraphStyle(
                "HermesTitle",
                fontName=FONT_BOLD,
                fontSize=20,
                leading=24,
                spaceAfter=4,
            ),
            "title_meta": rl.ParagraphStyle(
                "HermesTitleMeta",
                fontName=FONT_REGULAR,
                fontSize=10,
                leading=14,
            ),
            "heading": rl.ParagraphStyle(
                "HermesHeading",
                fontName=FONT_BOLD,
                fontSize=13,
                leading=16,
                spaceBefore=10,
                spaceAfter=6,
            ),
            "field_label": rl.ParagraphStyle(
                "HermesFieldLabel",
                fontName=FONT_BOLD,
                fontSize=9,
                leading=12,
                spaceBefore=2,
                spaceAfter=2,
            ),
            "label": rl.ParagraphStyle(
                "HermesLabel",
                fontName=FONT_BOLD,
                fontSize=9,
                leading=12,
            ),
            "value": rl.ParagraphStyle(
                "HermesValue",
                fontName=FONT_REGULAR,
                fontSize=9,
                leading=12,
            ),
            "mono_value": rl.ParagraphStyle(
                "HermesMonoValue",
                fontName=FONT_MONO,
                fontSize=8,
                leading=11,
                wordWrap="CJK",
            ),
            "bullet": rl.ParagraphStyle(
                "HermesBullet",
                fontName=FONT_REGULAR,
                fontSize=9,
                leading=12,
                leftIndent=10,
                spaceAfter=2,
            ),
            "mono_bullet": rl.ParagraphStyle(
                "HermesMonoBullet",
                fontName=FONT_MONO,
                fontSize=8,
                leading=11,
                leftIndent=10,
                spaceAfter=2,
                wordWrap="CJK",
            ),
        }

    @staticmethod
    def flowable_text_font_pairs(story):
        """Yield (text, font_name) for every rendered Paragraph in a story.

        Walks key/value tables via the inspection seam so tests can verify that
        localized labels/values and monospaced technical values reach real
        ReportLab flowables.
        """
        for flowable in story:
            content = getattr(flowable, "_content", None)
            if content is not None:
                # KeepTogether (and similar) group child flowables; recurse so
                # kept-together headings and rows are still visible to tests.
                yield from PdfReportFormatter.flowable_text_font_pairs(content)
                continue
            rows = getattr(flowable, "_hermes_rows", None)
            if rows is not None:
                for label_para, value_para in rows:
                    yield label_para.text, label_para.style.fontName
                    yield value_para.text, value_para.style.fontName
                continue
            text = getattr(flowable, "text", None)
            style = getattr(flowable, "style", None)
            if text is not None and style is not None:
                yield text, style.fontName
