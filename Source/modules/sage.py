#!/usr/bin/env python3

import sys

sys.path.append("/Users/digirettung/Documents/Project Sentinel/Source")

from core.codex import Codex


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
            lines.append(f"{label}: {value}")

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
