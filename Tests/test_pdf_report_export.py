"""
Focused tests for HERMES PDF report export.

These verify that the localized structured report dictionary is rendered to PDF
by ReportLab (the sibling of the Markdown renderer), that filenames and
overwrite protection are independent per (report type, language, format), that
PDF and Markdown coexist, that localized and technical values reach real
ReportLab flowables, and that PDF failures are isolated: they never mutate
case.json, never change the global UI language, and never damage an existing
Markdown or PDF report.

ReportLab is required to run this module. It is the PDF subsystem's dependency;
Sentinel and the Markdown path do not require it.
"""

import hashlib
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

I18N_DIR = SOURCE_ROOT / "i18n"

from core.session import RecoverySession
from i18n import get_language, init_language, set_language
from modules import pdf_report_formatter
from modules.pdf_report_formatter import (
    FONT_BOLD,
    FONT_MONO,
    FONT_REGULAR,
    PdfDependencyError,
    PdfFontError,
    PdfRenderingError,
    PdfReportError,
    PdfReportFormatter,
)
from modules.hermes import (
    Hermes,
    customer_report_filename,
    customer_report_pdf_filename,
    technician_report_filename,
    technician_report_pdf_filename,
)

FIXED_GENERATED_AT = datetime(2026, 7, 16, 12, 30, 0)

LONG_PATH = "/dev/disk/by-id/" + "sub-directory-segment/" * 12 + "source.img"
LONG_HASH = "a1b2c3d4" * 8  # 64 hex characters, SHA-256-shaped


def _case_dir(temp_dir, session_id="REC-2026-000001"):
    case_dir = Path(temp_dir) / session_id
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def _write_manifest(case_dir, manifest):
    (case_dir / "case.json").write_text(
        json.dumps(manifest, indent=4) + "\n", encoding="utf-8"
    )


def _session(case_dir, **overrides):
    values = {
        "session_id": case_dir.name,
        "created_at": datetime(2026, 7, 16, 10, 0, 0),
        "status": "COMPLETED",
        "recovery_path": str(case_dir),
        "case_name": "Customer SSD Recovery",
    }
    values.update(overrides)
    return RecoverySession(**values)


def _populated_manifest():
    return {
        "session_id": "REC-2026-000001",
        "case_name": "Customer SSD Recovery",
        "created_at": "2026-07-16T10:00:00",
        "status": "COMPLETED",
        "completed_at": "2026-07-17T09:00:00",
        "recovery_outcome": "SUCCESSFUL",
        "recovery_operations": [
            {
                "type": "PHOTOREC",
                "state": "COMPLETED",
                "started_at": "2026-07-16T11:00:05",
                "finished_at": "2026-07-16T11:42:31",
            }
        ],
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
        "assessment": {
            "decision": "APPROVED",
            "reason": "External device.",
            "risk": "LOW",
            "confidence": 100,
        },
    }


def _long_value_manifest():
    manifest = _populated_manifest()
    manifest["device"]["path"] = LONG_PATH
    manifest["device"]["serial"] = LONG_HASH
    # A long intake narrative forces the document across multiple pages.
    manifest["intake"]["incident_description"] = (
        "The drive stopped responding after an abrupt power loss. " * 60
    )
    return manifest


def _minimal_manifest():
    return {
        "session_id": "REC-2026-000001",
        "case_name": "",
        "created_at": "2026-07-16T10:00:00",
        "status": "NEW",
    }


def _page_count(pdf_bytes):
    # Count page objects without matching the "/Type /Pages" tree node.
    return len(re.findall(rb"/Type\s*/Page(?![s])", pdf_bytes))


def _texts_and_fonts(story):
    return list(PdfReportFormatter.flowable_text_font_pairs(story))


def _joined_text(story):
    return "\n".join(text for text, _ in _texts_and_fonts(story))


class _LanguageIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()

    def tearDown(self):
        init_language(SOURCE_ROOT)
        set_language(self._previous_language, persist=False)


class PdfGenerationTests(_LanguageIsolatedTestCase):
    def _save(self, method_name, language, manifest=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, manifest or _populated_manifest())
            hermes = Hermes(_session(case_dir), language)
            path = getattr(hermes, method_name)(generated_at=FIXED_GENERATED_AT)
            data = path.read_bytes()
            return path, data

    def _assert_valid_pdf(self, data):
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertGreater(len(data), 0)

    def test_english_technician_pdf(self):
        path, data = self._save("save_technician_pdf", "en")
        self.assertEqual(path.name, "technician_report.en.pdf")
        self._assert_valid_pdf(data)

    def test_german_technician_pdf(self):
        set_language("en", persist=False)  # UI English, report explicitly German
        path, data = self._save("save_technician_pdf", "de")
        self.assertEqual(path.name, "technician_report.de.pdf")
        self._assert_valid_pdf(data)

    def test_english_customer_pdf(self):
        path, data = self._save("save_customer_pdf", "en")
        self.assertEqual(path.name, "customer_report.en.pdf")
        self._assert_valid_pdf(data)

    def test_german_customer_pdf(self):
        path, data = self._save("save_customer_pdf", "de")
        self.assertEqual(path.name, "customer_report.de.pdf")
        self._assert_valid_pdf(data)

    def test_multipage_output_for_long_report(self):
        _, data = self._save(
            "save_technician_pdf", "en", manifest=_long_value_manifest()
        )
        self.assertGreater(_page_count(data), 1)

    def test_nonzero_output_for_minimal_case(self):
        _, data = self._save(
            "save_technician_pdf", "en", manifest=_minimal_manifest()
        )
        self._assert_valid_pdf(data)


class PdfFilenameTests(_LanguageIsolatedTestCase):
    def test_exact_language_qualified_pdf_filenames(self):
        self.assertEqual(
            technician_report_pdf_filename("en"), "technician_report.en.pdf"
        )
        self.assertEqual(
            technician_report_pdf_filename("de"), "technician_report.de.pdf"
        )
        self.assertEqual(
            customer_report_pdf_filename("en"), "customer_report.en.pdf"
        )
        self.assertEqual(
            customer_report_pdf_filename("de"), "customer_report.de.pdf"
        )

    def test_saved_pdf_files_use_exact_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            session = _session(case_dir)
            reports_dir = case_dir / "reports"

            self.assertEqual(
                Hermes(session, "en").save_technician_pdf(),
                reports_dir / "technician_report.en.pdf",
            )
            self.assertEqual(
                Hermes(session, "de").save_technician_pdf(),
                reports_dir / "technician_report.de.pdf",
            )
            self.assertEqual(
                Hermes(session, "en").save_customer_pdf(),
                reports_dir / "customer_report.en.pdf",
            )
            self.assertEqual(
                Hermes(session, "de").save_customer_pdf(),
                reports_dir / "customer_report.de.pdf",
            )
            for name in (
                "technician_report.en.pdf",
                "technician_report.de.pdf",
                "customer_report.en.pdf",
                "customer_report.de.pdf",
            ):
                self.assertTrue((reports_dir / name).is_file())


class PdfOverwriteAndCoexistenceTests(_LanguageIsolatedTestCase):
    def test_overwrite_refusal_is_independent_per_report_language_format(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            session = _session(case_dir)

            Hermes(session, "en").save_technician_pdf()

            # A different language is a different file and must succeed.
            Hermes(session, "de").save_technician_pdf()
            # A different report type must succeed.
            Hermes(session, "en").save_customer_pdf()

            # Re-saving the same (report, language, format) is refused.
            with self.assertRaises(FileExistsError):
                Hermes(session, "en").save_technician_pdf()
            with self.assertRaises(FileExistsError):
                Hermes(session, "de").save_technician_pdf()

    def test_markdown_and_pdf_coexist_and_do_not_block_each_other(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            session = _session(case_dir)
            reports_dir = case_dir / "reports"

            md_path = Hermes(session, "en").save_technician_report()
            md_bytes_before = md_path.read_bytes()

            pdf_path = Hermes(session, "en").save_technician_pdf()

            self.assertTrue(md_path.is_file())
            self.assertTrue(pdf_path.is_file())
            self.assertEqual(md_path.name, technician_report_filename("en"))
            self.assertEqual(pdf_path.name, technician_report_pdf_filename("en"))

            # Generating the PDF left the Markdown file untouched.
            self.assertEqual(md_path.read_bytes(), md_bytes_before)

            # An existing PDF does not block the Markdown of the same language,
            # and vice versa; each format refuses only its own filename.
            with self.assertRaises(FileExistsError):
                Hermes(session, "en").save_technician_report()
            with self.assertRaises(FileExistsError):
                Hermes(session, "en").save_technician_pdf()


class BothFormatWorkflowTests(unittest.TestCase):
    """The 'Both' selection generates each format independently."""

    def _load_offer(self, report_format):
        import ast

        source = (SOURCE_ROOT / "bin" / "sentinel").read_text(encoding="utf-8")
        module = ast.parse(source)

        def extract(name):
            for node in module.body:
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    return ast.get_source_segment(source, node)
            raise ValueError(name)

        namespace = {
            "tr": lambda key, **kwargs: kwargs.get("path", key),
            "print": mock.Mock(),
            "input": mock.Mock(return_value="y"),
            "log_info": mock.Mock(),
            "PdfReportError": PdfReportError,
            "CustomerReportNotCompletedError": __import__(
                "modules.hermes",
                fromlist=["CustomerReportNotCompletedError"],
            ).CustomerReportNotCompletedError,
            "ManifestError": __import__(
                "modules.manifest",
                fromlist=["ManifestError"],
            ).ManifestError,
            "_confirmed_yes": lambda response: response.strip().lower() in ("y", "j"),
            "_prompt_report_language": mock.Mock(return_value="en"),
            "_prompt_report_format": mock.Mock(return_value=report_format),
        }
        exec(extract("_save_report_format"), namespace)
        exec(extract("_offer_report_generation"), namespace)
        return namespace

    def test_both_generates_each_format_independently(self):
        namespace = self._load_offer("both")
        save_markdown = mock.Mock(return_value="/case/reports/technician_report.en.md")
        save_pdf = mock.Mock(return_value="/case/reports/technician_report.en.pdf")

        namespace["_offer_report_generation"](
            mock.Mock(),
            "report.prompt.generate",
            save_markdown,
            save_pdf,
            "report.label.saved_path",
            "report.pdf.label.saved_path",
            "Technician report saved",
            "Technician report PDF saved",
        )

        save_markdown.assert_called_once_with("en")
        save_pdf.assert_called_once_with("en")

    def test_both_pdf_failure_does_not_prevent_markdown_result(self):
        namespace = self._load_offer("both")
        save_markdown = mock.Mock(return_value="/case/reports/technician_report.en.md")
        save_pdf = mock.Mock(side_effect=PdfRenderingError("boom"))

        namespace["_offer_report_generation"](
            mock.Mock(),
            "report.prompt.generate",
            save_markdown,
            save_pdf,
            "report.label.saved_path",
            "report.pdf.label.saved_path",
            "Technician report saved",
            "Technician report PDF saved",
        )

        # Markdown still succeeded and was logged; the PDF failure was reported
        # and swallowed without aborting the report offer.
        save_markdown.assert_called_once_with("en")
        save_pdf.assert_called_once_with("en")
        logged = [call.args[2] for call in namespace["log_info"].call_args_list]
        self.assertIn(
            "Technician report saved: /case/reports/technician_report.en.md", logged
        )

    def test_both_existing_markdown_does_not_prevent_pdf(self):
        namespace = self._load_offer("both")
        save_markdown = mock.Mock(side_effect=FileExistsError("md exists"))
        save_pdf = mock.Mock(return_value="/case/reports/technician_report.en.pdf")

        namespace["_offer_report_generation"](
            mock.Mock(),
            "report.prompt.generate",
            save_markdown,
            save_pdf,
            "report.label.saved_path",
            "report.pdf.label.saved_path",
            "Technician report saved",
            "Technician report PDF saved",
        )

        save_pdf.assert_called_once_with("en")
        logged = [call.args[2] for call in namespace["log_info"].call_args_list]
        self.assertIn(
            "Technician report PDF saved: /case/reports/technician_report.en.pdf",
            logged,
        )


class PdfRendererBoundaryTests(_LanguageIsolatedTestCase):
    """Prove localized and technical values reach real ReportLab flowables."""

    def _story(self, language, manifest=None, kind="technician"):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, manifest or _populated_manifest())
            hermes = Hermes(_session(case_dir), language)
            if kind == "technician":
                report = hermes.build_technician_report(generated_at=FIXED_GENERATED_AT)
            else:
                report = hermes.build_customer_report(generated_at=FIXED_GENERATED_AT)
            formatter = PdfReportFormatter()
            return formatter.build_story(report, tuple(report.keys()), language)

    def test_english_headings_and_labels_reach_flowables(self):
        story = self._story("en")
        pairs = _texts_and_fonts(story)
        texts = [text for text, _ in pairs]

        self.assertIn("Case Information", texts)
        self.assertIn("Recovery Statistics", texts)
        self.assertIn("Case Number", texts)
        # Section heading uses the bold font.
        self.assertIn(("Case Information", FONT_BOLD), pairs)

    def test_german_headings_and_labels_reach_flowables(self):
        story = self._story("de")
        texts = [text for text, _ in _texts_and_fonts(story)]
        self.assertIn("Fallinformationen", texts)
        self.assertIn("Wiederherstellungsstatistik", texts)
        self.assertIn("Fallnummer", texts)

    def test_german_umlauts_reach_flowables(self):
        story = self._story("de", kind="customer")
        self.assertRegex(_joined_text(story), r"[äöüÄÖÜ]")

    def test_recommendations_and_disclaimer_reach_flowables(self):
        story = self._story("en", kind="customer")
        joined = _joined_text(story)
        self.assertIn("Keep at least two independent backups", joined)
        self.assertIn("Data recovery cannot be guaranteed", joined)

    def test_confidentiality_only_present_for_technician(self):
        formatter = PdfReportFormatter()
        self.assertEqual(
            formatter._confidential_text("en", "technician"),
            "INTERNAL \u2014 CONFIDENTIAL",
        )
        self.assertEqual(
            formatter._confidential_text("de", "technician"),
            "INTERN \u2014 VERTRAULICH",
        )
        self.assertIsNone(formatter._confidential_text("en", "customer"))

    def test_confidentiality_marking_drawn_on_technician_pages_only(self):
        rl = pdf_report_formatter._load_reportlab()
        formatter = PdfReportFormatter()

        technician_canvas = _RecordingCanvas()
        formatter._draw_furniture(
            technician_canvas,
            rl=rl,
            title="Technician Report",
            case_identifier="REC-2026-000001",
            confidential_text="INTERNAL \u2014 CONFIDENTIAL",
            generated_text="Generated: 2026-07-16 12:30:00",
        )
        self.assertIn("INTERNAL \u2014 CONFIDENTIAL", technician_canvas.centred)

        customer_canvas = _RecordingCanvas()
        formatter._draw_furniture(
            customer_canvas,
            rl=rl,
            title="Customer Report",
            case_identifier="REC-2026-000001",
            confidential_text=None,
            generated_text="Generated: 2026-07-16 12:30:00",
        )
        self.assertEqual(customer_canvas.centred, [])

    def test_long_path_and_hash_use_monospace_and_render(self):
        story = self._story("en", manifest=_long_value_manifest())
        pairs = _texts_and_fonts(story)

        self.assertIn((LONG_PATH, FONT_MONO), pairs)
        self.assertIn((LONG_HASH, FONT_MONO), pairs)

        # Technical relative artifact paths also use the monospaced font.
        self.assertIn(("images/source.img", FONT_MONO), pairs)

        # And the full document renders without error for these long values.
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _long_value_manifest())
            data = Hermes(_session(case_dir), "en").build_technician_pdf(
                generated_at=FIXED_GENERATED_AT
            )
            self.assertTrue(data.startswith(b"%PDF-"))


class _RecordingCanvas:
    """Minimal canvas that records the text drawing calls the footer makes."""

    def __init__(self):
        self.strings = []
        self.centred = []

    def setFont(self, *args, **kwargs):
        pass

    def setLineWidth(self, *args, **kwargs):
        pass

    def line(self, *args, **kwargs):
        pass

    def drawString(self, x, y, text):
        self.strings.append(text)

    def drawRightString(self, x, y, text):
        self.strings.append(text)

    def drawCentredString(self, x, y, text):
        self.centred.append(text)


class PdfDeterminismTests(_LanguageIsolatedTestCase):
    def test_pinned_timestamp_and_invariant_yield_identical_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            session = _session(case_dir)

            first = Hermes(session, "de").build_technician_pdf(
                generated_at=FIXED_GENERATED_AT, invariant=True
            )
            second = Hermes(session, "de").build_technician_pdf(
                generated_at=FIXED_GENERATED_AT, invariant=True
            )
            self.assertEqual(first, second)

    def test_visible_generation_timestamp_reflects_injected_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            report = Hermes(_session(case_dir), "en").build_technician_report(
                generated_at=FIXED_GENERATED_AT
            )
            self.assertEqual(
                report["Case Information"]["Report Generation Date"],
                FIXED_GENERATED_AT,
            )


class PdfFailureIsolationTests(_LanguageIsolatedTestCase):
    def _case(self, temp_dir):
        case_dir = _case_dir(temp_dir)
        _write_manifest(case_dir, _populated_manifest())
        return case_dir

    def test_missing_reportlab_affects_pdf_only(self):
        saved = {
            name: module
            for name, module in sys.modules.items()
            if name == "reportlab" or name.startswith("reportlab.")
        }
        for name in list(sys.modules):
            if name == "reportlab" or name.startswith("reportlab."):
                del sys.modules[name]
        sys.modules["reportlab"] = None  # forces ImportError on `import reportlab`

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                case_dir = self._case(temp_dir)
                session = _session(case_dir)
                case_json = case_dir / "case.json"
                digest_before = hashlib.sha256(case_json.read_bytes()).hexdigest()

                with self.assertRaises(PdfDependencyError):
                    Hermes(session, "en").save_technician_pdf()

                # No PDF was written.
                self.assertFalse(
                    (case_dir / "reports" / "technician_report.en.pdf").exists()
                )
                # Markdown generation still works without ReportLab.
                md_path = Hermes(session, "en").save_technician_report()
                self.assertTrue(md_path.is_file())
                # case.json is unchanged.
                self.assertEqual(
                    hashlib.sha256(case_json.read_bytes()).hexdigest(),
                    digest_before,
                )
        finally:
            del sys.modules["reportlab"]
            sys.modules.update(saved)

    def test_missing_font_affects_pdf_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._case(temp_dir)
            session = _session(case_dir)
            empty_fonts = Path(temp_dir) / "no-fonts"
            empty_fonts.mkdir()

            with mock.patch.object(pdf_report_formatter, "FONTS_DIR", empty_fonts):
                with self.assertRaises(PdfFontError):
                    Hermes(session, "en").save_technician_pdf()

            self.assertFalse(
                (case_dir / "reports" / "technician_report.en.pdf").exists()
            )
            # Markdown generation is unaffected by a missing font.
            self.assertTrue(Hermes(session, "en").save_technician_report().is_file())

    def test_rendering_exception_does_not_damage_existing_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._case(temp_dir)
            session = _session(case_dir)

            md_path = Hermes(session, "en").save_technician_report()
            md_before = md_path.read_bytes()

            # Pre-existing PDF from a different language must not be touched.
            existing_pdf = Hermes(session, "de").save_technician_pdf()
            existing_pdf_before = existing_pdf.read_bytes()

            with mock.patch.object(
                PdfReportFormatter,
                "format_pdf",
                side_effect=PdfRenderingError("render failure"),
            ):
                with self.assertRaises(PdfRenderingError):
                    Hermes(session, "en").save_technician_pdf()

            # The failed English PDF was never written.
            self.assertFalse(
                (case_dir / "reports" / "technician_report.en.pdf").exists()
            )
            # The Markdown and the pre-existing German PDF are intact.
            self.assertEqual(md_path.read_bytes(), md_before)
            self.assertEqual(existing_pdf.read_bytes(), existing_pdf_before)

    def test_pdf_generation_does_not_mutate_case_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._case(temp_dir)
            session = _session(case_dir)
            case_json = case_dir / "case.json"
            digest_before = hashlib.sha256(case_json.read_bytes()).hexdigest()

            Hermes(session, "en").save_technician_pdf()
            Hermes(session, "de").save_customer_pdf()

            self.assertEqual(
                hashlib.sha256(case_json.read_bytes()).hexdigest(), digest_before
            )

    def test_pdf_generation_does_not_mutate_global_ui_language(self):
        set_language("en", persist=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._case(temp_dir)
            Hermes(_session(case_dir), "de").build_technician_pdf(
                generated_at=FIXED_GENERATED_AT
            )
            self.assertEqual(get_language(), "en")


class MarkdownUnchangedTests(_LanguageIsolatedTestCase):
    def test_markdown_output_unchanged_when_pdf_is_also_generated(self):
        with mock.patch("modules.hermes.datetime", wraps=datetime) as mocked:
            mocked.now.return_value = FIXED_GENERATED_AT
            with tempfile.TemporaryDirectory() as temp_dir:
                case_dir = _case_dir(temp_dir)
                _write_manifest(case_dir, _populated_manifest())
                session = _session(case_dir)

                markdown_before = Hermes(session, "en").build_technician_markdown()
                Hermes(session, "en").build_technician_pdf()
                markdown_after = Hermes(session, "en").build_technician_markdown()

                self.assertEqual(markdown_before, markdown_after)


class ExistingHermesApiCompatibilityTests(_LanguageIsolatedTestCase):
    def test_existing_markdown_apis_still_work_without_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            hermes = Hermes(_session(case_dir), "en")

            technician = hermes.build_technician_report()
            self.assertIn("Case Information", technician)
            self.assertTrue(hermes.build_technician_markdown().startswith("#"))
            self.assertTrue(hermes.save_technician_report().is_file())

            customer = hermes.build_customer_report()
            self.assertIn("Case Information", customer)
            self.assertTrue(hermes.save_customer_report().is_file())


class PdfTitleBlockTests(_LanguageIsolatedTestCase):
    """The internal case name appears on the Technician PDF title block only."""

    def _block_texts(self, *, case_name):
        rl = pdf_report_formatter._load_reportlab()
        pdf_report_formatter._register_fonts(rl)
        formatter = PdfReportFormatter()
        block = formatter._title_block(
            rl,
            formatter._styles(rl),
            title="Report",
            case_identifier="REC-2026-000001",
            case_name=case_name,
            generated_text="Generated: 2026-07-16 12:30:00",
            language="en",
        )
        return "\n".join(text for text, _ in _texts_and_fonts(block))

    def test_technician_style_block_includes_case_name(self):
        texts = self._block_texts(case_name="Customer SSD Recovery")
        self.assertIn("Report", texts)
        self.assertIn("REC-2026-000001", texts)
        self.assertIn("Customer SSD Recovery", texts)
        self.assertIn("Generated: 2026-07-16 12:30:00", texts)

    def test_customer_style_block_omits_case_name(self):
        texts = self._block_texts(case_name=None)
        self.assertIn("REC-2026-000001", texts)
        self.assertIn("Generated: 2026-07-16 12:30:00", texts)
        self.assertNotIn("Customer SSD Recovery", texts)
        self.assertNotIn("Case Name", texts)

    def _title_block_case_name(self, build_method):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            hermes = Hermes(_session(case_dir), "en")
            with mock.patch.object(
                PdfReportFormatter, "format_pdf", return_value=b"%PDF-"
            ) as fmt:
                getattr(hermes, build_method)(generated_at=FIXED_GENERATED_AT)
            return fmt.call_args.kwargs["case_name"]

    def test_technician_pdf_wiring_passes_case_name(self):
        self.assertEqual(
            self._title_block_case_name("build_technician_pdf"),
            "Customer SSD Recovery",
        )

    def test_customer_pdf_wiring_passes_no_case_name(self):
        self.assertIsNone(self._title_block_case_name("build_customer_pdf"))


class PromptReportFormatTests(unittest.TestCase):
    """Direct unit tests for the operator format prompt in bin/sentinel."""

    def _load_prompt(self):
        import ast

        source = (SOURCE_ROOT / "bin" / "sentinel").read_text(encoding="utf-8")
        module = ast.parse(source)
        for node in module.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_prompt_report_format"
            ):
                segment = ast.get_source_segment(source, node)
                break
        else:
            raise ValueError("_prompt_report_format not found")

        prompts = mock.Mock()
        printed = mock.Mock()
        namespace = {
            "tr": lambda key, **kwargs: key,
            "print": printed,
            "input": prompts,
        }
        exec(segment, namespace)
        return namespace["_prompt_report_format"], prompts, printed

    def test_enter_defaults_to_markdown(self):
        prompt, prompts, _ = self._load_prompt()
        prompts.return_value = ""
        self.assertEqual(prompt(), "markdown")

    def test_markdown_selection(self):
        prompt, prompts, _ = self._load_prompt()
        prompts.return_value = "markdown"
        self.assertEqual(prompt(), "markdown")

    def test_pdf_selection(self):
        prompt, prompts, _ = self._load_prompt()
        prompts.return_value = "pdf"
        self.assertEqual(prompt(), "pdf")

    def test_both_selection(self):
        prompt, prompts, _ = self._load_prompt()
        prompts.return_value = "both"
        self.assertEqual(prompt(), "both")

    def test_invalid_input_then_valid_reprompts(self):
        prompt, prompts, printed = self._load_prompt()
        prompts.side_effect = ["nonsense", "2"]
        self.assertEqual(prompt(), "pdf")
        self.assertEqual(prompts.call_count, 2)
        self.assertIn("validation.invalid_selection", printed.call_args_list[-1].args)


class PdfEscapingTests(_LanguageIsolatedTestCase):
    """Values with &, < and > render as literal text without breaking markup."""

    def test_special_characters_render_without_breaking_document(self):
        special = 'Drive "X" <A> & <B>'
        manifest = _populated_manifest()
        manifest["case_name"] = special
        manifest["device"]["model"] = special
        manifest["intake"]["incident_description"] = "Failure & <panic> occurred"

        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, manifest)
            hermes = Hermes(_session(case_dir, case_name=special), "en")

            # The full document renders without a markup-parsing error.
            data = hermes.build_technician_pdf(generated_at=FIXED_GENERATED_AT)
            self.assertTrue(data.startswith(b"%PDF-"))
            self.assertTrue(data.rstrip().endswith(b"%%EOF"))

            # The angle brackets and ampersand are XML-escaped in the flowables,
            # so they reach ReportLab as literal characters, not as markup.
            report = hermes.build_technician_report(generated_at=FIXED_GENERATED_AT)
            joined = _joined_text(
                PdfReportFormatter().build_story(report, tuple(report.keys()), "en")
            )
            self.assertIn("&lt;A&gt;", joined)
            self.assertIn("&amp;", joined)


class PdfTranslationKeyParityTests(unittest.TestCase):
    def test_pdf_and_format_keys_present_in_both_catalogs(self):
        en = json.loads((I18N_DIR / "en.json").read_text(encoding="utf-8"))
        de = json.loads((I18N_DIR / "de.json").read_text(encoding="utf-8"))

        required = {
            "report.format.prompt.title",
            "report.format.option.markdown",
            "report.format.option.pdf",
            "report.format.option.both",
            "report.format.prompt.select",
            "report.pdf.label.saved_path",
            "report.pdf.label.saved_path_customer",
            "report.pdf.error.dependency",
            "report.pdf.error.font",
            "report.pdf.error.rendering",
            "report.pdf.confidential.technician",
            "report.pdf.footer.generated",
            "report.pdf.footer.page",
        }
        self.assertTrue(required.issubset(en.keys()))
        self.assertTrue(required.issubset(de.keys()))

        en_pdf_keys = {
            key for key in en if key.startswith(("report.pdf.", "report.format."))
        }
        de_pdf_keys = {
            key for key in de if key.startswith(("report.pdf.", "report.format."))
        }
        self.assertEqual(en_pdf_keys, de_pdf_keys)


if __name__ == "__main__":
    unittest.main()
