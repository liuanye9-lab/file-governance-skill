import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..governance.sensitivity import SensitivityScanner
from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB
from ..utils.logger import setup_logger

logger = setup_logger()


class FeishuBitablePublisher:
    def __init__(self, config: dict, db: GovernanceDB):
        self.config = config
        self.db = db
        feishu_cfg = config.get("feishu", {})
        bitable_cfg = feishu_cfg.get("bitable", {})
        self.provider = str(feishu_cfg.get("provider", "cli")).lower()
        self.enabled = bitable_cfg.get("enabled", False)
        self.base_token = bitable_cfg.get("base_token", "")
        self.knowledge_table_id = bitable_cfg.get("knowledge_table_id") or bitable_cfg.get("table_id", "")
        self.agent_table_id = bitable_cfg.get("agent_context_table_id") or bitable_cfg.get("agent_table_id", "")
        self.knowledge_page_field = bitable_cfg.get("knowledge_page_field", "")
        self._scanner = SensitivityScanner()
        self.cli_path = self._resolve_cli(config.get("feishu", {}).get("cli_path", ""))
        self.knowledge_fields = [
            "文件名", "分类", "子分类", "标签", "摘要", "文件类型",
            "文件大小", "来源", "来源会话", "直达链接",
            "协作状态", "人工标签", "协作备注", "审核结论",
            "版本号", "父文件", "密级", "同步状态", "处理时间",
        ]

    @staticmethod
    def _resolve_cli(cli_path: str) -> str:
        if cli_path and Path(cli_path).is_file():
            return cli_path
        executable = shutil.which("lark-cli")
        if executable:
            return executable
        candidates = list((Path.home() / "Library/pnpm/store").glob(
            "**/node_modules/@larksuite/cli/bin/lark-cli"
        ))
        if candidates:
            return str(sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0])
        return ""

    @property
    def available(self) -> bool:
        return bool(
            self.enabled
            and self.cli_path
            and self.base_token
            and self.knowledge_table_id
            and self.provider in {"cli", "lark-cli"}
        )

    def publish_record(self, record: FileRecord) -> Optional[str]:
        if not self.available:
            return None
        fields = self._build_knowledge_fields(record)
        try:
            result = self._run_bitable([
                "base", "+record-upsert", "--as", "user",
                "--base-token", self.base_token,
                "--table-id", self.knowledge_table_id,
                "--json", json.dumps(fields, ensure_ascii=False),
            ])
            record_id = self._find_value(result, "record_id") or self._find_value(result, "recordId")
            if record_id:
                record.record_id = record_id
                record.log_step("bitable", f"知识材料记录 {record_id}")
            time.sleep(0.3)
            return record_id
        except Exception as e:
            logger.debug(f"Bitable 写入失败: {e}")
            return None

    def update_record(self, record: FileRecord) -> bool:
        if not (self.available and record.record_id):
            return False
        try:
            self._run_bitable([
                "base", "+record-upsert", "--as", "user",
                "--base-token", self.base_token,
                "--table-id", self.knowledge_table_id,
                "--record-id", record.record_id,
                "--json", json.dumps(
                    self._build_knowledge_fields(record),
                    ensure_ascii=False,
                ),
            ])
            return True
        except Exception as e:
            logger.debug(f"Bitable 记录更新失败: {record.record_id}: {e}")
            return False

    def sync_agent_context(self, records: list[FileRecord]) -> dict:
        if not (self.available and self.agent_table_id):
            return {"created": 0, "updated": 0}
        state = self.db.get_sync_state("agent_context", {"existing_keys": set(), "records": {}})
        existing_keys = set(state.get("existing_keys", []))
        created = 0
        updated = 0
        context_records = self._build_agent_context(records)
        for ctx in context_records:
            key = ctx["key"]
            fields = ctx["fields"]
            try:
                if key in existing_keys:
                    existing_id = state.get("records", {}).get(key, "")
                    if existing_id:
                        self._run_bitable([
                            "base", "+record-upsert", "--as", "user",
                            "--base-token", self.base_token,
                            "--table-id", self.agent_table_id,
                            "--record-id", existing_id,
                            "--json", json.dumps(fields, ensure_ascii=False),
                        ])
                        updated += 1
                else:
                    result = self._run_bitable([
                        "base", "+record-upsert", "--as", "user",
                        "--base-token", self.base_token,
                        "--table-id", self.agent_table_id,
                        "--json", json.dumps(fields, ensure_ascii=False),
                    ])
                    rid = self._find_value(result, "record_id") or self._find_value(result, "recordId")
                    if rid:
                        state.setdefault("records", {})[key] = rid
                        existing_keys.add(key)
                        created += 1
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"Agent 上下文同步失败: {key}: {e}")
        self.db.set_sync_state("agent_context", {
            "existing_keys": list(existing_keys),
            "records": state.get("records", {}),
        })
        return {"created": created, "updated": updated}

    def clear_table(self, table_id: str) -> int:
        if not (self.cli_path and self.base_token and table_id):
            return 0
        deleted = 0
        max_rounds = 200  # 防止删除静默失败导致的无限循环
        try:
            for _ in range(max_rounds):
                result = self._run_bitable([
                    "base", "+record-list", "--as", "user",
                    "--base-token", self.base_token,
                    "--table-id", table_id, "--page-size", "50",
                ])
                items = self._find_value(result, "items") or []
                if not items:
                    break
                ids = [it.get("record_id") or it.get("recordId") for it in items]
                ids = [i for i in ids if i]
                if not ids:
                    break
                args = [
                    "base", "+record-delete", "--as", "user", "--yes",
                    "--base-token", self.base_token, "--table-id", table_id,
                ]
                for rid in ids:
                    args.extend(["--record-id", rid])
                self._run_bitable(args)
                deleted += len(ids)
                time.sleep(0.5)
            else:
                logger.warning(f"clear_table 达到最大轮次 {max_rounds}，可能仍有残留记录")
        except Exception as e:
            logger.debug(f"清空表失败: {e}")
        return deleted

    def verify_record(self, record: FileRecord) -> bool:
        if not (self.available and record.record_id):
            return False
        try:
            result = self._run_bitable([
                "base", "+record-get", "--as", "user",
                "--base-token", self.base_token,
                "--table-id", self.knowledge_table_id,
                "--record-id", record.record_id,
                "--field-id", "文件名",
                "--field-id", "同步状态",
                "--format", "json",
            ])
        except Exception as e:
            logger.debug(f"Bitable 回读失败: {record.record_id}: {e}")
            return False
        found = self._find_record(result, record.record_id)
        if not found:
            return False
        fields = found.get("fields", found)
        return fields.get("文件名") == self._scanner.redact(
            record.file_name
        )

    def _build_knowledge_fields(self, record: FileRecord) -> dict:
        from datetime import datetime
        safe_name = self._scanner.redact(record.file_name)
        governance_note = (
            f"行业={self._scanner.redact(record.domain or '未识别')}；"
            f"类型={self._scanner.redact(record.doc_type or '未识别')}；"
            f"质量={record.quality_score}；敏感级别={record.sensitivity_level}；"
            f"发布动作={record.publication_action}；验收={record.acceptance_status}；"
            f"权限预检={record.permission_status}；"
            f"复核优先级={record.review_priority or '无'}；"
            f"下次复核={record.next_review_at or '未安排'}"
        )
        note = "；".join(part for part in (record.review_note, governance_note) if part)
        fields = {
            "文件名": safe_name,
            "分类": [self._scanner.redact(record.category or "待人工复核")],
            "子分类": (
                [self._scanner.redact(record.sub_category)]
                if record.sub_category else []
            ),
            "标签": [
                self._scanner.redact(tag) for tag in record.tags
            ],
            "摘要": self._scanner.redact(record.summary or ""),
            "文件类型": [record.file_type or "other"],
            "文件大小": round(record.file_size / 1024, 1) if record.file_size else 0,
            "来源": [record.source or "unknown"],
            "来源会话": self._scanner.redact(record.source_session or ""),
            "直达链接": record.drive_url or "",
            "协作状态": [record.collaboration_status],
            "人工标签": [
                self._scanner.redact(tag) for tag in record.human_tags
            ],
            "协作备注": note,
            "审核结论": record.review_conclusion or (
                "生产就绪" if record.production_ready else "待人工审核"
            ),
            "版本号": record.version,
            "父文件": record.parent_archive or "",
            "密级": [record.security_level],
            "同步状态": [
                "已完成" if record.status == "done" else record.status
            ],
            "处理时间": datetime.now().isoformat(),
        }
        if self.knowledge_page_field and record.doc_url:
            fields[self.knowledge_page_field] = record.doc_url
        return fields

    def _build_agent_context(self, records: list[FileRecord]) -> list[dict]:
        from datetime import datetime
        done = [r for r in records if r.status == "done" and r.drive_url]
        categories = {}
        domains = {}
        doc_types = {}
        for r in done:
            categories.setdefault(r.category or "其他", []).append(r)
            domains[r.domain or "通用"] = domains.get(r.domain or "通用", 0) + 1
            doc_types[r.doc_type or "其他文档"] = doc_types.get(r.doc_type or "其他文档", 0) + 1
        project_name = self.config.get("knowledge", {}).get("project_name", "默认项目")
        output = []
        output.append({
            "key": f"project:{project_name}",
            "fields": {
                "Context Key": f"project:{project_name}",
                "类型": ["Project"],
                "内容": json.dumps({
                    "project_name": project_name,
                    "total_materials": len(done),
                    "last_updated": datetime.now().isoformat(),
                }, ensure_ascii=False),
                "摘要": f"项目「{project_name}」共治理 {len(done)} 份材料",
            }
        })
        output.append({
            "key": "coverage:overview",
            "fields": {
                "Context Key": "coverage:overview",
                "类型": ["Coverage"],
                "内容": json.dumps({
                    "total": len(done),
                    "by_category": {c: len(rs) for c, rs in categories.items()},
                    "by_domain": domains,
                    "by_doc_type": doc_types,
                    "production_ready": sum(1 for r in done if r.production_ready),
                    "needs_review": sum(1 for r in done if not r.production_ready),
                }, ensure_ascii=False),
                "摘要": f"已覆盖 {len(categories)} 个分类，共 {len(done)} 份材料",
            }
        })
        output.append({
            "key": "taxonomy:categories",
            "fields": {
                "Context Key": "taxonomy:categories",
                "类型": ["Taxonomy"],
                "内容": json.dumps(self.config.get("taxonomy", {}), ensure_ascii=False),
                "摘要": "分类体系定义",
            }
        })
        for r in done:
            output.append({
                "key": f"knowledge:{r.file_hash[:16]}",
                "fields": {
                    "Context Key": f"knowledge:{r.file_hash[:16]}",
                    "类型": ["Knowledge Record"],
                    "内容": json.dumps({
                        "file_name": self._scanner.redact(r.file_name),
                        "domain": r.domain,
                        "doc_type": r.doc_type,
                        "category": r.category,
                        "sub_category": r.sub_category,
                        "tags": r.tags,
                        "summary": self._scanner.redact(r.summary or ""),
                        "drive_url": r.drive_url,
                        "doc_url": r.doc_url,
                        "doc_token": r.doc_token,
                        "source": r.source,
                        "source_revision": r.source_revision,
                        "version": r.version,
                        "file_type": r.file_type,
                        "sensitivity_level": r.sensitivity_level,
                        "quality_score": r.quality_score,
                        "quality_dimensions": r.quality_dimensions,
                        "production_ready": r.production_ready,
                        "review_priority": r.review_priority,
                        "review_cycle_days": r.review_cycle_days,
                        "next_review_at": r.next_review_at,
                        "conflict_status": r.conflict_status,
                        "publication_action": r.publication_action,
                        "target_page_path": r.target_page_path,
                        "permission_status": r.permission_status,
                        "knowledge_page_status": r.knowledge_page_status,
                        "readback_verified": r.readback_verified,
                        "acceptance_status": r.acceptance_status,
                        "review_owner": r.review_owner,
                        "review_task_url": r.review_task_url,
                    }, ensure_ascii=False),
                    "摘要": self._scanner.redact(
                        r.summary or r.file_name
                    ),
                }
            })
        output.append({
            "key": "governance:rules",
            "fields": {
                "Context Key": "governance:rules",
                "类型": ["Governance Rule"],
                "内容": json.dumps({
                    "default_security_level": "L2-Internal",
                    "default_share_permission": "tenant_readable",
                    "dedup_strategy": "hash+path",
                    "versioning_enabled": True,
                    "quality_dimensions": ["reliability", "findability", "maintainability"],
                    "quality_threshold": self.config.get("governance", {}).get(
                        "quality_threshold", 75
                    ),
                    "publication_mode": self.config.get("governance", {}).get(
                        "publication_mode", "auto"
                    ),
                }, ensure_ascii=False),
                "摘要": "治理规则：权限分级、自动去重、版本追踪、发布前风险检查与三维质量评分",
            }
        })
        return output

    def _run_bitable(self, args: list[str]) -> dict:
        if not self.cli_path:
            raise RuntimeError("lark-cli 不可用")
        if "--format" not in args:
            args = [*args, "--format", "json"]
        completed = subprocess.run(
            [self.cli_path, *args], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or output)
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"raw": output}

    @classmethod
    def _find_value(cls, value, key: str):
        if isinstance(value, dict):
            if value.get(key) is not None:
                return value[key]
            for v in value.values():
                f = cls._find_value(v, key)
                if f is not None:
                    return f
        elif isinstance(value, list):
            for v in value:
                f = cls._find_value(v, key)
                if f is not None:
                    return f
        return None

    @classmethod
    def _find_record(cls, value, record_id: str):
        if isinstance(value, dict):
            current_id = value.get("record_id") or value.get("recordId")
            if current_id == record_id:
                return value
            for child in value.values():
                found = cls._find_record(child, record_id)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_record(child, record_id)
                if found:
                    return found
        return None
