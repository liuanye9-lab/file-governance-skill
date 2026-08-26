from pathlib import Path
from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


class MetadataProcessor:
    def process(self, record: FileRecord) -> FileRecord:
        p = Path(record.source_path)
        try:
            stat = p.stat()
            record.file_size = stat.st_size
            if not record.created_at:
                from datetime import datetime
                record.created_at = datetime.fromtimestamp(stat.st_ctime).isoformat()
            if not record.modified_at:
                from datetime import datetime
                record.modified_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
        except Exception as e:
            logger.debug(f"元数据提取失败 {p.name}: {e}")

        ext = record.file_ext
        try:
            if ext == "pdf":
                self._extract_pdf_meta(record)
            elif ext in ("docx", "doc"):
                self._extract_doc_meta(record)
            elif ext in ("pptx", "ppt"):
                pass
            elif ext in ("xlsx", "xls"):
                pass
        except Exception as e:
            logger.debug(f"格式特定元数据提取失败 {p.name}: {e}")

        record.log_step("metadata", f"size={record.file_size}")
        return record

    def _extract_pdf_meta(self, record: FileRecord):
        try:
            import pdfplumber
            with pdfplumber.open(record.source_path) as pdf:
                record.page_count = len(pdf.pages)
                meta = pdf.metadata or {}
                record.author = meta.get("Author") or record.author
        except Exception:
            pass

    def _extract_doc_meta(self, record: FileRecord):
        try:
            from docx import Document
            doc = Document(record.source_path)
            core = doc.core_properties
            record.author = core.author or record.author
            record.page_count = core.pages or record.page_count
        except Exception:
            pass
