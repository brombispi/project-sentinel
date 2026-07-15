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


def tr(key, **kwargs):
    template = _catalogs.get(_language, {}).get(key)
    if template is None:
        template = _catalogs.get(DEFAULT_LANGUAGE, {}).get(key)
    if template is None:
        return f"[{key}]"

    if kwargs:
        return template.format(**kwargs)

    return template


def tr_plural(count, stem, **kwargs):
    suffix = "one" if count == 1 else "other"
    return tr(f"{stem}.{suffix}", count=count, **kwargs)
