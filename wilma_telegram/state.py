import json
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seen: set[str] = set()
        self.wilma_initialized = False
        self.daisy_initialized = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.seen = set(data.get("seen", []))
        self.wilma_initialized = bool(data.get("wilma_initialized", data.get("initialized", False)))
        self.daisy_initialized = bool(data.get("daisy_initialized", False))

    def save(self) -> None:
        payload = {
            "seen": sorted(self.seen),
            "wilma_initialized": self.wilma_initialized,
            "daisy_initialized": self.daisy_initialized,
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def message_key(self, scope: str, message_id: int | str) -> str:
        return f"{scope}:{message_id}"

    def is_seen(self, scope: str, message_id: int | str) -> bool:
        return self.message_key(scope, message_id) in self.seen

    def mark_seen(self, scope: str, message_id: int | str) -> None:
        self.seen.add(self.message_key(scope, message_id))
