import json
from pathlib import Path


class SessionRegistry:

    def __init__(self):
        project_root = Path(__file__).resolve().parent.parent
        self.registry_path = project_root / "state" / "session_registry.json"

    def load(self):
        with open(self.registry_path, "r") as file:
            return json.load(file)

    def next_session_id(self):
        registry = self.load()

        year = registry["year"]
        number = registry["last_number"] + 1

        registry["last_number"] = number
        self.save(registry)

        return f"REC-{year}-{number:06d}"

    def save(self, registry):
        with open(self.registry_path, "w") as file:
            json.dump(registry, file, indent=4)