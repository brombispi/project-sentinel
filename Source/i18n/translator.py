import json
import os
import sys
from pathlib import Path

SUPPORTED_LANGUAGES = ("en", "de")
DEFAULT_LANGUAGE = "en"

_language = DEFAULT_LANGUAGE
_catalogs = {}
_project_root = None


def _i18n_dir():
    return Path(__file__).resolve().parent


def _pack_path(language):
    return _i18n_dir() / f"{language}.json"


def _pack_exists(language):
    return _pack_path(language).is_file()


def _load_catalog(language):
    try:
        with open(_pack_path(language), encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass

    print(
        f"Warning: could not load language pack '{language}'.",
        file=sys.stderr,
    )
    return {}


def _ensure_catalog(language):
    if language not in _catalogs:
        _catalogs[language] = _load_catalog(language)


def _resolve_language(language):
    normalized = str(language).strip().lower()
    if normalized in SUPPORTED_LANGUAGES and _pack_exists(normalized):
        return normalized
    return DEFAULT_LANGUAGE


def config_path(project_root):
    return Path(project_root) / "state" / "sentinel_config.json"


def read_config_language(project_root):
    path = config_path(project_root)
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    return str(data.get("language", "")).strip().lower() or None


def persist_language(project_root, language):
    path = config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}

    if not isinstance(existing, dict):
        existing = {}

    existing["language"] = _resolve_language(language)
    path.write_text(
        json.dumps(existing, indent=4) + "\n",
        encoding="utf-8",
    )


# TestDisk execution configuration lives in the SAME sentinel_config.json as the
# language setting, under an optional top-level "testdisk" object, and is read
# through the same file path, JSON parser, and fail-safe handling used for the
# language field. Host-specific values (recovery account, forbidden groups,
# privilege-drop mechanism, execution mode) are deployment-owned and are NOT
# shipped as application defaults: a missing or incomplete block therefore stays
# missing and fails closed at the caller. The only field that may default is the
# working-copy safety margin (64 MiB); it is a conservative headroom, not a
# host-specific value.
TESTDISK_DEFAULT_SAFETY_MARGIN_BYTES = 64 * 1024 * 1024
_TESTDISK_EXECUTION_MODES = ("root", "sudo", "external")
# Privilege-drop mechanisms Sentinel can actually construct a command for. Only
# setpriv is supported today (§7A / §11); an arbitrary command name/template is
# NOT accepted, so command-injection and unvalidated-template risks cannot enter
# through configuration. This is a supported-set check, not a default: the field
# stays required and host configuration must still name it explicitly.
_TESTDISK_SUPPORTED_DROP_MECHANISMS = ("setpriv",)


def _testdisk_config_error(code, message, *, field=None):
    error = {"success": False, "code": code, "message": message}
    if field is not None:
        error["field"] = field
    return error


def _require_config_string(block, field):
    value = block.get(field)
    if value is None:
        return None, _testdisk_config_error(
            "TESTDISK_CONFIG_MISSING_FIELD",
            f"Missing required field: {field}",
            field=field,
        )
    if not isinstance(value, str) or not value.strip():
        return None, _testdisk_config_error(
            "TESTDISK_CONFIG_INVALID_FIELD",
            f"Field must be a non-empty string: {field}",
            field=field,
        )
    return value.strip(), None


def _validate_testdisk_block(block):
    normalized = {}

    account, error = _require_config_string(block, "recovery_account")
    if error is not None:
        return error
    normalized["recovery_account"] = account

    groups = block.get("forbidden_groups")
    if groups is None:
        return _testdisk_config_error(
            "TESTDISK_CONFIG_MISSING_FIELD",
            "Missing required field: forbidden_groups",
            field="forbidden_groups",
        )
    if not isinstance(groups, list) or not groups:
        return _testdisk_config_error(
            "TESTDISK_CONFIG_INVALID_FIELD",
            "Field must be a non-empty list of strings: forbidden_groups",
            field="forbidden_groups",
        )
    normalized_groups = []
    for item in groups:
        if not isinstance(item, str) or not item.strip():
            return _testdisk_config_error(
                "TESTDISK_CONFIG_INVALID_FIELD",
                "forbidden_groups must contain only non-empty strings.",
                field="forbidden_groups",
            )
        normalized_groups.append(item.strip())
    normalized["forbidden_groups"] = normalized_groups

    mechanism, error = _require_config_string(block, "privilege_drop_mechanism")
    if error is not None:
        return error
    if mechanism not in _TESTDISK_SUPPORTED_DROP_MECHANISMS:
        return _testdisk_config_error(
            "TESTDISK_CONFIG_INVALID_MECHANISM",
            f"Unsupported privilege_drop_mechanism: {mechanism}. "
            f"Supported: {', '.join(_TESTDISK_SUPPORTED_DROP_MECHANISMS)}.",
            field="privilege_drop_mechanism",
        )
    normalized["privilege_drop_mechanism"] = mechanism

    mode = block.get("execution_mode")
    if mode is None:
        return _testdisk_config_error(
            "TESTDISK_CONFIG_MISSING_FIELD",
            "Missing required field: execution_mode",
            field="execution_mode",
        )
    if not isinstance(mode, str):
        return _testdisk_config_error(
            "TESTDISK_CONFIG_INVALID_FIELD",
            "Field must be a string: execution_mode",
            field="execution_mode",
        )
    normalized_mode = mode.strip().lower()
    if normalized_mode not in _TESTDISK_EXECUTION_MODES:
        return _testdisk_config_error(
            "TESTDISK_CONFIG_INVALID_MODE",
            f"Unsupported execution_mode: {mode}",
            field="execution_mode",
        )
    normalized["execution_mode"] = normalized_mode

    margin = block.get("working_copy_safety_margin_bytes")
    if margin is None:
        normalized["working_copy_safety_margin_bytes"] = (
            TESTDISK_DEFAULT_SAFETY_MARGIN_BYTES
        )
    else:
        # bool is a subclass of int; reject it explicitly.
        if isinstance(margin, bool) or not isinstance(margin, int):
            return _testdisk_config_error(
                "TESTDISK_CONFIG_INVALID_FIELD",
                "Field must be a non-negative integer: "
                "working_copy_safety_margin_bytes",
                field="working_copy_safety_margin_bytes",
            )
        if margin < 0:
            return _testdisk_config_error(
                "TESTDISK_CONFIG_NEGATIVE_MARGIN",
                "working_copy_safety_margin_bytes must not be negative.",
                field="working_copy_safety_margin_bytes",
            )
        normalized["working_copy_safety_margin_bytes"] = margin

    return {"success": True, "config": normalized}


def read_testdisk_config(project_root):
    """
    Read the optional "testdisk" object from sentinel_config.json.

    Tri-state return:
      * None — no "testdisk" block is configured (file missing/unreadable, not a
        JSON object, or the key is absent). TestDisk is simply not configured;
        the caller fails closed because the required identity/mechanism/mode are
        unavailable.
      * {"success": False, "code": ..., "message": ..., ["field": ...]} — the
        block is present but invalid (malformed JSON, non-object block, a
        missing/blank/wrong-typed required field, an unsupported privilege-drop
        mechanism, an unsupported execution mode, or a negative margin). The
        caller fails closed on the structured error.
      * {"success": True, "config": {...}} — a validated, normalized config with
        recovery_account, forbidden_groups, privilege_drop_mechanism,
        execution_mode, and working_copy_safety_margin_bytes (defaulted to
        64 MiB when omitted).

    Reuses config_path() and the same JSON parser and fail-safe handling as the
    language configuration; it never raises for a missing or malformed file and
    leaves language reading untouched.
    """

    path = config_path(project_root)
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except json.JSONDecodeError:
        return _testdisk_config_error(
            "TESTDISK_CONFIG_MALFORMED",
            "sentinel_config.json is not valid JSON.",
        )

    if not isinstance(data, dict) or "testdisk" not in data:
        return None

    block = data["testdisk"]
    if not isinstance(block, dict):
        return _testdisk_config_error(
            "TESTDISK_CONFIG_INVALID_BLOCK",
            "The 'testdisk' configuration must be an object.",
            field="testdisk",
        )

    return _validate_testdisk_block(block)


def init_language(project_root=None):
    """
    Load language packs and resolve the active language.

    Resolution order:
    1. SENTINEL_LANG environment variable
    2. Source/state/sentinel_config.json
    3. English fallback
    """

    global _language, _project_root

    _project_root = project_root

    requested = os.environ.get("SENTINEL_LANG", "").strip().lower()
    if not requested and project_root is not None:
        requested = read_config_language(project_root) or ""

    resolved = _resolve_language(requested)
    if requested and requested != resolved:
        print(
            f"Warning: unsupported language '{requested}'; using English.",
            file=sys.stderr,
        )

    _catalogs.clear()
    _ensure_catalog(DEFAULT_LANGUAGE)
    if resolved != DEFAULT_LANGUAGE:
        _ensure_catalog(resolved)

    _language = resolved
    return resolved


def get_language():
    return _language


def set_language(language, persist=True, project_root=None):
    global _language

    resolved = _resolve_language(language)
    _ensure_catalog(DEFAULT_LANGUAGE)
    if resolved != DEFAULT_LANGUAGE:
        _ensure_catalog(resolved)
    _language = resolved

    if persist:
        root = project_root if project_root is not None else _project_root
        if root is not None:
            persist_language(root, resolved)

    return resolved


def translate(key, language=None, **kwargs):
    """
    Translate a key in an explicit language without mutating global state.

    When language is None the current process-global UI language is used
    (preserving tr() behavior). An unsupported explicit language resolves
    safely to English. The fallback order is:
    requested language -> English -> "[key]".
    """

    if language is None:
        lang = _language
    else:
        lang = _resolve_language(language)

    _ensure_catalog(lang)

    template = _catalogs.get(lang, {}).get(key)
    if template is None:
        template = _catalogs.get(DEFAULT_LANGUAGE, {}).get(key)
    if template is None:
        return f"[{key}]"

    if kwargs:
        return template.format(**kwargs)

    return template


def tr(key, **kwargs):
    return translate(key, None, **kwargs)


def tr_plural(count, stem, **kwargs):
    suffix = "one" if count == 1 else "other"
    return tr(f"{stem}.{suffix}", count=count, **kwargs)


def operator_message(result, namespace):
    """
    Translate an operator-facing result message by stable code.

    Falls back to the English message when no translation key exists.
    """

    code = result.get("code")
    if code:
        key = f"{namespace}.message.{str(code).lower()}"
        display_args = result.get("display_args") or {}
        rendered = tr(key, **display_args)
        if not rendered.startswith("["):
            return rendered

    return result.get("message", "")


def display_aegis_reason(reason):
    mapping = {
        "Target is the Recovery Engine.": "aegis.reason.recovery_engine",
        "External device.": "aegis.reason.external_device",
        "Source device is currently mounted.": "aegis.reason.mounted_source",
        "Source device identity cannot be trusted.": (
            "aegis.reason.unidentified_source"
        ),
    }
    key = mapping.get(reason)
    if key:
        return tr(key)
    return reason


def display_aegis_recommendation(recommendation):
    mapping = {
        "Select an external customer storage device.": (
            "aegis.recommendation.recovery_engine"
        ),
        "Unmount the source device before continuing.": (
            "aegis.recommendation.unmounted_source"
        ),
        "Verify the physical source device and obtain a trustworthy "
        "serial before continuing.": "aegis.recommendation.unidentified_source",
    }
    key = mapping.get(recommendation)
    if key:
        return tr(key)
    return recommendation


def display_janus_reason(assessment, mount_point=None):
    if assessment.approved and mount_point:
        return tr("janus.reason.approved", mount_point=mount_point)

    mapping = {
        "Recovery Engine cannot be used as a recovery destination.": (
            "janus.reason.recovery_engine_destination"
        ),
        "Destination is not mounted or has no writable mount point.": (
            "janus.reason.not_mounted"
        ),
    }
    key = mapping.get(assessment.reason)
    if key:
        return tr(key)
    return assessment.reason


def display_smart_warning(warning):
    mapping = {
        "smartctl is not installed.": "smart.warning.smartctl_not_installed",
        "smartctl produced no output.": "smart.warning.no_output",
    }
    key = mapping.get(warning)
    if key:
        return tr(key)
    return warning


def display_oracle_goal(goal):
    mapping = {
        "Protect the original device.": "oracle.goal.protect_device",
        "Preserve the original device.": "oracle.goal.preserve_device",
    }
    key = mapping.get(goal)
    if key:
        return tr(key)
    return goal


def display_oracle_step(step, recommendation=None):
    mapping = {
        "Do not perform any recovery operation.": "oracle.step.stop_no_recovery",
        "Create a forensic image.": "oracle.step.create_forensic_image",
        "Verify image integrity.": "oracle.step.verify_image_integrity",
        "Perform recovery on the image, not the original device.": (
            "oracle.step.recover_on_image"
        ),
    }
    key = mapping.get(step)
    if key:
        return tr(key)
    displayed = display_aegis_recommendation(step)
    if displayed != step:
        return displayed
    if recommendation is not None and step == recommendation:
        return tr("oracle.step.recommendation", recommendation=step)
    return step
