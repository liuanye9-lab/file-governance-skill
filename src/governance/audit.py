from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB


class AuditLogger:
    def __init__(self, db: GovernanceDB):
        self.db = db

    def log_pipeline_start(self, total_files: int):
        self.db.log_audit(
            action="pipeline_start",
            detail=f"开始处理 {total_files} 个文件",
            success=True,
        )

    def log_file_step(self, record: FileRecord, step: str, success: bool = True, detail: str = ""):
        self.db.log_audit(
            action=f"file_{step}",
            file_id=record.id,
            file_name=record.file_name,
            detail=detail,
            success=success,
        )

    def log_file_complete(self, record: FileRecord):
        self.db.log_audit(
            action="file_complete" if record.status == "done" else f"file_{record.status}",
            file_id=record.id,
            file_name=record.file_name,
            detail=f"分类={record.category}, version={record.version}",
            success=(record.status == "done"),
        )

    def log_pipeline_complete(self, stats: dict):
        self.db.log_audit(
            action="pipeline_complete",
            detail=f"完成: 新增{stats.get('done',0)}, 跳过{stats.get('skipped',0)}, 失败{stats.get('failed',0)}",
            success=True,
        )
