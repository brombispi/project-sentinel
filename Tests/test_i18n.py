import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

I18N_DIR = SOURCE_ROOT / "i18n"

from i18n.translator import (
    DEFAULT_LANGUAGE,
    _catalogs,
    config_path,
    get_language,
    init_language,
    operator_message,
    read_config_language,
    set_language,
    tr,
)

CASE_LOADER_SOURCE = (SOURCE_ROOT / "modules" / "case_loader.py").read_text(
    encoding="utf-8"
)
SENTINEL_SOURCE = (SOURCE_ROOT / "bin" / "sentinel").read_text(
    encoding="utf-8"
)


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


def _log_case_load_failure_for_test(session, load_result, log_warning, log_error):
    code = load_result.get("code")
    message = load_result.get("message", "")

    if code == "AMBIGUOUS_SOURCE":
        log_warning(session, "SENTINEL", "Ambiguous source device match.")
        return

    if code == "AMBIGUOUS_DESTINATION":
        log_warning(session, "SENTINEL", "Ambiguous destination device match.")
        return

    if code == "SOURCE_NOT_CONNECTED":
        log_error(session, "SENTINEL", "Source device missing.")
        return

    if code in (
        "DESTINATION_NOT_CONNECTED",
        "DESTINATION_NOT_MOUNTED",
    ):
        log_error(session, "SENTINEL", "Destination device missing.")
        return

    log_error(session, "SENTINEL", f"Case load failed: {message}")


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        self._env_patch = mock.patch.dict(os.environ, {}, clear=True)
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        init_language(SOURCE_ROOT)

    def test_english_strings(self):
        set_language("en", persist=False)
        self.assertEqual(tr("startup.title"), "PROJECT SENTINEL")
        self.assertEqual(
            tr("startup.menu.create"),
            "[1] Create New Recovery Case",
        )

    def test_german_strings(self):
        set_language("de", persist=False)
        self.assertEqual(
            tr("startup.menu.create"),
            "[1] Neuen Wiederherstellungsfall erstellen",
        )
        self.assertNotEqual(
            tr("startup.menu.create"),
            "[1] Create New Recovery Case",
        )

    def test_german_missing_key_falls_back_to_english(self):
        set_language("de", persist=False)
        key = "startup.title"
        saved = _catalogs["de"].pop(key)
        try:
            self.assertEqual(tr(key), "PROJECT SENTINEL")
        finally:
            _catalogs["de"][key] = saved

    def test_missing_key_falls_back_to_bracketed_key(self):
        set_language("en", persist=False)
        self.assertEqual(
            tr("nonexistent.phase.two.key"),
            "[nonexistent.phase.two.key]",
        )

    def test_formatting_placeholders(self):
        set_language("en", persist=False)
        self.assertEqual(
            tr("case.archive.confirm", session_id="REC-2026-000001"),
            "Archive case REC-2026-000001? [y/N]:",
        )

        set_language("de", persist=False)
        self.assertEqual(
            tr("case.archive.confirm", session_id="REC-2026-000001"),
            "Fall REC-2026-000001 archivieren? [j/N]:",
        )

    def test_invalid_language_selection_falls_back_to_english(self):
        with mock.patch.dict(os.environ, {"SENTINEL_LANG": "xx"}):
            language = init_language(SOURCE_ROOT)
        self.assertEqual(language, DEFAULT_LANGUAGE)
        self.assertEqual(get_language(), DEFAULT_LANGUAGE)

    def test_environment_override(self):
        with mock.patch.dict(os.environ, {"SENTINEL_LANG": "de"}):
            language = init_language(SOURCE_ROOT)
        self.assertEqual(language, "de")
        self.assertEqual(get_language(), "de")
        self.assertEqual(
            tr("startup.menu.create"),
            "[1] Neuen Wiederherstellungsfall erstellen",
        )

    def test_config_persistence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            set_language("de", persist=True, project_root=project_root)
            self.assertEqual(read_config_language(project_root), "de")
            self.assertTrue(config_path(project_root).is_file())

            with mock.patch.dict(os.environ, {}, clear=True):
                init_language(project_root)
            self.assertEqual(get_language(), "de")

    def test_malformed_config_does_not_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            path = config_path(project_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not valid json", encoding="utf-8")

            self.assertIsNone(read_config_language(project_root))
            language = init_language(project_root)
            self.assertEqual(language, DEFAULT_LANGUAGE)

    def test_malformed_language_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            broken_pack = Path(temp_dir) / "de.json"
            broken_pack.write_text("{broken", encoding="utf-8")

            with mock.patch(
                "i18n.translator._pack_path",
                side_effect=lambda language: (
                    broken_pack if language == "de" else I18N_DIR / f"{language}.json"
                ),
            ):
                init_language(SOURCE_ROOT)
                set_language("de", persist=False)
                self.assertEqual(tr("startup.title"), "PROJECT SENTINEL")

    def test_language_packs_share_the_same_keys(self):
        en_keys = set(
            json.loads((I18N_DIR / "en.json").read_text(encoding="utf-8")).keys()
        )
        de_keys = set(
            json.loads((I18N_DIR / "de.json").read_text(encoding="utf-8")).keys()
        )
        self.assertEqual(en_keys, de_keys)

        set_language("de", persist=False)
        rendered = tr("case.list.status", status="READY_FOR_IMAGING")
        self.assertIn("READY_FOR_IMAGING", rendered)
        self.assertNotIn("BEREIT", rendered)

    def test_phase2_german_workflow_strings_contain_umlauts(self):
        set_language("de", persist=False)
        samples = (
            tr("imaging.refused.title"),
            tr("validation.invalid_selection"),
            tr("device.source.title"),
            tr("summary.complete"),
        )
        combined = " ".join(samples)
        self.assertRegex(combined, r"[äöüÄÖÜ]")

    def test_phase2_operator_message_uses_code_not_english_message(self):
        set_language("de", persist=False)
        rendered = operator_message(
            {
                "code": "SOURCE_NOT_CONNECTED",
                "message": "Source device is not connected or could not be matched using case.json.",
                "display_args": {"identity_source": "case.json"},
            },
            "load",
        )
        self.assertIn("case.json", rendered)
        self.assertNotEqual(
            rendered,
            "Source device is not connected or could not be matched using case.json.",
        )

    def test_phase2_archive_operator_message_placeholder(self):
        set_language("de", persist=False)
        rendered = operator_message(
            {
                "code": "RELOCATE_SUCCESS",
                "message": "Recovery case relocated to /mnt/backup/Recoveries/REC-2026-000001",
                "display_args": {
                    "dest_path": "/mnt/backup/Recoveries/REC-2026-000001",
                },
            },
            "archive",
        )
        self.assertIn("/mnt/backup/Recoveries/REC-2026-000001", rendered)

    def test_json_packs_load_as_utf8(self):
        de_text = (I18N_DIR / "de.json").read_text(encoding="utf-8")
        self.assertIn("Grösse", de_text)
        self.assertIn("Sprache wählen", de_text)

    def test_sentinel_does_not_pass_translated_strings_to_log_calls(self):
        self.assertNotRegex(
            SENTINEL_SOURCE,
            r"log_(info|error|warning|operator)\([^)]*tr\(",
        )

    def test_sentinel_does_not_branch_on_translated_text(self):
        self.assertNotIn('if tr("', SENTINEL_SOURCE)
        self.assertNotIn("== tr(", SENTINEL_SOURCE)


class SentinelLocalizationRegressionTests(unittest.TestCase):
    def test_confirmed_yes_accepts_localized_affirmatives(self):
        confirmed_yes = _load_sentinel_function("_confirmed_yes")

        for value in ("y", "Y", "j", "J", " y ", "  J  "):
            with self.subTest(value=value):
                self.assertTrue(confirmed_yes(value))

        for value in ("n", ""):
            with self.subTest(value=value):
                self.assertFalse(confirmed_yes(value))

    def test_prompt_startup_menu_redraws_after_language_selection(self):
        namespace = {
            "tr": lambda key, **kwargs: key,
            "get_language": lambda: "en",
            "_language_display_name": lambda code: code,
            "print": mock.Mock(),
        }
        _load_sentinel_function("_print_startup_menu", namespace)
        _load_sentinel_function("_prompt_startup_menu", namespace)

        print_menu = mock.Mock()
        prompt_language = mock.Mock()
        namespace["_print_startup_menu"] = print_menu
        namespace["_prompt_language_selection"] = prompt_language

        with mock.patch("builtins.input", side_effect=["l", "c"]):
            result = namespace["_prompt_startup_menu"]()

        self.assertEqual(result, "cancel")
        self.assertEqual(print_menu.call_count, 2)
        prompt_language.assert_called_once()


class CaseLoaderCodeTests(unittest.TestCase):
    def test_failure_results_declare_stable_codes(self):
        for name, value in (
            ("SOURCE_NOT_CONNECTED", "SOURCE_NOT_CONNECTED"),
            ("AMBIGUOUS_SOURCE", "AMBIGUOUS_SOURCE"),
            ("DESTINATION_NOT_CONNECTED", "DESTINATION_NOT_CONNECTED"),
            ("MANIFEST_ERROR", "MANIFEST_ERROR"),
            ("CASE_PATH_NOT_ACCESSIBLE", "CASE_PATH_NOT_ACCESSIBLE"),
            ("CASE_LOADED", "CASE_LOADED"),
        ):
            with self.subTest(code=value):
                self.assertIn(
                    f'CODE_{name} = "{value}"',
                    CASE_LOADER_SOURCE,
                )

    def test_sentinel_branches_on_load_code_not_message(self):
        self.assertIn("code == CODE_SOURCE_NOT_CONNECTED", SENTINEL_SOURCE)
        self.assertIn("code == CODE_AMBIGUOUS_SOURCE", SENTINEL_SOURCE)
        self.assertIn("code in (", SENTINEL_SOURCE)
        self.assertNotIn(
            '"Source device is not connected" in',
            SENTINEL_SOURCE,
        )
        self.assertNotIn(
            '"Ambiguous source device match" in',
            SENTINEL_SOURCE,
        )

    def test_log_case_load_failure_uses_codes_with_any_message(self):
        session = mock.Mock()
        german_message = (
            "Quellgerät ist nicht angeschlossen oder konnte nicht "
            "zugeordnet werden."
        )

        log_error = mock.Mock()
        _log_case_load_failure_for_test(
            session,
            {
                "code": "SOURCE_NOT_CONNECTED",
                "message": german_message,
            },
            log_warning=mock.Mock(),
            log_error=log_error,
        )
        log_error.assert_called_once_with(
            session,
            "SENTINEL",
            "Source device missing.",
        )


class TechnicianReportOfferTests(unittest.TestCase):
    def _load_offer_function(self):
        namespace = {
            "_confirmed_yes": _load_sentinel_function("_confirmed_yes"),
            "tr": lambda key, **kwargs: kwargs.get("path", key),
            "print": mock.Mock(),
            "input": mock.Mock(),
            "log_info": mock.Mock(),
            "Hermes": mock.Mock(),
        }
        offer = _load_sentinel_function(
            "_offer_technician_report",
            namespace,
        )
        return offer, namespace

    def test_skips_prompt_when_recovery_not_successful(self):
        offer, namespace = self._load_offer_function()
        session = mock.Mock()

        for recovery_result in (None, {"success": False}):
            with self.subTest(recovery_result=recovery_result):
                namespace["input"].reset_mock()
                offer(session, recovery_result)
                namespace["input"].assert_not_called()

    def test_declined_does_not_generate_report(self):
        offer, namespace = self._load_offer_function()
        session = mock.Mock()
        namespace["input"].return_value = "n"

        offer(session, {"success": True})

        namespace["Hermes"].assert_not_called()
        namespace["log_info"].assert_not_called()

    def test_accepted_saves_report_and_logs(self):
        offer, namespace = self._load_offer_function()
        session = mock.Mock()
        report_path = Path("/tmp/recovery/reports/technician_report.md")
        namespace["input"].return_value = "y"
        namespace["Hermes"].return_value.save_technician_report.return_value = (
            report_path
        )

        offer(session, {"success": True})

        namespace["Hermes"].assert_called_once_with(session)
        namespace["Hermes"].return_value.save_technician_report.assert_called_once_with()
        namespace["log_info"].assert_called_once_with(
            session,
            "HERMES",
            f"Technician report saved: {report_path}",
        )

    def test_existing_report_displays_error_without_crashing(self):
        offer, namespace = self._load_offer_function()
        session = mock.Mock()
        namespace["input"].return_value = "y"
        namespace["Hermes"].return_value.save_technician_report.side_effect = (
            FileExistsError(
                "Technician report already exists: "
                "/tmp/recovery/reports/technician_report.md"
            )
        )

        offer(session, {"success": True})

        namespace["print"].assert_called()
        namespace["log_info"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
