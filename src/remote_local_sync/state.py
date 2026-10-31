"""What both copies looked like right after the last sync.

With this record the client can tell "the other side changed since we last synced"
from "the other side is just older", which is how it avoids overwriting or
deleting work done on the other side.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

VERSION = 1


class State:
    def __init__(self, path: Path, server_id: str) -> None:
        self.path = path
        self.server_id = server_id
        self.files: Dict[str, Dict[str, dict]] = {}
        self.dirty = False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return
        if data.get("version") == VERSION and data.get("server") == server_id:
            self.files = data.get("files", {})

    def get(self, mkey: str, rel: str) -> Optional[dict]:
        return self.files.get(mkey, {}).get(rel)

    def set(self, mkey: str, rel: str, local: Iterable[int], remote: Iterable[int]) -> None:
        rec = {"l": list(local), "r": list(remote)}
        if self.files.get(mkey, {}).get(rel) != rec:
            self.files.setdefault(mkey, {})[rel] = rec
            self.dirty = True

    def drop(self, mkey: str, rel: str) -> None:
        if self.files.get(mkey, {}).pop(rel, None) is not None:
            self.dirty = True

    def prune(self, mkey: str, start: str, keep: Iterable[str]) -> None:
        """Forget files under start that no longer exist on either side."""
        keep = set(keep)
        records = self.files.get(mkey, {})
        prefix = start + "/" if start else ""
        gone: List[str] = [rel for rel in records
                           if (not start or rel == start or rel.startswith(prefix)) and rel not in keep]
        for rel in gone:
            del records[rel]
        if gone:
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"version": VERSION, "server": self.server_id, "files": self.files},
                                  indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)
        self.dirty = False
