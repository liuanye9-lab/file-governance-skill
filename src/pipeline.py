import sys
import json
import time
from pathlib import Path

from .utils.config import load_config, SKILL_ROOT
from .utils.db import GovernanceDB
from .utils.logger import setup_logger
from .models.file_record import FileRecord
from .collectors import get_collectors
from .processors.hasher import HashProcessor
from .processors.metadata import MetadataProcessor
from .processors.dedup import DedupProcessor
from .processors.parser import DocumentParser
from .processors.media import MediaProcessor
from .processors.classifier import Classifier
from .governance.version import VersionManager
from .governance.permission import PermissionGovernor
from .governance.audit import AuditLogger
from .governance.acceptance import PublicationAcceptance
from .governance.planner import GovernancePlanner
from .governance.wiki_catalog import WikiCatalog
from .publishers.feishu_drive import FeishuDrivePublisher
from .publishers.feishu_bitable import FeishuBitablePublisher
from .publishers.feishu_docx import FeishuDocxPublisher
from .publishers.feishu_tasks import FeishuTaskPublisher
from .publishers.knowledge_graph import KnowledgeGraphPublisher
from .publishers.reporter import ResultReporter

logger = setup_logger()


class GovernancePipeline:
    def __init__(self, config: dict = None):
        self.config = config or load_config()
        # 默认 DB 路径锚定到 Skill 根目录，避免随当前工作目录漂移导致数据"丢失"。
        default_db = str(SKILL_ROOT / "data" / "governance.db")
        db_path = self.config.get("db", {}).get("path") or default_db
        self.db = GovernanceDB(db_path)
        self.collectors = get_collectors(self.config, self.db)
        self.hasher = HashProcessor()
        self.metadata = MetadataProcessor()
        self.dedup = DedupProcessor(self.db, self.config.get("governance", {}).get("dedup_strategy", "hash+path"))
        self.parser = DocumentParser(max_text_length=self.config.get("processing", {}).get("max_text_length", 8000))
        self.media = MediaProcessor(
            self.config,
            max_text_length=self.config.get("processing", {}).get(
                "max_text_length", 8000
            ),
        )
        self.classifier = Classifier(self.config.get("taxonomy", {}))
        self.version_mgr = VersionManager(self.db)
        governance_cfg = self.config.get("governance", {})
        self.planner = GovernancePlanner(
            self.db,
            quality_threshold=governance_cfg.get("quality_threshold", 75),
        )
        self.publication_mode = governance_cfg.get("publication_mode", "auto")
        self.block_on = set(governance_cfg.get(
            "block_on",
            ["high_sensitivity", "name_conflict", "unparseable"],
        ))
        self.drive_pub = FeishuDrivePublisher(self.config)
        self.bitable_pub = FeishuBitablePublisher(self.config, self.db)
        self.docx_pub = FeishuDocxPublisher(self.config)
        self.require_knowledge_page_confirmation = bool(
            self.config.get("knowledge_pages", {}).get(
                "require_confirmation", True
            )
        )
        self.task_pub = FeishuTaskPublisher(self.config)
        self.graph_pub = KnowledgeGraphPublisher(self.config)
        self.permission_gov = PermissionGovernor(
            self.config,
            cli_path=self.config.get("feishu", {}).get("cli_path", ""),
        )
        self.wiki_catalog = WikiCatalog(
            self.config,
            cli_path=self.config.get("feishu", {}).get("cli_path", ""),
        )
        self.wiki_snapshot = {"status": "not_loaded", "entries": []}
        self.acceptance = PublicationAcceptance(
            expected_security_level=governance_cfg.get(
                "default_security_level", "L2-Internal"
            ),
            expected_share_permission=governance_cfg.get(
                "default_share_permission", "tenant_readable"
            ),
        )
        self.audit = AuditLogger(self.db)
        self.reporter = ResultReporter()

    def run(self, source: str = "all") -> dict:
        logger.info("=" * 60)
        logger.info("File Governance Pipeline 启动")
        logger.info("=" * 60)

        all_records = []
        if source == "all":
            for collector in self.collectors:
                try:
                    all_records.extend(list(collector.scan()))
                except Exception as e:
                    logger.error(f"采集器 {collector.source_type} 扫描失败: {e}")
        else:
            source_map = {c.source_type: c for c in self.collectors}
            if source in source_map:
                try:
                    all_records.extend(list(source_map[source].scan()))
                except Exception as e:
                    logger.error(f"采集器 {source} 扫描失败: {e}")
            else:
                logger.warning(f"未知来源: {source}")

        return self._process_records(all_records)

    def fetch(self, urls: list) -> dict:
        """按给定文件地址（URL 或本地路径）自动爬取并治理沉淀。"""
        from .collectors.url_fetch import UrlFetchCollector
        logger.info("=" * 60)
        logger.info(f"File Governance Fetch 启动：{len(urls)} 个地址")
        logger.info("=" * 60)
        url_cfg = dict(self.config.get("sources", {}).get("url_fetch", {}))
        url_cfg["urls"] = urls
        url_cfg.setdefault("max_file_size_mb", self.config.get("governance", {}).get("max_file_size_mb", 500))
        collector = UrlFetchCollector(url_cfg, self.db)
        records = list(collector.scan())
        result = self._process_records(records)
        # 汇报抓取成功率：请求地址数 vs 实际抓到并进入处理的文件数
        requested = len(urls)
        fetched = len(records)
        result["fetch"] = {"requested": requested, "fetched": fetched, "unfetched": requested - fetched}
        if fetched < requested:
            logger.warning(f"抓取：请求 {requested} 个地址，成功 {fetched} 个，失败/未找到 {requested - fetched} 个")
        return result

    def plan(self, source: str = "all", urls: list = None) -> dict:
        """只读盘点：完成解析、分类、风险与质量检查，但不上传、不写 Bitable。"""
        self._load_wiki_catalog()
        if urls:
            from .collectors.url_fetch import UrlFetchCollector
            url_cfg = dict(self.config.get("sources", {}).get("url_fetch", {}))
            url_cfg["urls"] = urls
            url_cfg.setdefault(
                "max_file_size_mb",
                self.config.get("governance", {}).get("max_file_size_mb", 500),
            )
            records = list(UrlFetchCollector(url_cfg, self.db).scan())
        else:
            records = []
            collectors = self.collectors if source == "all" else [
                item for item in self.collectors if item.source_type == source
            ]
            for collector in collectors:
                try:
                    records.extend(list(collector.scan()))
                except Exception as e:
                    logger.error(f"采集器 {collector.source_type} 扫描失败: {e}")

        planned = []
        skipped = []
        failed = []
        for record in records:
            try:
                self.hasher.process(record)
                record.source_revision = record.file_hash
                self.metadata.process(record)
                self.dedup.process(record)
                if record.status == "skipped":
                    skipped.append(record)
                    self.db.insert_file(record.to_dict())
                    continue
                self.version_mgr.process(record)
                self.parser.process(record)
                if record.file_type in {"image", "audio", "video"}:
                    self.media.process(record)
                self.classifier.process(record)
                self._apply_default_target(record)
                self.permission_gov.preflight_target(record)
                self.planner.process(record)
                record.status = (
                    "pending_review"
                    if record.governance_action in ("hold", "review")
                    else "planned"
                )
                self.db.insert_file(record.to_dict())
                self.audit.log_file_step(
                    record,
                    "planned",
                    detail=f"action={record.governance_action}, score={record.quality_score}",
                )
                planned.append(record)
            except Exception as e:
                record.status = "failed"
                record.error_message = str(e)
                self.db.insert_file(record.to_dict())
                failed.append(record)

        manifest = self.planner.build_manifest(planned + skipped + failed)
        manifest["summary"]["skipped"] = len(skipped)
        manifest["summary"]["failed"] = len(failed)
        return {
            "stats": {
                "total": len(records),
                "planned": len(planned),
                "skipped": len(skipped),
                "failed": len(failed),
            },
            "manifest": manifest,
            "wiki_catalog": self.wiki_snapshot,
        }

    def publish_review_queue(
        self,
        record_ids: list = None,
        approve_risk: bool = False,
    ) -> dict:
        """发布已盘点记录；冲突/无法解析需 approve_risk，高敏资料始终拦截。"""
        self._load_wiki_catalog()
        queue = [
            FileRecord.from_dict(item)
            for item in self.db.get_review_queue()
            if not record_ids or item.get("id") in record_ids
        ]
        stats = {
            "total": len(queue),
            "done": 0,
            "pending_review": 0,
            "blocked": 0,
            "failed": 0,
        }
        results = []
        for record in queue:
            if record.sensitivity_level == "high":
                stats["blocked"] += 1
                continue
            if record.publication_action == "exclude":
                stats["blocked"] += 1
                continue
            if (
                record.governance_action in {"hold", "review"}
                or record.publication_action == "pending"
            ) and not approve_risk:
                stats["blocked"] += 1
                continue
            record.status = "done"
            if self._publish_prepared_record(record):
                self.db.insert_file(record.to_dict())
                self.audit.log_file_complete(record)
                stats["done"] += 1
                results.append({
                    "id": record.id,
                    "file_name": self.planner.sensitivity.redact(
                        record.file_name
                    ),
                    "drive_url": record.drive_url,
                    "doc_url": record.doc_url,
                    "publication_action": record.publication_action,
                    "acceptance_status": record.acceptance_status,
                    "quality_score": record.quality_score,
                    "production_ready": record.production_ready,
                })
            else:
                if record.status != "pending_review":
                    record.status = "failed"
                record.error_message = record.error_message or "发布失败"
                self.db.insert_file(record.to_dict())
                stats[record.status] += 1

        if self.bitable_pub.available and stats["done"]:
            all_done = [FileRecord.from_dict(r) for r in self.db.get_successful_records()]
            self.bitable_pub.sync_agent_context(all_done)
        graph_result = self._sync_knowledge_graph()
        return {
            "stats": stats,
            "results": results,
            "knowledge_graph": graph_result,
            "wiki_catalog": self.wiki_snapshot,
        }

    def _publish_prepared_record(self, record: FileRecord) -> bool:
        """发布已完成解析、分类和治理检查的记录。"""
        if (
            self.drive_pub.available
            and record.file_size > 0
            and not record.drive_url
        ):
            try:
                self.drive_pub.upload(record)
            except Exception as e:
                safe_error = self.planner.sensitivity.redact(str(e))
                logger.warning(
                    f"  上传失败: {safe_error}，尝试查找已有文件..."
                )
                existing = self.drive_pub.find_existing_url(record.file_name)
                if existing:
                    record.drive_url = existing
                    record.log_step("upload", f"复用已有链接 {existing}")
        elif record.file_size <= 0:
            record.log_step("upload", "空文件，仅本地归档不上传", success=False)

        if self.drive_pub.available and record.file_size > 0 and not record.drive_url:
            return self._defer_publication(
                record, "上传飞书失败且未找到已有文件"
            )
        if record.drive_url:
            self.db.update_version_drive_url(record.file_hash, record.version, record.drive_url)

        if record.drive_url:
            try:
                self.permission_gov.process(record)
            except Exception as e:
                logger.warning(
                    "权限治理失败: "
                    f"{self.planner.sensitivity.redact(str(e))}"
                )
            if record.permission_status == "blocked":
                return self._defer_publication(record, "权限治理失败")

        if self.docx_pub.enabled:
            if record.publication_action in {"pending", "exclude"}:
                return self._defer_publication(
                    record,
                    f"发布动作 {record.publication_action} 不允许创建知识页",
                )
            if record.publication_action in {"merge", "split"}:
                return self._defer_publication(
                    record,
                    f"发布动作 {record.publication_action} 需要显式批次编排",
                )
            if record.publication_action == "reference":
                record.doc_url = record.doc_url or record.drive_url
                record.knowledge_page_status = "referenced"
                record.readback_verified = self.drive_pub.verify_reference(
                    record.doc_url
                )
                if not record.readback_verified:
                    return self._defer_publication(
                        record, "引用目标回读验证失败"
                    )
            else:
                try:
                    page_result = self.docx_pub.publish_record(record)
                except Exception as e:
                    return self._defer_publication(
                        record, f"知识页发布失败: {e}"
                    )
                if page_result.get("status") != "verified":
                    return self._defer_publication(
                        record,
                        "知识页未通过回读验证: "
                        f"{page_result.get('status', 'unknown')}",
                    )

        if self.bitable_pub.available:
            if record.record_id:
                published = self.bitable_pub.update_record(record)
            else:
                published = bool(self.bitable_pub.publish_record(record))
            if not published:
                return self._defer_publication(
                    record, "知识材料表写入失败"
                )
            if not self.bitable_pub.verify_record(record):
                return self._defer_publication(
                    record, "知识材料表写后回读失败"
                )
        if self.docx_pub.enabled:
            acceptance = self.acceptance.evaluate(
                record,
                governance_table={
                    "record_id": record.record_id,
                    "verified": bool(
                        self.bitable_pub.available and record.record_id
                    ),
                },
            )
            if acceptance.status != "success":
                record.status = "pending_review"
                record.error_message = (
                    "发布验收未通过: " + "; ".join(acceptance.reasons)
                )[:1000]
                if self.bitable_pub.available:
                    self.bitable_pub.update_record(record)
                return False
            if self.bitable_pub.available:
                if (
                    not self.bitable_pub.update_record(record)
                    or not self.bitable_pub.verify_record(record)
                ):
                    record.acceptance_status = "pending"
                    return self._defer_publication(
                        record, "验收状态回写或回读失败"
                    )
        return True

    @staticmethod
    def _defer_publication(record: FileRecord, message: str) -> bool:
        record.status = "pending_review"
        record.error_message = str(message)[:1000]
        record.log_step(
            "publication_deferred",
            record.error_message,
            success=False,
        )
        return False

    def _process_records(self, all_records: list) -> dict:
        self._load_wiki_catalog()
        stats = {
            "total": len(all_records),
            "done": 0,
            "pending_review": 0,
            "skipped": 0,
            "failed": 0,
        }
        results = []
        self.audit.log_pipeline_start(stats["total"])

        for i, record in enumerate(all_records, 1):
            safe_name = self.planner.sensitivity.redact(record.file_name)
            logger.info(f"[{i}/{len(all_records)}] 处理: {safe_name}")
            try:
                self.hasher.process(record)
                record.source_revision = record.file_hash
                self.metadata.process(record)
                self.dedup.process(record)
                if record.status == "skipped":
                    self.db.insert_file(record.to_dict())
                    stats["skipped"] += 1
                    self.audit.log_file_complete(record)
                    continue
                self.version_mgr.process(record)
                self.parser.process(record)
                if record.file_type in {"image", "audio", "video"}:
                    self.media.process(record)
                self.classifier.process(record)
                self._apply_default_target(record)
                self.permission_gov.preflight_target(record)
                self.planner.process(record)

                if self._should_hold(record):
                    record.status = "pending_review"
                    self.db.insert_file(record.to_dict())
                    stats["pending_review"] += 1
                    self.audit.log_file_step(
                        record,
                        "pending_review",
                        success=False,
                        detail=f"priority={record.review_priority}, action={record.governance_action}",
                    )
                    logger.warning(f"  发布前拦截: {record.review_priority} / {record.review_conclusion}")
                    continue

                record.status = "done"
                if not self._publish_prepared_record(record):
                    if record.status != "pending_review":
                        record.status = "failed"
                    self.db.insert_file(record.to_dict())
                    self.audit.log_file_complete(record)
                    stats[record.status] += 1
                    logger.warning(f"  发布未完成: {record.status}")
                    continue
                self.db.insert_file(record.to_dict())
                self.audit.log_file_complete(record)
                results.append({
                    "file_name": safe_name,
                    "category": self.planner.sensitivity.redact(
                        record.category or ""
                    ),
                    "sub_category": self.planner.sensitivity.redact(
                        record.sub_category or ""
                    ),
                    "summary": self.planner.sensitivity.redact(
                        record.summary or ""
                    ),
                    "drive_url": record.drive_url,
                    "doc_url": record.doc_url,
                    "publication_action": record.publication_action,
                    "acceptance_status": record.acceptance_status,
                    "quality_score": record.quality_score,
                    "production_ready": record.production_ready,
                })
                stats["done"] += 1
                logger.info(f"  完成 ✓")
                time.sleep(0.2)
            except Exception as e:
                safe_error = self.planner.sensitivity.redact(str(e))
                logger.error(f"  处理失败: {safe_error}")
                record.status = "failed"
                record.error_message = safe_error
                self.db.insert_file(record.to_dict())
                self.audit.log_file_complete(record)
                stats["failed"] += 1

        if self.bitable_pub.available:
            try:
                all_done = [FileRecord.from_dict(r) for r in self.db.get_successful_records()]
                sync_result = self.bitable_pub.sync_agent_context(all_done)
                logger.info(f"Agent 上下文同步: 新增 {sync_result['created']}，更新 {sync_result['updated']}")
            except Exception as e:
                logger.warning(f"Agent 上下文同步失败: {e}")

        graph_result = self._sync_knowledge_graph()
        self.audit.log_pipeline_complete(stats)
        dashboard_url = self.config.get("feishu", {}).get("bitable", {}).get("url", "")
        card = self.reporter.build_card(results, stats, dashboard_url)
        logger.info(f"处理完成: {stats}")
        return {
            "stats": stats,
            "results": results,
            "card": card,
            "knowledge_graph": graph_result,
            "wiki_catalog": self.wiki_snapshot,
        }

    def _sync_knowledge_graph(self) -> dict:
        try:
            records = [
                FileRecord.from_dict(item)
                for item in self.db.get_successful_records()
            ]
            graph = self.graph_pub.build(records)
            return self.graph_pub.publish(graph)
        except Exception as e:
            logger.warning(f"知识关系图同步失败: {e}")
            return {
                "status": "failed",
                "published": False,
                "reason": str(e),
            }

    def _should_hold(self, record: FileRecord) -> bool:
        if record.sensitivity_level == "high":
            return True
        if self.publication_mode == "gated":
            return True
        if self.docx_pub.enabled and self.require_knowledge_page_confirmation:
            return True
        checks = {
            "high_sensitivity": record.sensitivity_level == "high",
            "name_conflict": bool(record.conflict_status),
            "unparseable": self.planner.quality.is_unparseable(record),
        }
        return any(checks.get(reason, False) for reason in self.block_on)

    def _apply_default_target(self, record: FileRecord):
        wiki_cfg = self.config.get("feishu", {}).get("wiki", {})
        record.target_space_id = (
            record.target_space_id or wiki_cfg.get("space_id", "")
        )
        record.target_node_token = (
            record.target_node_token or wiki_cfg.get("parent_node_token", "")
        )
        if not record.target_page_path:
            record.target_page_path = "/".join(
                part for part in (
                    record.category or record.domain,
                    record.sub_category or record.doc_type,
                )
                if part
            )
        if self.wiki_snapshot.get("status") == "ok":
            target = self.wiki_catalog.resolve_target_node(
                record.category or record.domain or "",
                record.sub_category or record.doc_type or "",
            )
            if target:
                record.target_space_id = (
                    record.target_space_id
                    or self.wiki_catalog.resolved_space_id
                )
                record.target_node_token = target.get("node_token", "")
                record.target_page_path = target.get("path", "")

    def _load_wiki_catalog(self):
        if self.wiki_snapshot.get("status") != "not_loaded":
            return
        self.wiki_snapshot = self.wiki_catalog.snapshot()

    def refresh_all(self) -> dict:
        logger.warning("执行全量刷新：将清空 Bitable 两表记录和本地同步状态")
        if self.bitable_pub.available:
            if self.bitable_pub.knowledge_table_id:
                n = self.bitable_pub.clear_table(self.bitable_pub.knowledge_table_id)
                logger.info(f"已清空知识材料表 {n} 条记录")
            if self.bitable_pub.agent_table_id:
                n = self.bitable_pub.clear_table(self.bitable_pub.agent_table_id)
                logger.info(f"已清空 Agent 上下文表 {n} 条记录")
        self.db.clear_all_records()
        logger.info("已清空本地数据库")
        return self.run()

    def govern_permissions(self) -> dict:
        records = self.db.get_successful_records()
        tokens = []
        for r in records:
            url = r.get("drive_url", "")
            if "/file/" in url:
                token = url.rsplit("/file/", 1)[-1].split("?")[0].rstrip("/")
                if token:
                    tokens.append(token)
        logger.info(f"对 {len(tokens)} 个文件执行权限治理...")
        result = self.permission_gov.govern_existing(tokens)
        logger.info(f"权限治理完成: 成功 {result['success']}, 失败 {result['failed']}")
        return result

    def quality_review(self, record_ids: list = None) -> dict:
        records = [
            FileRecord.from_dict(item)
            for item in self.db.get_successful_records()
            if not record_ids or item.get("id") in record_ids
        ]
        results = []
        for record in records:
            self.planner.quality.process(record)
            self.db.insert_file(record.to_dict())
            results.append({
                "id": record.id,
                "file_name": self.planner.sensitivity.redact(
                    record.file_name
                ),
                "quality_score": record.quality_score,
                "quality_dimensions": record.quality_dimensions,
                "production_ready": record.production_ready,
                "review_priority": record.review_priority,
                "next_review_at": record.next_review_at,
            })
        return {
            "stats": {
                "total": len(records),
                "production_ready": sum(
                    1 for item in results if item["production_ready"]
                ),
                "needs_review": sum(
                    1 for item in results if not item["production_ready"]
                ),
            },
            "results": results,
        }

    def due_reviews(self, as_of: str = "", create_tasks: bool = False) -> dict:
        from datetime import date
        target = as_of or date.today().isoformat()
        items = self.db.get_due_reviews(target)
        safe_items = [
            {
                **item,
                "file_name": self.planner.sensitivity.redact(
                    item.get("file_name", "")
                ),
            }
            for item in items
        ]
        result = {
            "as_of": target,
            "count": len(items),
            "items": safe_items,
            "tasks": {"created": 0, "reused": 0, "skipped": 0, "failed": 0, "details": []},
        }
        if not create_tasks:
            return result
        for item in items:
            had_task = bool(item.get("review_task_guid"))
            try:
                task = self.task_pub.create_review_task(item)
                if not task.get("task_guid"):
                    result["tasks"]["skipped"] += 1
                else:
                    self.db.update_review_task(
                        item["id"],
                        task["task_guid"],
                        task["task_url"],
                        task["owner"],
                        task["reminder"],
                    )
                    result["tasks"]["reused" if had_task else "created"] += 1
                result["tasks"]["details"].append({
                    "record_id": item["id"],
                    "file_name": self.planner.sensitivity.redact(
                        item["file_name"]
                    ),
                    **task,
                })
            except Exception as e:
                result["tasks"]["failed"] += 1
                result["tasks"]["details"].append({
                    "record_id": item["id"],
                    "file_name": self.planner.sensitivity.redact(
                        item["file_name"]
                    ),
                    "error": self.planner.sensitivity.redact(str(e)),
                })
        return result

    def close(self):
        self.db.close()
