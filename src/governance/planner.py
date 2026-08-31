from datetime import datetime

from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB
from .quality import QualityReviewer
from .sensitivity import SensitivityScanner


class GovernancePlanner:
    """生成发布前治理决策和标准问题清单。"""

    def __init__(self, db: GovernanceDB, quality_threshold: int = 75):
        self.db = db
        self.sensitivity = SensitivityScanner()
        self.quality = QualityReviewer(quality_threshold)

    def process(self, record: FileRecord) -> FileRecord:
        self._detect_conflicts(record)
        self.sensitivity.process(record)
        self.quality.process(record)
        return record

    def _detect_conflicts(self, record: FileRecord):
        candidates = [
            item for item in self.db.find_by_name(record.file_name)
            if item.get("status") == "done" and item.get("id") != record.id
        ]
        conflicts = [
            {
                "record_id": item.get("id"),
                "file_hash": item.get("file_hash"),
                "version": item.get("version"),
                "captured_at": item.get("captured_at"),
                "drive_url": item.get("drive_url"),
            }
            for item in candidates
            if item.get("file_hash") and item.get("file_hash") != record.file_hash
        ]
        if conflicts:
            record.conflict_status = "same_name_different_content"
            record.conflict_details = conflicts
            record.governance_action = "hold"
            record.review_priority = "P0"
            record.log_step("conflict", f"同名不同内容冲突 {len(conflicts)} 条", success=False)
        else:
            record.log_step("conflict", "无同名内容冲突")

    @staticmethod
    def build_manifest(records: list[FileRecord]) -> dict:
        ledger = []
        sensitive = []
        conflicts = []
        unparseable = []
        publish_plan = []
        for record in records:
            item = {
                "id": record.id,
                "file_name": record.file_name,
                "source": record.source,
                "domain": record.domain,
                "doc_type": record.doc_type,
                "category": record.category,
                "sub_category": record.sub_category,
                "version": record.version,
                "sensitivity_level": record.sensitivity_level,
                "quality_score": record.quality_score,
                "production_ready": record.production_ready,
                "review_priority": record.review_priority,
                "review_cycle_days": record.review_cycle_days,
                "next_review_at": record.next_review_at,
                "action": record.governance_action,
                "summary": record.summary,
            }
            ledger.append(item)
            if record.sensitivity_findings:
                sensitive.append({
                    "id": record.id,
                    "file_name": record.file_name,
                    "level": record.sensitivity_level,
                    "findings": record.sensitivity_findings,
                })
            if record.conflict_status:
                conflicts.append({
                    "id": record.id,
                    "file_name": record.file_name,
                    "status": record.conflict_status,
                    "details": record.conflict_details,
                })
            text = record.text_content.strip()
            is_unparseable = record.file_size > 0 and (
                (record.file_type in QualityReviewer.PARSEABLE_TYPES and not text)
                or record.file_type in {"image", "audio", "video"}
                or (record.file_type == "zip" and text.startswith("[压缩包]"))
            )
            if is_unparseable:
                unparseable.append({
                    "id": record.id,
                    "file_name": record.file_name,
                    "file_type": record.file_type,
                })
            publish_plan.append({
                "id": record.id,
                "file_name": record.file_name,
                "action": record.governance_action,
                "reason": record.review_conclusion,
            })

        return {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total": len(records),
                "ready": sum(1 for r in records if r.production_ready),
                "review": sum(1 for r in records if r.governance_action == "review"),
                "hold": sum(1 for r in records if r.governance_action == "hold"),
                "sensitive": len(sensitive),
                "conflicts": len(conflicts),
                "unparseable": len(unparseable),
            },
            "material_ledger": ledger,
            "sensitive_list": sensitive,
            "conflict_list": conflicts,
            "unparseable_list": unparseable,
            "publish_plan": publish_plan,
        }
