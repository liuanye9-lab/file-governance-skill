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

        stats = {"total": len(all_records), "done": 0, "skipped": 0, "failed": 0}
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
                upload_ok = False
                if self.drive_pub.available and record.file_size > 0:
                    try:
                        self.drive_pub.upload(record)
                        upload_ok = True
                    except Exception as e:
                        logger.warning(f"  上传失败: {e}，尝试查找已有文件...")
                        existing = self.drive_pub.find_existing_url(record.file_name)
                        if existing:
                            record.drive_url = existing
                            record.log_step("upload", f"复用已有链接 {existing}")
                            upload_ok = True
                elif record.file_size <= 0:
                    record.log_step("upload", "空文件，仅本地归档不上传", success=False)
                if upload_ok and record.drive_url:
                    try:
                        self.permission_gov.process(record)
                    except Exception as e:
                        logger.debug(f"权限治理跳过: {e}")
                if self.bitable_pub.available:
                    try:
                        self.bitable_pub.publish_record(record)
                    except Exception as e:
                        logger.warning(f"Bitable 写入失败: {e}")
                # 需上传却未成功（非空文件且 drive 可用但上传失败）标记为 failed，便于重试
                if self.drive_pub.available and record.file_size > 0 and not record.drive_url:
                    record.status = "failed"
                    record.error_message = "上传飞书失败且未找到已有文件"
                    self.db.insert_file(record.to_dict())
                    self.audit.log_file_complete(record)
                    stats["failed"] += 1
                    logger.warning("  标记为失败（待重试）")
                    continue
                record.status = "done"
                self.db.insert_file(record.to_dict())
                self.audit.log_file_complete(record)
                results.append({
                    "file_name": record.file_name,
                    "category": record.category,
                    "sub_category": record.sub_category,
                    "summary": record.summary,
                    "drive_url": record.drive_url,
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

    def close(self):
        self.db.close()
