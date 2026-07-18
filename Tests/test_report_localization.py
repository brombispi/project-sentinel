"""
Focused tests for HERMES report localization.

These verify that report prose (titles, section headings, field labels,
presentational values, placeholders, customer sentences, recommendations, and
the disclaimer) is localized per report, that recorded facts stay untranslated,
that rendering never mutates the process-global UI language, and that report
files are language-qualified with independent overwrite protection.
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

I18N_DIR = SOURCE_ROOT / "i18n"

from core.session import RecoverySession
from i18n import get_language, init_language, set_language, translate
from i18n.translator import _catalogs
from modules.hermes import (
    CUSTOMER_POLICY_VERSION,
    Hermes,
    customer_report_filename,
    technician_report_filename,
)

SENTINEL_SOURCE = (SOURCE_ROOT / "bin" / "sentinel").read_text(encoding="utf-8")


def _extract_sentinel_function(function_name):
    module = ast.parse(SENTINEL_SOURCE)
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            segment = ast.get_source_segment(SENTINEL_SOURCE, node)
            if segment is None:
                raise ValueError(f"Could not extract {function_name}")
            return segment
    raise ValueError(f"Function {function_name} not found in sentinel")


def _load_sentinel_function(function_name, namespace=None):
    namespace = {} if namespace is None else namespace
    exec(_extract_sentinel_function(function_name), namespace)
    return namespace[function_name]


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
        "created_at": "2026-07-16T10:00:00",
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


def _minimal_manifest():
    return {
        "session_id": "REC-2026-000001",
        "case_name": "",
        "created_at": "2026-07-16T10:00:00",
        "status": "NEW",
    }


class TranslateApiTests(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()

    def tearDown(self):
        init_language(SOURCE_ROOT)
        set_language(self._previous_language, persist=False)

    def test_explicit_language_ignores_global(self):
        set_language("de", persist=False)
        self.assertEqual(translate("report.value.yes", "en"), "Yes")
        self.assertEqual(translate("report.value.yes", "de"), "Ja")

    def test_none_language_uses_global(self):
        set_language("de", persist=False)
        self.assertEqual(translate("report.value.yes"), "Ja")
        set_language("en", persist=False)
        self.assertEqual(translate("report.value.yes"), "Yes")

    def test_unsupported_language_falls_back_to_english(self):
        self.assertEqual(translate("report.value.yes", "xx"), "Yes")
        self.assertEqual(translate("report.value.yes", "fr"), "Yes")

    def test_missing_requested_language_key_falls_back_to_english(self):
        # Ensure the German catalog is loaded before mutating it.
        self.assertEqual(translate("report.value.yes", "de"), "Ja")
        saved = _catalogs["de"].pop("report.value.yes")
        try:
            self.assertEqual(translate("report.value.yes", "de"), "Yes")
        finally:
            _catalogs["de"]["report.value.yes"] = saved

    def test_missing_english_key_returns_bracketed_key(self):
        self.assertEqual(
            translate("report.nonexistent.key", "en"), "[report.nonexistent.key]"
        )

    def test_translate_does_not_mutate_global_language(self):
        set_language("en", persist=False)
        translate("report.value.yes", "de")
        self.assertEqual(get_language(), "en")


class ReportLanguageResolutionTests(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()

    def tearDown(self):
        init_language(SOURCE_ROOT)
        set_language(self._previous_language, persist=False)

    def _hermes(self, language=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _minimal_manifest())
            return Hermes(_session(case_dir), language)

    def test_defaults_to_ui_language(self):
        set_language("de", persist=False)
        self.assertEqual(self._hermes().language, "de")
        set_language("en", persist=False)
        self.assertEqual(self._hermes().language, "en")

    def test_explicit_language_overrides_ui(self):
        set_language("de", persist=False)
        self.assertEqual(self._hermes("en").language, "en")

    def test_unsupported_language_resolves_to_english(self):
        set_language("de", persist=False)
        self.assertEqual(self._hermes("xx").language, "en")


class TechnicianReportLocalizationTests(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()

    def tearDown(self):
        init_language(SOURCE_ROOT)
        set_language(self._previous_language, persist=False)

    def _report(self, language, manifest=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, manifest or _populated_manifest())
            hermes = Hermes(_session(case_dir), language)
            return hermes.build_technician_report(), hermes.build_technician_markdown()

    def test_english_headings_labels_values(self):
        set_language("de", persist=False)  # UI German, report explicitly English
        report, markdown = self._report("en")

        self.assertTrue(markdown.startswith("# Technician Report\n"))
        self.assertIn("Case Information", report)
        self.assertEqual(report["Case Information"]["Case Number"], "REC-2026-000001")
        self.assertEqual(
            report["Recovery Statistics"]["Recovery Attempt Recorded"], "Yes"
        )

    def test_german_headings_labels_values(self):
        set_language("en", persist=False)  # UI English, report explicitly German
        report, markdown = self._report("de")

        self.assertTrue(markdown.startswith("# Technikerbericht\n"))
        self.assertIn("## Fallinformationen", markdown)
        self.assertIn("## Wiederherstellungsstatistik", markdown)

        case_info = report["Fallinformationen"]
        self.assertEqual(case_info["Fallnummer"], "REC-2026-000001")

        statistics = report["Wiederherstellungsstatistik"]
        self.assertEqual(statistics["Wiederherstellungsversuch erfasst"], "Ja")

    def test_german_placeholders(self):
        set_language("en", persist=False)
        report, _ = self._report("de", manifest=_minimal_manifest())

        device = report["Geräteidentität"]
        self.assertEqual(device["SMART-Nachweis"], "Nicht erfasst")

        statistics = report["Wiederherstellungsstatistik"]
        self.assertEqual(statistics["Wiederherstellungsversuch erfasst"], "Nein")
        self.assertEqual(
            statistics["Wiederherstellungs-Ausgabeorte"], "Keine erfasst"
        )

    def test_technical_facts_remain_untranslated(self):
        report_en, _ = self._report("en")
        report_de, _ = self._report("de")

        # Identifiers, timestamps, and hashes/paths are facts, not prose.
        self.assertEqual(report_en["Case Information"]["Case Number"], "REC-2026-000001")
        self.assertEqual(report_de["Fallinformationen"]["Fallnummer"], "REC-2026-000001")
        self.assertEqual(
            report_de["Fallinformationen"]["Erstellungsdatum"], "2026-07-16T10:00:00"
        )
        # Acquisition state code is a technical fact in both languages.
        self.assertEqual(
            report_de["Abbildungsdetails"]["Erwerbsstatuscode"],
            "ACQUISITION_NO_ARTIFACTS",
        )
        self.assertEqual(
            report_de["Abbildungsdetails"]["Abbildpfad"], "images/source.img"
        )

    def test_rendering_does_not_mutate_global_language(self):
        set_language("en", persist=False)
        self._report("de")
        self.assertEqual(get_language(), "en")

        set_language("de", persist=False)
        self._report("en")
        self.assertEqual(get_language(), "de")

    def test_unsupported_language_renders_english(self):
        set_language("de", persist=False)
        _, markdown = self._report("xx")
        self.assertTrue(markdown.startswith("# Technician Report\n"))


class CustomerReportLocalizationTests(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()

    def tearDown(self):
        init_language(SOURCE_ROOT)
        set_language(self._previous_language, persist=False)

    def _report(self, language, manifest=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, manifest or _populated_manifest())
            hermes = Hermes(_session(case_dir), language)
            return hermes.build_customer_report(), hermes.build_customer_markdown()

    def test_english_outcome_recommendations_disclaimer(self):
        set_language("de", persist=False)
        report, markdown = self._report("en")

        self.assertTrue(markdown.startswith("# Customer Report\n"))
        self.assertEqual(
            report["Recovery Outcome"]["Outcome"],
            "The requested data was recovered successfully.",
        )
        self.assertEqual(
            report["Recommendations"]["Guidance"],
            tuple(
                translate(f"report.customer.recommendation.{index}", "en")
                for index in range(1, 5)
            ),
        )
        self.assertEqual(
            report["Disclaimer"]["Terms"],
            tuple(
                translate(f"report.customer.disclaimer.{index}", "en")
                for index in range(1, 5)
            ),
        )
        self.assertEqual(
            report["Recommendations"]["Policy Version"], CUSTOMER_POLICY_VERSION
        )

    def test_german_outcome_recommendations_disclaimer(self):
        set_language("en", persist=False)
        report, markdown = self._report("de")

        self.assertTrue(markdown.startswith("# Kundenbericht\n"))
        outcome = report["Wiederherstellungsergebnis"]["Ergebnis"]
        self.assertEqual(
            outcome, "Die angeforderten Daten wurden erfolgreich wiederhergestellt."
        )

        guidance = report["Empfehlungen"]["Hinweise"]
        self.assertEqual(len(guidance), 4)
        self.assertRegex(" ".join(guidance), r"[äöüÄÖÜ]")

        terms = report["Haftungsausschluss"]["Bedingungen"]
        self.assertEqual(len(terms), 4)
        self.assertIn("Datenwiederherstellung", " ".join(terms))
        # Policy version is a version identifier, not prose.
        self.assertEqual(
            report["Empfehlungen"]["Richtlinienversion"], CUSTOMER_POLICY_VERSION
        )

    def test_german_placeholders(self):
        set_language("en", persist=False)
        report, _ = self._report("de", manifest=_minimal_manifest())

        self.assertEqual(
            report["Fallinformationen"]["Kundenname"], "Nicht erfasst"
        )
        self.assertEqual(
            report["Wiederherstellungsergebnis"]["Ergebnis"],
            "Es wurde kein Wiederherstellungsergebnis erfasst.",
        )

    def test_rendering_does_not_mutate_global_language(self):
        set_language("en", persist=False)
        self._report("de")
        self.assertEqual(get_language(), "en")


class ReportFilenameLocalizationTests(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()
        set_language("en", persist=False)

    def tearDown(self):
        init_language(SOURCE_ROOT)
        set_language(self._previous_language, persist=False)

    def test_all_four_language_qualified_filenames(self):
        self.assertEqual(technician_report_filename("en"), "technician_report.en.md")
        self.assertEqual(technician_report_filename("de"), "technician_report.de.md")
        self.assertEqual(customer_report_filename("en"), "customer_report.en.md")
        self.assertEqual(customer_report_filename("de"), "customer_report.de.md")

    def test_saved_reports_use_language_qualified_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            session = _session(case_dir)

            reports_dir = case_dir / "reports"

            self.assertEqual(
                Hermes(session, "en").save_technician_report(),
                reports_dir / "technician_report.en.md",
            )
            self.assertEqual(
                Hermes(session, "de").save_technician_report(),
                reports_dir / "technician_report.de.md",
            )
            self.assertEqual(
                Hermes(session, "en").save_customer_report(),
                reports_dir / "customer_report.en.md",
            )
            self.assertEqual(
                Hermes(session, "de").save_customer_report(),
                reports_dir / "customer_report.de.md",
            )

            for name in (
                "technician_report.en.md",
                "technician_report.de.md",
                "customer_report.en.md",
                "customer_report.de.md",
            ):
                self.assertTrue((reports_dir / name).is_file())

    def test_per_language_overwrite_protection_is_independent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = _case_dir(temp_dir)
            _write_manifest(case_dir, _populated_manifest())
            session = _session(case_dir)

            Hermes(session, "en").save_technician_report()

            # The German version is a different file and must still succeed.
            Hermes(session, "de").save_technician_report()

            # Re-saving the same language must be refused.
            with self.assertRaises(FileExistsError):
                Hermes(session, "en").save_technician_report()
            with self.assertRaises(FileExistsError):
                Hermes(session, "de").save_technician_report()


class ReportLanguagePromptTests(unittest.TestCase):
    def setUp(self):
        self._previous_language = get_language()

    def tearDown(self):
        init_language(SOURCE_ROOT)
        set_language(self._previous_language, persist=False)

    def _load_prompt(self, ui_language="en"):
        namespace = {
            "tr": lambda key, **kwargs: translate(key, ui_language, **kwargs),
            "get_language": lambda: ui_language,
            "_language_display_name": lambda code: {
                "en": "English",
                "de": "Deutsch",
            }.get(code, code),
            "print": mock.Mock(),
            "input": mock.Mock(),
        }
        prompt = _load_sentinel_function("_prompt_report_language", namespace)
        return prompt, namespace

    def test_empty_input_returns_ui_default(self):
        prompt, namespace = self._load_prompt(ui_language="de")
        namespace["input"].return_value = ""
        self.assertEqual(prompt(), "de")

    def test_numeric_and_code_choices(self):
        for value, expected in (("1", "en"), ("2", "de"), ("en", "en"), ("de", "de")):
            with self.subTest(value=value):
                prompt, namespace = self._load_prompt()
                namespace["input"].return_value = value
                self.assertEqual(prompt(), expected)

    def test_invalid_then_valid_loops(self):
        prompt, namespace = self._load_prompt()
        namespace["input"].side_effect = ["zz", "2"]
        self.assertEqual(prompt(), "de")
        namespace["print"].assert_called()

    def test_prompt_does_not_mutate_global_language(self):
        set_language("en", persist=False)
        prompt, namespace = self._load_prompt(ui_language="en")
        namespace["input"].return_value = "de"
        prompt()
        self.assertEqual(get_language(), "en")


class IndependentReportLanguageSelectionTests(unittest.TestCase):
    def _load_delivery(self):
        namespace = {
            "_confirmed_yes": _load_sentinel_function("_confirmed_yes"),
            "tr": lambda key, **kwargs: kwargs.get("path", key),
            "print": mock.Mock(),
            "input": mock.Mock(),
            "log_info": mock.Mock(),
            "Hermes": mock.Mock(),
            "_prompt_report_language": mock.Mock(),
        }
        _load_sentinel_function("_offer_report_generation", namespace)
        delivery = _load_sentinel_function("_run_delivery_workflow", namespace)
        return delivery, namespace

    def test_technician_and_customer_use_independent_languages(self):
        delivery, namespace = self._load_delivery()
        session = mock.Mock()
        namespace["input"].side_effect = ["y", "y"]
        namespace["_prompt_report_language"].side_effect = ["en", "de"]

        delivery(session, mock.Mock(), {"intake": {}}, recovery_result={"success": True})

        # Each report resolved its own language and passed it to Hermes.
        self.assertEqual(namespace["_prompt_report_language"].call_count, 2)
        self.assertEqual(namespace["Hermes"].call_args_list[0], mock.call(session, "en"))
        self.assertEqual(namespace["Hermes"].call_args_list[1], mock.call(session, "de"))


if __name__ == "__main__":
    unittest.main()
