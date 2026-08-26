import os
from pathlib import Path
from typing import Iterator

from .base import BaseCollector
from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


class WechatCollector(BaseCollector):
    source_type = "wechat"

    def __init__(self, config: dict, db):
        super().__init__(config, db)
        self.base_path = Path(config.get("base_path", "")).expanduser()
        self.fixed_chat_path = config.get("fixed_chat_path", "")

    def scan(self) -> Iterator[FileRecord]:
        scan_roots = []
        if self.fixed_chat_path:
            p = Path(self.fixed_chat_path).expanduser()
            if p.exists():
                scan_roots.append((p, self.fixed_chat_path))
        elif self.base_path.exists():
            scan_roots.append((self.base_path, "wechat"))
            for child in self.base_path.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    pass
        else:
            logger.warning(f"微信目录不存在: {self.base_path}")
            return

        for root, session_name in scan_roots:
            for file_path in self._iter_files(root):
                if self.db.is_path_processed(str(file_path.resolve())):
                    continue
                try:
                    record = FileRecord.from_path(
                        str(file_path),
                        source="wechat",
                        source_session=session_name,
                    )
                    record.compute_hash()
                    yield record
                except Exception as e:
                    logger.debug(f"跳过文件 {file_path}: {e}")
