from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
import hashlib
import uuid


@dataclass
class FileRecord:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_path: str = ""
    file_name: str = ""
    file_ext: str = ""
    file_type: str = ""
    file_size: int = 0
    file_hash: str = ""
    source: str = ""
    source_session: str = ""
    captured_at: str = field(default_factory=lambda: datetime.now().isoformat())
    created_at: Optional[str] = None
    modified_at: Optional[str] = None
    author: Optional[str] = None
    page_count: Optional[int] = None
    status: str = "pending"
    domain: Optional[str] = None
    doc_type: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    summary: Optional[str] = None
    text_content: str = ""
    drive_url: str = ""
    doc_url: str = ""
    version: int = 1
    is_new_version: bool = False
    parent_archive: Optional[str] = None
    archive_children: list[str] = field(default_factory=list)
    security_level: str = "L2-Internal"
    share_permission: str = "tenant_readable"
    collaboration_status: str = "待审核"
    human_tags: list[str] = field(default_factory=list)
    review_note: str = ""
    review_conclusion: str = ""
    error_message: str = ""
    processing_steps: list[dict] = field(default_factory=list)
    record_id: str = ""
    agent_record_id: str = ""

    @staticmethod
    def from_path(path: str, source: str, source_session: str = "") -> "FileRecord":
        p = Path(path)
        stat = p.stat()
        ext = p.suffix.lower().lstrip(".")
        return FileRecord(
            source_path=str(p.resolve()),
            file_name=p.name,
            file_ext=ext,
            file_type=FileRecord._guess_type(ext),
            file_size=stat.st_size,
            source=source,
            source_session=source_session,
            created_at=datetime.fromtimestamp(stat.st_ctime).isoformat(),
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        )

    @staticmethod
    def _guess_type(ext: str) -> str:
        type_map = {
            "doc": "doc", "docx": "docx", "pdf": "pdf",
            "xls": "xls", "xlsx": "xlsx", "csv": "csv",
            "ppt": "ppt", "pptx": "pptx",
            "txt": "txt", "md": "markdown", "json": "json", "html": "html",
            "zip": "zip", "rar": "archive", "7z": "archive",
            "jpg": "image", "jpeg": "image", "png": "image", "gif": "image",
            "webp": "image", "bmp": "image",
            "mp3": "audio", "wav": "audio", "mp4": "video", "mov": "video",
        }
        return type_map.get(ext, "other")

    def compute_hash(self) -> str:
        if self.file_hash:
            return self.file_hash
        h = hashlib.sha256()
        try:
            with open(self.source_path, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
            self.file_hash = h.hexdigest()
        except Exception:
            self.file_hash = f"hash-fail-{self.id}"
        return self.file_hash

    def log_step(self, step: str, detail: str = "", success: bool = True):
        self.processing_steps.append({
            "step": step,
            "detail": detail,
            "success": success,
            "at": datetime.now().isoformat(),
        })

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tags_str"] = ", ".join(self.tags)
        d["human_tags_str"] = ", ".join(self.human_tags)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FileRecord":
        if "tags_str" in d:
            d["tags"] = [t.strip() for t in d.pop("tags_str").split(",") if t.strip()]
        if "human_tags_str" in d:
            d["human_tags"] = [t.strip() for t in d.pop("human_tags_str").split(",") if t.strip()]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
