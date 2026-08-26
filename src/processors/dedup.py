from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB
from ..utils.logger import setup_logger

logger = setup_logger()


class DedupProcessor:
    def __init__(self, db: GovernanceDB, strategy: str = "hash+path"):
        self.db = db
        self.strategy = strategy

    def check(self, record: FileRecord) -> tuple[bool, str]:
        if "path" in self.strategy and self.db.is_path_processed(record.source_path):
            return True, "文件路径已处理过"
        if "hash" in self.strategy and record.file_hash:
            existing = self.db.find_by_hash(record.file_hash)
            if existing:
                record.version = existing.get("version", 1) + 1
                record.log_step("dedup", f"哈希重复，标记为新版本 v{record.version}")
                return True, f"文件内容已存在（{existing['file_name']}），作为新版本处理"
        return False, ""

    def process(self, record: FileRecord) -> FileRecord:
        is_dup, reason = self.check(record)
        if is_dup and "新版本" in reason:
            record.log_step("dedup", reason, success=True)
        elif is_dup:
            record.status = "skipped"
            record.log_step("dedup", reason, success=True)
        else:
            record.log_step("dedup", "新文件", success=True)
        return record
