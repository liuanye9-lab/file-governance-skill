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
        # scan_roots: [(目录, 会话名)]。指定 fixed_chat_path 时只扫该目录；
        # 否则把 base_path 下每个聊天子目录作为独立会话，子目录名即会话名。
        scan_roots = []
        if self.fixed_chat_path:
            p = Path(self.fixed_chat_path).expanduser()
            if p.exists():
                scan_roots.append((p, p.name or "wechat"))
            else:
                logger.warning(f"指定的微信聊天目录不存在: {p}")
                return
        elif self.base_path.exists():
            subdirs = [
                c for c in self.base_path.iterdir()
                if c.is_dir() and not c.name.startswith(".")
            ]
            if subdirs:
                # 按聊天会话拆分，各自命名（避免与 base_path 整体扫描重复）
                for child in subdirs:
                    scan_roots.append((child, child.name))
                # 收集 base_path 直属文件（不含子目录，交由上面的子目录处理）
                scan_roots.append((self.base_path, "wechat", False))
            else:
                scan_roots.append((self.base_path, "wechat"))
        else:
            logger.warning(f"微信目录不存在: {self.base_path}")
            return

        for entry in scan_roots:
            root, session_name = entry[0], entry[1]
            recursive = entry[2] if len(entry) > 2 else True
            files = (
                self._iter_files(root)
                if recursive
                else (p for p in root.iterdir() if not self._should_skip(p))
            )
            for file_path in files:
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
