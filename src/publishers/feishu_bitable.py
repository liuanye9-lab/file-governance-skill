import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..models.file_record import FileRecord
from ..utils.db import GovernanceDB
from ..utils.logger import setup_logger

logger = setup_logger()


class FeishuBitablePublisher:
    def __init__(self, config: dict, db: GovernanceDB):
        self.config = config
        self.db = db
        bitable_cfg = config.get("feishu", {}).get("bitable", {})
        self.enabled = bitable_cfg.get("enabled", False)
        self.base_token = bitable_cfg.get("base_token", "")
        self.knowledge_table_id = bitable_cfg.get("knowledge_table_id") or bitable_cfg.get("table_id", "")
        self.agent_table_id = bitable_cfg.get("agent_context_table_id") or bitable_cfg.get("agent_table_id", "")
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
        return bool(self.cli_path and self.base_token and self.knowledge_table_id)

    def publish_record(self, record: FileRecord) -> Optional[str]:
        if not self.available:
            return None
        fields = self._build_knowledge_fields(record)
        try:
            result = self._run_bitable([
                "base", "+record-create", "--as", "user",
                "--base-token", self.base_token,
                "--table-id", self.knowledge_table_id,
                "--fields", json.dumps(fields, ensure_ascii=False),
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
                            "base", "+record-update", "--as", "user",
                            "--base-token", self.base_token,
                            "--table-id", self.agent_table_id,
                            "--record-id", existing_id,
                            "--fields", json.dumps(fields, ensure_ascii=False),
                        ])
                        updated += 1
                else:
                    result = self._run_bitable([
                        "base", "+record-create", "--as", "user",
                        "--base-token", self.base_token,
                        "--table-id", self.agent_table_id,
                        "--fields", json.dumps(fields, ensure_ascii=False),
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
                    "base", "records", "list", "--as", "user",
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

    def _build_knowledge_fields(self, record: FileRecord) -> dict:
        from datetime import datetime
        return {
            "文件名": record.file_name,
            "分类": record.category or "待人工复核",
            "子分类": record.sub_category or "",
            "标签": ", ".join(record.tags),
            "摘要": record.summary or "",
            "文件类型": record.file_type or "other",
            "文件大小": round(record.file_size / 1024, 1) if record.file_size else 0,
            "来源": record.source,
            "来源会话": record.source_session or "",
            "直达链接": {"link": record.drive_url, "text": record.file_name} if record.drive_url else "",
            "协作状态": record.collaboration_status,
            "人工标签": ", ".join(record.human_tags),
            "协作备注": record.review_note,
            "审核结论": record.review_conclusion,
            "版本号": record.version,
            "父文件": record.parent_archive or "",
            "密级": record.security_level,
            "同步状态": "已完成" if record.status == "done" else record.status,
            "处理时间": datetime.now().isoformat(),
        }

    def _build_agent_context(self, records: list[FileRecord]) -> list[dict]:
        from datetime import datetime
        done = [r for r in records if r.status == "done" and r.drive_url]
        categories = {}
        for r in done:
            categories.setdefault(r.category or "其他", []).append(r)
        project_name = self.config.get("knowledge", {}).get("project_name", "默认项目")
        output = []
        output.append({
            "key": f"project:{project_name}",
            "fields": {
                "Context Key": f"project:{project_name}",
                "类型": "Project",
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
                "类型": "Coverage",
                "内容": json.dumps({
                    "total": len(done),
                    "by_category": {c: len(rs) for c, rs in categories.items()},
                    "by_type": {},
                }, ensure_ascii=False),
                "摘要": f"已覆盖 {len(categories)} 个分类，共 {len(done)} 份材料",
            }
        })
        output.append({
            "key": "taxonomy:categories",
            "fields": {
                "Context Key": "taxonomy:categories",
                "类型": "Taxonomy",
                "内容": json.dumps(self.config.get("taxonomy", {}), ensure_ascii=False),
                "摘要": "分类体系定义",
            }
        })
        for r in done:
            output.append({
                "key": f"knowledge:{r.file_hash[:16]}",
                "fields": {
                    "Context Key": f"knowledge:{r.file_hash[:16]}",
                    "类型": "Knowledge Record",
                    "内容": json.dumps({
                        "file_name": r.file_name,
                        "category": r.category,
                        "sub_category": r.sub_category,
                        "tags": r.tags,
                        "summary": r.summary,
                        "drive_url": r.drive_url,
                        "source": r.source,
                        "version": r.version,
                        "file_type": r.file_type,
                    }, ensure_ascii=False),
                    "摘要": r.summary or r.file_name,
                }
            })
        output.append({
            "key": "governance:rules",
            "fields": {
                "Context Key": "governance:rules",
                "类型": "Governance Rule",
                "内容": json.dumps({
                    "default_security_level": "L2-Internal",
                    "default_share_permission": "tenant_readable",
                    "dedup_strategy": "hash+path",
                    "versioning_enabled": True,
                }, ensure_ascii=False),
                "摘要": "默认治理规则：内部可见、自动去重、版本追踪",
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
