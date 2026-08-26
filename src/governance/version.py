from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB
from ..utils.logger import setup_logger

logger = setup_logger()


class VersionManager:
    def __init__(self, db: GovernanceDB):
        self.db = db

    def process(self, record: FileRecord) -> FileRecord:
        if record.status == "skipped" and record.version > 1:
            record.status = "pending"
            self.db.add_version(
                file_hash=record.file_hash,
                file_name=record.file_name,
                version=record.version,
                source_path=record.source_path,
                notes=f"新版本 v{record.version}，来自 {record.source}",
            )
            record.log_step("version", f"新版本 v{record.version} 已注册")
        elif record.status != "failed":
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
