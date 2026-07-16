#!/usr/bin/env python3

import sys
from pathlib import Path

sys.path.append("/Users/digirettung/Documents/Project Sentinel/Source")

from core.codex import Codex
from i18n import init_language, tr

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_i18n_initialized = False


def _display_codex_value(value):
    global _i18n_initialized

    if isinstance(value, str) and value.startswith("codex."):
        if not _i18n_initialized:
            init_language(PROJECT_ROOT)
            _i18n_initialized = True
        return tr(value)

    return value


class Sage:
    """
    Knowledge explanation subsystem.

    SAGE explains knowledge from CODEX.
    It does not observe, decide, or execute.
    """

    def __init__(self, codex):
        self.codex = codex

    def explain(self, category, key):
        entry = self.codex.lookup(category, key)

        if entry is None:
            return f"No knowledge found for {category}.{key}"

        lines = []

        lines.append(f"SAGE")
        lines.append(f"Knowledge Explanation")
        lines.append("")
        lines.append(f"Category: {category}")
        lines.append(f"Topic   : {key}")
        lines.append("")

        for field, value in entry.items():
            label = field.replace("_", " ").title()
            lines.append(f"{label}: {_display_codex_value(value)}")

        return "\n".join(lines)


if __name__ == "__main__":
    codex = Codex()

    codex.register("filesystem", "ntfs", {
        "warning": "Do not run CHKDSK before imaging.",
        "risk": "CHKDSK may modify filesystem metadata.",
        "recommended_action": "Create an image before repair attempts."
    })

    sage = Sage(codex)
    print(sage.explain("filesystem", "ntfs"))
