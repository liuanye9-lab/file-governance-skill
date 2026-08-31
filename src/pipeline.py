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
from .processors.classifier import Classifier
from .governance.version import VersionManager
from .governance.permission import PermissionGovernor
from .governance.audit import AuditLogger
from .governance.planner import GovernancePlanner
from .publishers.feishu_drive import FeishuDrivePublisher
from .publishers.feishu_bitable import FeishuBitablePublisher
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
        self.permission_gov = PermissionGovernor(
            self.config,
            cli_path=self.config.get("feishu", {}).get("cli_path", ""),
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
                self.metadata.process(record)
                self.dedup.process(record)
                if record.status == "skipped":
                    skipped.append(record)
                    self.db.insert_file(record.to_dict())
                    continue
                self.version_mgr.process(record)
                self.parser.process(record)
                self.classifier.process(record)
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
        }

    def publish_review_queue(
        self,
        record_ids: list = None,
        approve_risk: bool = False,
    ) -> dict:
        """发布已盘点记录；冲突/无法解析需 approve_risk，高敏资料始终拦截。"""
        queue = [
            FileRecord.from_dict(item)
            for item in self.db.get_review_queue()
            if not record_ids or item.get("id") in record_ids
        ]
        stats = {"total": len(queue), "done": 0, "blocked": 0, "failed": 0}
        results = []
        for record in queue:
            if record.sensitivity_level == "high":
                stats["blocked"] += 1
                continue
            if record.governance_action == "hold" and not approve_risk:
                stats["blocked"] += 1
                continue
            record.status = "done"
            if self._publish_prepared_record(record):
                self.db.insert_file(record.to_dict())
                self.audit.log_file_complete(record)
                stats["done"] += 1
                results.append({
                    "id": record.id,
                    "file_name": record.file_name,
                    "drive_url": record.drive_url,
                    "quality_score": record.quality_score,
                    "production_ready": record.production_ready,
                })
            else:
                record.status = "failed"
                record.error_message = record.error_message or "发布失败"
                self.db.insert_file(record.to_dict())
                stats["failed"] += 1

        if self.bitable_pub.available and stats["done"]:
            all_done = [FileRecord.from_dict(r) for r in self.db.get_successful_records()]
            self.bitable_pub.sync_agent_context(all_done)
        return {"stats": stats, "results": results}

    def _publish_prepared_record(self, record: FileRecord) -> bool:
        """发布已完成解析、分类和治理检查的记录。"""
        if self.drive_pub.available and record.file_size > 0:
            try:
                self.drive_pub.upload(record)
            except Exception as e:
                logger.warning(f"  上传失败: {e}，尝试查找已有文件...")
                existing = self.drive_pub.find_existing_url(record.file_name)
                if existing:
                    record.drive_url = existing
                    record.log_step("upload", f"复用已有链接 {existing}")
        elif record.file_size <= 0:
            record.log_step("upload", "空文件，仅本地归档不上传", success=False)

        if self.drive_pub.available and record.file_size > 0 and not record.drive_url:
            record.error_message = "上传飞书失败且未找到已有文件"
            return False
        if record.drive_url:
            self.db.update_version_drive_url(record.file_hash, record.version, record.drive_url)

        if record.drive_url:
            try:
                self.permission_gov.process(record)
            except Exception as e:
                logger.warning(f"权限治理失败: {e}")

        if self.bitable_pub.available:
            record_id = self.bitable_pub.publish_record(record)
            if not record_id:
                record.error_message = "知识材料表写入失败"
                return False
        return True

    def _process_records(self, all_records: list) -> dict:
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
            logger.info(f"[{i}/{len(all_records)}] 处理: {record.file_name}")
            try:
                self.hasher.process(record)
                self.metadata.process(record)
                self.dedup.process(record)
                if record.status == "skipped":
                    self.db.insert_file(record.to_dict())
                    stats["skipped"] += 1
                    self.audit.log_file_complete(record)
                    continue
                self.version_mgr.process(record)
                self.parser.process(record)
                self.classifier.process(record)
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
                    record.status = "failed"
                    self.db.insert_file(record.to_dict())
                    self.audit.log_file_complete(record)
                    stats["failed"] += 1
                    logger.warning("  标记为失败（待重试）")
                    continue
                self.db.insert_file(record.to_dict())
                self.audit.log_file_complete(record)
                results.append({
                    "file_name": record.file_name,
                    "category": record.category,
                    "sub_category": record.sub_category,
                    "summary": record.summary,
                    "drive_url": record.drive_url,
                    "quality_score": record.quality_score,
                    "production_ready": record.production_ready,
                })
                stats["done"] += 1
                logger.info(f"  完成 ✓")
                time.sleep(0.2)
            except Exception as e:
                logger.error(f"  处理失败: {e}")
                record.status = "failed"
                record.error_message = str(e)
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

        self.audit.log_pipeline_complete(stats)
        dashboard_url = self.config.get("feishu", {}).get("bitable", {}).get("url", "")
        card = self.reporter.build_card(results, stats, dashboard_url)
        logger.info(f"处理完成: {stats}")
        return {"stats": stats, "results": results, "card": card}

    def _should_hold(self, record: FileRecord) -> bool:
        if self.publication_mode == "gated":
            return True
        checks = {
            "high_sensitivity": record.sensitivity_level == "high",
            "name_conflict": bool(record.conflict_status),
            "unparseable": self.planner.quality.is_unparseable(record),
        }
        return any(checks.get(reason, False) for reason in self.block_on)

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

    def due_reviews(self, as_of: str = "") -> dict:
        from datetime import date
        target = as_of or date.today().isoformat()
        items = self.db.get_due_reviews(target)
        return {"as_of": target, "count": len(items), "items": items}

    def close(self):
        self.db.close()
