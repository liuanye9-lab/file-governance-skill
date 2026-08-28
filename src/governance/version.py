from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB
from ..utils.logger import setup_logger

logger = setup_logger()


class VersionManager:
    """版本控制：维护 versions 表的版本链。

    触发条件基于 record.is_new_version 标志（由 DedupProcessor 设置），
    不再依赖 status 字符串，保证版本历史与 files 表一致。
    """

    def __init__(self, db: GovernanceDB):
        self.db = db

    def process(self, record: FileRecord) -> FileRecord:
        if record.is_new_version and record.version > 1:
            # 内容命中已完成记录，登记为新版本
            self.db.add_version(
                file_hash=record.file_hash,
                file_name=record.file_name,
                version=record.version,
                source_path=record.source_path,
                notes=f"新版本 v{record.version}，来自 {record.source}",
            )
            record.log_step("version", f"新版本 v{record.version} 已登记")
        else:
            # 首次入库：若 versions 表尚无该哈希则登记 v1
            versions = self.db.get_versions(record.file_hash)
            if not versions:
                self.db.add_version(
                    file_hash=record.file_hash,
                    file_name=record.file_name,
                    version=1,
                    source_path=record.source_path,
                    notes="首次入库",
                )
                record.version = 1
                record.log_step("version", "首次版本 v1")
        return record
