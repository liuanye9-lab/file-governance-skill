from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

from ..models.file_record import FileRecord


class BaseCollector(ABC):
    source_type: str = "base"
    supported_extensions: set = set()

    def __init__(self, config: dict, db):
        self.config = config
        self.db = db
        self.max_file_size_mb = config.get("max_file_size_mb", 500)

    @abstractmethod
    def scan(self) -> Iterator[FileRecord]:
        pass

    def _should_skip(self, path: Path) -> bool:
        if not path.is_file():
            return True
        if path.name.startswith("."):
            return True
        if path.name in {"governance.db", "governance.db-journal", "governance.log"}:
            return True
        try:
            if path.stat().st_size > self.max_file_size_mb * 1024 * 1024:
                return True
        except OSError:
            return True
        if path.name.endswith((".tmp", ".crdownload", ".part", ".download")):
            return True
        return False

    def _iter_files(self, root: Path, skip_hidden_dirs: bool = True) -> Iterator[Path]:
        if not root.exists():
            return
        for p in root.rglob("*"):
            if skip_hidden_dirs:
                rel_parts = p.relative_to(root).parts
                if any(part.startswith(".") for part in rel_parts[:-1]):
                    continue
            if not self._should_skip(p):
                yield p
