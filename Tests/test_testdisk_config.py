import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

from i18n.translator import (  # noqa: E402
    TESTDISK_DEFAULT_SAFETY_MARGIN_BYTES,
    config_path,
    persist_language,
    read_config_language,
    read_testdisk_config,
)

VALID_BLOCK = {
    "recovery_account": "recovery-user",
    "forbidden_groups": ["group-a", "group-b"],
    "privilege_drop_mechanism": "setpriv",
    "execution_mode": "sudo",
    "working_copy_safety_margin_bytes": 12345,
}


class TestDiskConfigTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_root = Path(self._tmp.name)

    def _write_config(self, document):
        path = config_path(self.project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document, indent=4), encoding="utf-8")
        return path

    def _write_testdisk(self, block, *, extra=None):
        document = {"testdisk": block}
        if extra:
            document.update(extra)
        return self._write_config(document)


class ValidConfigTests(TestDiskConfigTestBase):
    def test_valid_complete_block(self):
        self._write_testdisk(VALID_BLOCK)
        result = read_testdisk_config(self.project_root)
        self.assertTrue(result["success"])
        self.assertEqual(
            result["config"],
            {
                "recovery_account": "recovery-user",
                "forbidden_groups": ["group-a", "group-b"],
                "privilege_drop_mechanism": "setpriv",
                "execution_mode": "sudo",
                "working_copy_safety_margin_bytes": 12345,
            },
        )

    def test_optional_margin_defaults(self):
        block = dict(VALID_BLOCK)
        del block["working_copy_safety_margin_bytes"]
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertTrue(result["success"])
        self.assertEqual(
            result["config"]["working_copy_safety_margin_bytes"],
            TESTDISK_DEFAULT_SAFETY_MARGIN_BYTES,
        )
        self.assertEqual(TESTDISK_DEFAULT_SAFETY_MARGIN_BYTES, 67108864)

    def test_zero_margin_is_accepted(self):
        block = dict(VALID_BLOCK, working_copy_safety_margin_bytes=0)
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertTrue(result["success"])
        self.assertEqual(result["config"]["working_copy_safety_margin_bytes"], 0)

    def test_values_are_normalized(self):
        block = dict(
            VALID_BLOCK,
            recovery_account="  recovery-user  ",
            forbidden_groups=[" group-a ", "group-b"],
            privilege_drop_mechanism="  setpriv ",
            execution_mode="EXTERNAL",
        )
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertTrue(result["success"])
        self.assertEqual(result["config"]["recovery_account"], "recovery-user")
        self.assertEqual(
            result["config"]["forbidden_groups"], ["group-a", "group-b"]
        )
        self.assertEqual(
            result["config"]["privilege_drop_mechanism"], "setpriv"
        )
        self.assertEqual(result["config"]["execution_mode"], "external")


class DropMechanismConstraintTests(TestDiskConfigTestBase):
    """privilege_drop_mechanism is constrained to the supported set (setpriv)."""

    def test_setpriv_mechanism_is_accepted(self):
        block = dict(VALID_BLOCK, privilege_drop_mechanism="setpriv")
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertTrue(result["success"])
        self.assertEqual(
            result["config"]["privilege_drop_mechanism"], "setpriv"
        )

    def test_unknown_mechanism_is_rejected(self):
        block = dict(VALID_BLOCK, privilege_drop_mechanism="rm -rf /")
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_MECHANISM")
        self.assertEqual(result["field"], "privilege_drop_mechanism")

    def test_arbitrary_command_template_is_rejected(self):
        block = dict(
            VALID_BLOCK,
            privilege_drop_mechanism="sudo setpriv --reuid={uid}",
        )
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_MECHANISM")

    def test_no_mechanism_default_is_introduced(self):
        # Omitting the mechanism must stay a fail-closed MISSING_FIELD; a
        # default (e.g. setpriv) must NOT be silently supplied.
        block = dict(VALID_BLOCK)
        del block["privilege_drop_mechanism"]
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_MISSING_FIELD")
        self.assertEqual(result["field"], "privilege_drop_mechanism")
        self.assertNotIn("config", result)


class AbsentConfigTests(TestDiskConfigTestBase):
    def test_missing_file_returns_none(self):
        self.assertIsNone(read_testdisk_config(self.project_root))

    def test_missing_block_returns_none(self):
        self._write_config({"language": "de"})
        self.assertIsNone(read_testdisk_config(self.project_root))

    def test_document_not_object_returns_none(self):
        self._write_config("[1, 2, 3]")
        self.assertIsNone(read_testdisk_config(self.project_root))


class InvalidConfigTests(TestDiskConfigTestBase):
    def test_block_not_object_is_failure(self):
        self._write_config({"testdisk": ["not", "an", "object"]})
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_BLOCK")

    def test_missing_recovery_account(self):
        block = dict(VALID_BLOCK)
        del block["recovery_account"]
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_MISSING_FIELD")
        self.assertEqual(result["field"], "recovery_account")

    def test_missing_forbidden_groups(self):
        block = dict(VALID_BLOCK)
        del block["forbidden_groups"]
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_MISSING_FIELD")
        self.assertEqual(result["field"], "forbidden_groups")

    def test_missing_privilege_drop_mechanism(self):
        block = dict(VALID_BLOCK)
        del block["privilege_drop_mechanism"]
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_MISSING_FIELD")
        self.assertEqual(result["field"], "privilege_drop_mechanism")

    def test_missing_execution_mode(self):
        block = dict(VALID_BLOCK)
        del block["execution_mode"]
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_MISSING_FIELD")
        self.assertEqual(result["field"], "execution_mode")

    def test_blank_recovery_account_is_invalid(self):
        block = dict(VALID_BLOCK, recovery_account="   ")
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "recovery_account")

    def test_recovery_account_wrong_type(self):
        block = dict(VALID_BLOCK, recovery_account=123)
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "recovery_account")

    def test_forbidden_groups_wrong_type(self):
        block = dict(VALID_BLOCK, forbidden_groups="group-a")
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "forbidden_groups")

    def test_forbidden_groups_empty_list_is_invalid(self):
        block = dict(VALID_BLOCK, forbidden_groups=[])
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "forbidden_groups")

    def test_forbidden_groups_with_blank_entry_is_invalid(self):
        block = dict(VALID_BLOCK, forbidden_groups=["group-a", "  "])
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "forbidden_groups")

    def test_forbidden_groups_with_non_string_entry_is_invalid(self):
        block = dict(VALID_BLOCK, forbidden_groups=["group-a", 7])
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "forbidden_groups")

    def test_execution_mode_wrong_type(self):
        block = dict(VALID_BLOCK, execution_mode=1)
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "execution_mode")

    def test_unsupported_execution_mode(self):
        block = dict(VALID_BLOCK, execution_mode="wizardry")
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_MODE")
        self.assertEqual(result["field"], "execution_mode")

    def test_margin_wrong_type_string(self):
        block = dict(VALID_BLOCK, working_copy_safety_margin_bytes="lots")
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "working_copy_safety_margin_bytes")

    def test_margin_bool_is_rejected(self):
        block = dict(VALID_BLOCK, working_copy_safety_margin_bytes=True)
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_INVALID_FIELD")
        self.assertEqual(result["field"], "working_copy_safety_margin_bytes")

    def test_negative_margin(self):
        block = dict(VALID_BLOCK, working_copy_safety_margin_bytes=-1)
        self._write_testdisk(block)
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_NEGATIVE_MARGIN")
        self.assertEqual(result["field"], "working_copy_safety_margin_bytes")

    def test_malformed_json_is_failure(self):
        self._write_config("{ not valid json ]")
        result = read_testdisk_config(self.project_root)
        self.assertIsNotNone(result)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_MALFORMED")


class CoexistenceTests(TestDiskConfigTestBase):
    def test_language_reading_unchanged_with_testdisk_block(self):
        self._write_testdisk(VALID_BLOCK, extra={"language": "de"})
        # Language reader still returns the language, unaffected by testdisk.
        self.assertEqual(read_config_language(self.project_root), "de")
        # TestDisk reader still returns the validated block.
        result = read_testdisk_config(self.project_root)
        self.assertTrue(result["success"])

    def test_language_reading_unchanged_on_malformed_json(self):
        # Existing behavior: malformed file => language reader returns None.
        self._write_config("{ not valid json ]")
        self.assertIsNone(read_config_language(self.project_root))
        # TestDisk reader fails closed with a structured error on the same file.
        result = read_testdisk_config(self.project_root)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_MALFORMED")

    def test_unrelated_top_level_keys_are_ignored(self):
        self._write_testdisk(
            VALID_BLOCK,
            extra={"language": "en", "unrelated_key": {"nested": [1, 2, 3]}},
        )
        result = read_testdisk_config(self.project_root)
        self.assertTrue(result["success"])
        self.assertNotIn("unrelated_key", result["config"])
        self.assertNotIn("language", result["config"])

    def test_persist_language_preserves_testdisk_block(self):
        # Backward-compatibility guard: writing the language must not drop or
        # mutate the testdisk block or unrelated top-level data.
        original_testdisk = copy.deepcopy(VALID_BLOCK)
        unrelated = {"nested": [1, 2, 3], "flag": True}
        self._write_config(
            {
                "language": "en",
                "testdisk": copy.deepcopy(original_testdisk),
                "unrelated_key": copy.deepcopy(unrelated),
            }
        )

        persist_language(self.project_root, "de")

        document = json.loads(
            config_path(self.project_root).read_text(encoding="utf-8")
        )
        self.assertEqual(document["language"], "de")
        self.assertEqual(document["testdisk"], original_testdisk)
        self.assertEqual(document["unrelated_key"], unrelated)


if __name__ == "__main__":
    unittest.main()
