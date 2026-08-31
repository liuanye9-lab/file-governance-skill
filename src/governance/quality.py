from datetime import datetime, timedelta

from ..models.file_record import FileRecord


class QualityReviewer:
    """按“内容可靠、易找易懂、易维护”三维度生成可解释质量评分。"""

    PARSEABLE_TYPES = {
        "doc", "docx", "pdf", "xls", "xlsx", "csv", "ppt", "pptx",
        "txt", "markdown", "json", "html", "zip",
    }

    def __init__(self, threshold: int = 75):
        self.threshold = int(threshold)

    def process(self, record: FileRecord) -> FileRecord:
        reliability, reliability_notes = self._reliability(record)
        findability, findability_notes = self._findability(record)
        maintainability, maintainability_notes = self._maintainability(record)
        overall = round(reliability * 0.45 + findability * 0.30 + maintainability * 0.25)

        record.quality_dimensions = {
            "reliability": {"score": reliability, "evidence": reliability_notes},
            "findability": {"score": findability, "evidence": findability_notes},
            "maintainability": {"score": maintainability, "evidence": maintainability_notes},
        }
        record.quality_score = overall
        record.production_ready = (
            overall >= self.threshold
            and reliability >= 70
            and record.sensitivity_level != "high"
            and not record.conflict_status
            and not self.is_unparseable(record)
        )

        if record.sensitivity_level == "high" or record.conflict_status or self.is_unparseable(record):
            record.review_priority = "P0"
            record.governance_action = "hold"
        elif not record.production_ready:
            record.review_priority = record.review_priority or "P1"
            if record.governance_action != "hold":
                record.governance_action = "review"
        elif not record.review_priority:
            record.review_priority = "P2"

        record.review_conclusion = "生产就绪" if record.production_ready else "待人工审核"
        record.review_cycle_days = self._review_cycle(record)
        record.next_review_at = (
            datetime.now() + timedelta(days=record.review_cycle_days)
        ).date().isoformat()
        record.log_step(
            "quality_review",
            f"score={overall}, ready={record.production_ready}, "
            f"priority={record.review_priority}, review_in={record.review_cycle_days}d",
        )
        return record

    @staticmethod
    def _review_cycle(record: FileRecord) -> int:
        if record.sensitivity_level in {"high", "medium"}:
            return 30
        if record.doc_type in {"数据报表", "报告分析", "会议纪要"}:
            return 30
        if record.doc_type in {"制度规范", "合同协议", "流程SOP"}:
            return 90
        return 180

    def _reliability(self, record: FileRecord) -> tuple[int, list[str]]:
        score = 100
        notes = []
        if not record.file_hash or record.file_hash.startswith("hash-fail-"):
            score -= 30
            notes.append("缺少可靠内容哈希")
        if not record.source:
            score -= 15
            notes.append("来源不明确")
        if self.is_unparseable(record):
            score -= 40
            notes.append("可解析文件未提取到正文")
        if record.sensitivity_level == "high":
            score -= 30
            notes.append("存在高敏信息")
        if record.conflict_status:
            score -= 25
            notes.append("存在版本或命名冲突")
        return max(0, score), notes or ["来源、哈希与解析结果完整"]

    @staticmethod
    def _findability(record: FileRecord) -> tuple[int, list[str]]:
        checks = (
            (record.domain, "行业领域"),
            (record.doc_type, "文档类型"),
            (record.category, "分类"),
            (record.tags, "标签"),
            (record.summary, "摘要"),
        )
        score = sum(20 for value, _ in checks if value)
        missing = [label for value, label in checks if not value]
        return score, [f"缺少{label}" for label in missing] or ["分类、标签与摘要完整"]

    @staticmethod
    def _maintainability(record: FileRecord) -> tuple[int, list[str]]:
        checks = (
            (record.source_path, "来源路径"),
            (record.captured_at, "采集时间"),
            (record.modified_at, "修改时间"),
            (record.version >= 1, "版本号"),
            (record.processing_steps, "处理轨迹"),
        )
        score = sum(20 for value, _ in checks if value)
        missing = [label for value, label in checks if not value]
        return score, [f"缺少{label}" for label in missing] or ["来源、版本与处理轨迹完整"]

    def is_unparseable(self, record: FileRecord) -> bool:
        if record.file_size <= 0:
            return False
        text = record.text_content.strip()
        if record.file_type in {"image", "audio", "video"}:
            return True
        if record.file_type == "zip" and text.startswith("[压缩包]"):
            return True
        return record.file_type in self.PARSEABLE_TYPES and not text
