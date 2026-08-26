from pathlib import Path
from typing import Iterator

from .base import BaseCollector
from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


class LocalFolderCollector(BaseCollector):
    source_type = "local_folder"

    def __init__(self, config: dict, db):
        super().__init__(config, db)
        self.folder_path = Path(config["path"]).expanduser()
        self.session_name = config.get("name", self.folder_path.name)
        self.recursive = config.get("recursive", True)

    def scan(self) -> Iterator[FileRecord]:
        if not self.folder_path.exists():
            logger.warning(f"监控目录不存在: {self.folder_path}")
            return
        iterator = self.folder_path.rglob("*") if self.recursive else self.folder_path.iterdir()
        for file_path in iterator:
            if self._should_skip(file_path):
                continue
            resolved = str(file_path.resolve())
            if self.db.is_path_processed(resolved):
                continue
            try:
                record = FileRecord.from_path(
                    resolved, source="local_folder", source_session=self.session_name
                )
                record.compute_hash()
                yield record
            except Exception as e:
                logger.debug(f"跳过本地文件 {file_path}: {e}")
