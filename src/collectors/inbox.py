from pathlib import Path
from typing import Iterator

from .base import BaseCollector
from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


class InboxCollector(BaseCollector):
    source_type = "inbox"

    def __init__(self, config: dict, db):
        super().__init__(config, db)
        path = config.get("path") or config.get("inbox_path", "./inbox")
        self.inbox_path = Path(path).expanduser()
        self.inbox_path.mkdir(parents=True, exist_ok=True)

    def scan(self) -> Iterator[FileRecord]:
        if not self.inbox_path.exists():
            return
        for file_path in self._iter_files(self.inbox_path):
            resolved = str(file_path.resolve())
            if self.db.is_path_processed(resolved):
                continue
            try:
                record = FileRecord.from_path(
                    resolved, source="inbox", source_session="拖拽收件箱"
                )
                record.compute_hash()
                yield record
            except Exception as e:
                logger.debug(f"跳过收件箱文件 {file_path}: {e}")
