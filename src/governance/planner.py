from datetime import datetime

from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB
from .quality import QualityReviewer
from .sensitivity import SensitivityScanner


class GovernancePlanner:
    """生成发布前治理决策和标准问题清单。"""

    PUBLICATION_ACTIONS = (
        "create",
        "update",
        "merge",
        "split",
        "reference",
        "pending",
        "exclude",
    )
    EXECUTABLE_ACTIONS = frozenset(PUBLICATION_ACTIONS[:5])
    PERMISSION_READY_STATUSES = frozenset({
        "ready", "verified", "granted", "passed", "ok",
    })
    PERMISSION_BLOCKED_STATUSES = frozenset({
        "blocked", "denied", "failed", "forbidden",
    })
    GENERATED_PERMISSION_CODES = frozenset({
        "missing_target_space_id",
        "missing_target_node_token",
        "missing_target_page_path",
        "permission_not_verified",
        "permission_denied",
        "security_level_mismatch",
        "share_permission_mismatch",
    })

    def __init__(self, db: GovernanceDB, quality_threshold: int = 75):
        self.db = db
        self.sensitivity = SensitivityScanner()
        self.quality = QualityReviewer(quality_threshold)

    def process(self, record: FileRecord) -> FileRecord:
        self._detect_conflicts(record)
        self.sensitivity.process(record)
        self.quality.process(record)
        action, reasons, blocking_reasons = self._decide_publication_action(record)
        permission_status, permission_issues = self._preflight_permissions(record, action)
        self._set(record, "publication_action", action)
        raw_permission_status = str(
            self._get(record, "permission_status", "") or ""
        ).strip()
        if not raw_permission_status or raw_permission_status == "unchecked":
            self._set(record, "permission_status", permission_status)
        self._set(record, "permission_issues", permission_issues)

        if action == "pending":
            self._set(record, "production_ready", False)
            if blocking_reasons:
                self._set(record, "governance_action", "hold")
                self._set(record, "review_priority", "P0")
            elif self._get(record, "governance_action", "publish") != "hold":
                self._set(record, "governance_action", "review")
                self._set(record, "review_priority", self._get(record, "review_priority", "") or "P1")
        elif action == "exclude":
            self._set(record, "production_ready", False)
            self._set(record, "governance_action", "hold")

        log_step = self._get(record, "log_step")
        if callable(log_step):
            status = self._publication_status(
                action,
                permission_status,
                blocking_reasons,
            )
            log_step(
                "publication_plan",
                f"action={action}, status={status}, reasons={'; '.join(reasons)}",
                success=status not in {"blocked"},
            )
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
                "doc_url": item.get("doc_url"),
                "doc_token": item.get("doc_token"),
                "record_id": item.get("record_id"),
                "target_space_id": item.get("target_space_id"),
                "target_node_token": item.get("target_node_token"),
                "target_page_path": item.get("target_page_path"),
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

    @classmethod
    def build_manifest(cls, records: list[FileRecord]) -> dict:
        ledger = []
        sensitive = []
        conflicts = []
        unparseable = []
        publish_plan = []
        directory_mapping = []
        permission_issues = []
        action_counts = {action: 0 for action in cls.PUBLICATION_ACTIONS}
        status_counts = {
            "ready": 0,
            "pending": 0,
            "blocked": 0,
            "excluded": 0,
        }

        for record in records:
            action, reasons, blocking_reasons = cls._decide_publication_action(record)
            permission_status, record_permission_issues = cls._preflight_permissions(
                record,
                action,
            )
            publication_status = cls._publication_status(
                action,
                permission_status,
                blocking_reasons,
            )
            publishable = (
                action in cls.EXECUTABLE_ACTIONS
                and publication_status == "ready"
            )
            action_counts[action] += 1
            status_counts[publication_status] += 1

            item = {
                "id": cls._get(record, "id", ""),
                "file_name": cls._get(record, "file_name", ""),
                "source": cls._get(record, "source", ""),
                "domain": cls._get(record, "domain"),
                "doc_type": cls._get(record, "doc_type"),
                "category": cls._get(record, "category"),
                "sub_category": cls._get(record, "sub_category"),
                "version": cls._get(record, "version", 1),
                "sensitivity_level": cls._get(record, "sensitivity_level", "none"),
                "quality_score": cls._get(record, "quality_score", 0),
                "production_ready": bool(cls._get(record, "production_ready", False)),
                "review_priority": cls._get(record, "review_priority", ""),
                "review_cycle_days": cls._get(record, "review_cycle_days", 0),
                "next_review_at": cls._get(record, "next_review_at"),
                "governance_action": cls._get(record, "governance_action", "publish"),
                "publication_action": action,
                "action": action,
                "status": publication_status,
                "publishable": publishable,
                "permission_status": permission_status,
                "summary": cls._get(record, "summary"),
            }
            ledger.append(item)
            findings = cls._get(record, "sensitivity_findings", []) or []
            if findings:
                sensitive.append({
                    "id": cls._get(record, "id", ""),
                    "file_name": cls._get(record, "file_name", ""),
                    "level": cls._get(record, "sensitivity_level", "none"),
                    "findings": findings,
                })
            conflict_status = cls._get(record, "conflict_status", "")
            if conflict_status:
                conflicts.append({
                    "id": cls._get(record, "id", ""),
                    "file_name": cls._get(record, "file_name", ""),
                    "status": conflict_status,
                    "details": cls._get(record, "conflict_details", []) or [],
                })
            is_unparseable = cls._is_unparseable(record)
            if is_unparseable:
                unparseable.append({
                    "id": cls._get(record, "id", ""),
                    "file_name": cls._get(record, "file_name", ""),
                    "file_type": cls._get(record, "file_type", ""),
                })

            mapping = cls._directory_mapping(record, action)
            directory_mapping.append(mapping)
            if record_permission_issues:
                permission_issues.append({
                    "id": cls._get(record, "id", ""),
                    "file_name": cls._get(record, "file_name", ""),
                    "publication_action": action,
                    "status": permission_status,
                    "issues": record_permission_issues,
                })

            publish_plan.append({
                "id": cls._get(record, "id", ""),
                "file_name": cls._get(record, "file_name", ""),
                "action": action,
                "status": publication_status,
                "publishable": publishable,
                "reason": "; ".join(reasons),
                "target_space_id": mapping["target_space_id"],
                "target_node_token": mapping["target_node_token"],
                "target_page_path": mapping["target_page_path"],
                "permission_status": permission_status,
            })

        manifest_status = cls._manifest_status(status_counts, len(records))
        return {
            "generated_at": datetime.now().isoformat(),
            "status": manifest_status,
            "summary": {
                "total": len(records),
                "ready": sum(
                    1 for r in records if bool(cls._get(r, "production_ready", False))
                ),
                "review": sum(
                    1 for r in records
                    if cls._get(r, "governance_action", "publish") == "review"
                ),
                "hold": sum(
                    1 for r in records
                    if cls._get(r, "governance_action", "publish") == "hold"
                ),
                "sensitive": len(sensitive),
                "conflicts": len(conflicts),
                "unparseable": len(unparseable),
                "publishable": status_counts["ready"],
                "actions": action_counts,
                "statuses": status_counts,
            },
            "material_ledger": ledger,
            "sensitive_list": sensitive,
            "conflict_list": conflicts,
            "unparseable_list": unparseable,
            "directory_mapping": directory_mapping,
            "permission_issues": permission_issues,
            "publish_plan": publish_plan,
        }

    @classmethod
    def _decide_publication_action(cls, record) -> tuple[str, list[str], list[str]]:
        blocking_reasons = cls._blocking_reasons(record)
        if blocking_reasons:
            return "pending", blocking_reasons, blocking_reasons

        status = str(cls._get(record, "status", "") or "").strip().lower()
        if status == "skipped":
            return "exclude", ["记录已跳过，不进入发布队列"], []
        if status == "failed":
            reason = cls._get(record, "error_message", "") or "处理失败，修复后重新决策"
            return "pending", [str(reason)], []

        requested = str(cls._get(record, "publication_action", "") or "").strip().lower()
        if requested and requested not in cls.PUBLICATION_ACTIONS:
            return "pending", [f"不支持的发布动作: {requested}"], []

        if requested in {"pending", "exclude"}:
            conclusion = cls._get(record, "review_conclusion", "")
            return requested, [str(conclusion or f"已指定 {requested} 动作")], []

        legacy_action = str(
            cls._get(record, "governance_action", "publish") or "publish"
        ).strip().lower()
        if legacy_action in {"hold", "review"}:
            conclusion = cls._get(record, "review_conclusion", "") or "等待人工审核"
            return "pending", [str(conclusion)], []

        if not bool(cls._get(record, "production_ready", False)):
            conclusion = cls._get(record, "review_conclusion", "") or "尚未达到生产就绪标准"
            return "pending", [str(conclusion)], []

        if requested in {"merge", "split", "reference", "update"}:
            return requested, [f"已指定 {requested} 动作"], []

        if (
            bool(cls._get(record, "is_new_version", False))
            or cls._version_number(record) > 1
            or bool(cls._get(record, "doc_token", ""))
        ):
            return "update", ["检测到已有知识页或后续版本"], []

        return "create", ["新资料创建知识页"], []

    @classmethod
    def _blocking_reasons(cls, record) -> list[str]:
        reasons = []
        if str(cls._get(record, "sensitivity_level", "none") or "").lower() == "high":
            reasons.append("高敏资料禁止自动发布")
        if cls._get(record, "conflict_status", ""):
            reasons.append("存在未解决冲突")
        if cls._is_unparseable(record):
            reasons.append("内容不可解析")
        return reasons

    @classmethod
    def _preflight_permissions(cls, record, action: str) -> tuple[str, list[dict]]:
        if action in {"pending", "exclude"}:
            return "not_required", []

        issues = cls._existing_permission_issues(record)
        required_fields = cls._required_mapping_fields(action)
        field_labels = {
            "target_space_id": "目标知识空间",
            "target_node_token": "目标节点",
            "target_page_path": "目标页面路径",
        }
        for field_name in required_fields:
            if not cls._get(record, field_name, ""):
                issues.append({
                    "code": f"missing_{field_name}",
                    "severity": "pending",
                    "message": f"缺少{field_labels[field_name]}",
                })

        security_level = cls._get(record, "security_level", "L2-Internal")
        if security_level != "L2-Internal":
            issues.append({
                "code": "security_level_mismatch",
                "severity": "blocker",
                "message": f"密级必须为 L2-Internal，当前为 {security_level or '未设置'}",
            })
        share_permission = cls._get(record, "share_permission", "tenant_readable")
        if share_permission != "tenant_readable":
            issues.append({
                "code": "share_permission_mismatch",
                "severity": "blocker",
                "message": (
                    "共享权限必须为 tenant_readable，"
                    f"当前为 {share_permission or '未设置'}"
                ),
            })

        raw_status = str(
            cls._get(record, "permission_status", "unchecked") or "unchecked"
        ).strip().lower()
        if raw_status in cls.PERMISSION_BLOCKED_STATUSES:
            issues.append({
                "code": "permission_denied",
                "severity": "blocker",
                "message": f"目标位置权限预检失败: {raw_status}",
            })
        elif raw_status not in cls.PERMISSION_READY_STATUSES:
            issues.append({
                "code": "permission_not_verified",
                "severity": "pending",
                "message": f"目标位置权限尚未验证: {raw_status}",
            })

        issues = cls._deduplicate_issues(issues)
        if any(issue.get("severity") in {"blocker", "error"} for issue in issues):
            return "blocked", issues
        if issues:
            return "pending", issues
        return "ready", []

    @classmethod
    def _existing_permission_issues(cls, record) -> list[dict]:
        normalized = []
        for issue in cls._get(record, "permission_issues", []) or []:
            if isinstance(issue, dict):
                if issue.get("code") in cls.GENERATED_PERMISSION_CODES:
                    continue
                normalized.append({
                    "code": str(issue.get("code") or "permission_issue"),
                    "severity": str(issue.get("severity") or "pending"),
                    "message": str(issue.get("message") or issue),
                })
            else:
                normalized.append({
                    "code": "permission_issue",
                    "severity": "pending",
                    "message": str(issue),
                })
        return normalized

    @staticmethod
    def _deduplicate_issues(issues: list[dict]) -> list[dict]:
        result = []
        seen = set()
        for issue in issues:
            key = (issue.get("code"), issue.get("message"))
            if key not in seen:
                seen.add(key)
                result.append(issue)
        return result

    @classmethod
    def _directory_mapping(cls, record, action: str) -> dict:
        target_space_id = cls._get(record, "target_space_id", "") or ""
        target_node_token = cls._get(record, "target_node_token", "") or ""
        target_page_path = cls._get(record, "target_page_path", "") or ""
        mapping = {
            "target_space_id": target_space_id,
            "target_node_token": target_node_token,
            "target_page_path": target_page_path,
        }
        required_fields = cls._required_mapping_fields(action)
        present = [bool(mapping[field_name]) for field_name in required_fields]
        if not required_fields:
            status = "not_required"
        elif all(present):
            status = "mapped"
        elif any(mapping.values()):
            status = "partial"
        else:
            status = "unmapped"
        return {
            "id": cls._get(record, "id", ""),
            "file_name": cls._get(record, "file_name", ""),
            "source_path": cls._get(record, "source_path", ""),
            "target_space_id": target_space_id,
            "target_node_token": target_node_token,
            "target_page_path": target_page_path,
            "publication_action": action,
            "status": status,
        }

    @staticmethod
    def _required_mapping_fields(action: str) -> tuple[str, ...]:
        return {
            "create": ("target_space_id", "target_page_path"),
            "update": ("target_space_id", "target_node_token"),
            "merge": ("target_space_id", "target_node_token"),
            "split": ("target_space_id", "target_node_token", "target_page_path"),
            "reference": ("target_space_id", "target_page_path"),
        }.get(action, ())

    @staticmethod
    def _publication_status(
        action: str,
        permission_status: str,
        blocking_reasons: list[str],
    ) -> str:
        if blocking_reasons or permission_status == "blocked":
            return "blocked"
        if action == "pending" or permission_status == "pending":
            return "pending"
        if action == "exclude":
            return "excluded"
        return "ready"

    @staticmethod
    def _manifest_status(status_counts: dict, total: int) -> str:
        if status_counts["blocked"]:
            return "blocked"
        if status_counts["pending"]:
            return "pending"
        if total and status_counts["excluded"] == total:
            return "excluded"
        if total:
            return "ready"
        return "empty"

    @classmethod
    def _is_unparseable(cls, record) -> bool:
        file_size = cls._get(record, "file_size", 0) or 0
        if file_size <= 0:
            return False
        file_type = cls._get(record, "file_type", "") or ""
        text = str(cls._get(record, "text_content", "") or "").strip()
        if file_type in {"image", "audio", "video"}:
            return (
                cls._get(record, "media_status", "") != "resolved"
                or not text
            )
        if file_type == "zip" and text.startswith("[压缩包]"):
            return True
        return file_type in QualityReviewer.PARSEABLE_TYPES and not text

    @classmethod
    def _version_number(cls, record) -> int:
        try:
            return int(cls._get(record, "version", 1) or 1)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _get(record, field_name: str, default=None):
        if isinstance(record, dict):
            return record.get(field_name, default)
        return getattr(record, field_name, default)

    @staticmethod
    def _set(record, field_name: str, value):
        if isinstance(record, dict):
            record[field_name] = value
        else:
            setattr(record, field_name, value)
