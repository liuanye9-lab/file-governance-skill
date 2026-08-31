from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB
from ..utils.logger import setup_logger

logger = setup_logger()

# 空文件（0 字节）的 SHA-256 恒为同一值，不能据此判定内容重复。
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class DedupProcessor:
    """去重引擎：路径去重 + 内容哈希去重 + 版本识别。

    check() 为只读判定，返回结构化结果 (kind, existing)：
      - kind == "new"      全新文件
      - kind == "path"     路径已处理过（应跳过）
      - kind == "version"  内容哈希命中已完成记录（作为新版本处理）
    version 递增只在 process() 中执行，避免 check 产生副作用。
    """

    def __init__(self, db: GovernanceDB, strategy: str = "hash+path"):
        self.db = db
        self.strategy = strategy

    def check(self, record: FileRecord) -> tuple[str, dict]:
        if "path" in self.strategy and self.db.is_path_processed(record.source_path):
            return "path", {}
        # 空文件哈希无区分度，跳过内容去重（否则任意两个空文件会被误判为同一内容）。
        if (
            "hash" in self.strategy
            and record.file_hash
            and record.file_size > 0
            and record.file_hash != EMPTY_SHA256
        ):
            # 只与"已成功入库(done)"的记录比对，避免 failed/pending 记录导致版本号虚增。
            existing = self.db.find_done_by_hash(record.file_hash)
            if existing:
                return "version", existing
        return "new", {}

    def process(self, record: FileRecord) -> FileRecord:
        kind, existing = self.check(record)
        if kind == "path":
            record.status = "skipped"
            record.log_step("dedup", "文件路径已处理过", success=True)
        elif kind == "version":
            record.version = existing.get("version", 1) + 1
            record.is_new_version = True
            for field_name in (
                "drive_url",
                "doc_url",
                "doc_token",
                "record_id",
                "target_space_id",
                "target_node_token",
                "target_page_path",
            ):
                value = existing.get(field_name)
                if value:
                    setattr(record, field_name, value)
            record.log_step(
                "dedup",
                f"内容与《{existing.get('file_name','')}》一致，作为新版本 v{record.version} 处理",
                success=True,
            )
        else:
            record.log_step("dedup", "新文件", success=True)
        return record
