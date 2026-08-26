from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


class HashProcessor:
    def process(self, record: FileRecord) -> FileRecord:
        record.compute_hash()
        record.log_step("hash", f"SHA-256: {record.file_hash[:16]}...")
        return record
