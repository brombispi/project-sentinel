from knowledge.filesystem import FILESYSTEM_KNOWLEDGE


class Codex:
    """
    Structured engineering knowledge service.

    CODEX stores trusted knowledge.
    It does not observe, decide, or execute.
    """

    def __init__(self):
        self.knowledge = {
            "filesystem": FILESYSTEM_KNOWLEDGE
        }

    def register(self, category, key, value):
        if category not in self.knowledge:
            self.knowledge[category] = {}

        self.knowledge[category][key] = value

    def lookup(self, category, key):
        return self.knowledge.get(category, {}).get(key)

    def contains(self, category, key):
        return key in self.knowledge.get(category, {})

    def list_categories(self):
        return list(self.knowledge.keys())

    def list_keys(self, category):
        return list(self.knowledge.get(category, {}).keys())
