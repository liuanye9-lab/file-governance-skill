import os
import tempfile
import unittest
from pathlib import Path

from src.governance.planner import GovernancePlanner
from src.governance.quality import QualityReviewer
from src.governance.sensitivity import SensitivityScanner
from src.models.file_record import FileRecord
from src.pipeline import GovernancePipeline
from src.utils.db import GovernanceDB


def local_config(root: str, db_path: str) -> dict:
    return {
        "sources": {
            "inbox": {"enabled": True, "path": root},
            "wechat": {"enabled": False},
            "local_folders": [],
        },
        "feishu": {"provider": "none", "bitable": {"enabled": False}},
        "knowledge": {"project_name": "测试项目"},
        "taxonomy": {"categories": []},
        "governance": {
            "publication_mode": "auto",
            "quality_threshold": 75,
            "block_on": ["high_sensitivity", "name_conflict", "unparseable"],
        },
        "processing": {"max_text_length": 8000},
        "db": {"path": db_path},
    }


class GovernanceControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.db_path = str(self.root / "data" / "governance.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_sensitive_evidence_is_masked_and_held(self):
        path = self.root / "credentials.txt"
        path.write_text("api_key = sk-test-1234567890abcdef", encoding="utf-8")
        record = FileRecord.from_path(str(path), "test")
        record.text_content = path.read_text(encoding="utf-8")
        SensitivityScanner().process(record)
        self.assertEqual(record.sensitivity_level, "high")
        self.assertEqual(record.governance_action, "hold")
        evidence = record.sensitivity_findings[0]["evidence"][0]
        self.assertNotIn("1234567890abcdef", evidence)
        self.assertNotIn("1234567890abcdef", record.text_content)
        self.assertIn("[REDACTED:credential]", record.text_content)

    def test_quality_review_production_ready(self):
        path = self.root / "report.md"
        path.write_text("# 财务报告\n营收、利润和现金流分析。", encoding="utf-8")
        record = FileRecord.from_path(str(path), "test")
        record.compute_hash()
        record.text_content = path.read_text(encoding="utf-8")
        record.domain = "金融财务"
        record.doc_type = "报告分析"
        record.category = "金融财务"
        record.tags = ["营收", "利润"]
        record.summary = "用于判断企业盈利能力与现金流风险。"
        record.log_step("parse", "ok")
        QualityReviewer().process(record)
        self.assertTrue(record.production_ready)
        self.assertGreaterEqual(record.quality_score, 75)
        self.assertEqual(record.review_cycle_days, 30)
        self.assertTrue(record.next_review_at)

    def test_same_name_different_content_is_conflict(self):
        db = GovernanceDB(self.db_path)
        old_path = self.root / "policy.md"
        old_path.write_text("旧版本制度", encoding="utf-8")
        old = FileRecord.from_path(str(old_path), "test")
        old.compute_hash()
        old.status = "done"
        db.insert_file(old.to_dict())

        new_path = self.root / "other" / "policy.md"
        new_path.parent.mkdir()
        new_path.write_text("新版本且内容不同", encoding="utf-8")
        new = FileRecord.from_path(str(new_path), "test")
        new.compute_hash()
        new.text_content = new_path.read_text(encoding="utf-8")
        new.domain = "运营管理"
        new.doc_type = "制度规范"
        new.category = "运营管理"
        new.tags = ["制度"]
        new.summary = "制度变更。"
        GovernancePlanner(db).process(new)
        self.assertEqual(new.conflict_status, "same_name_different_content")
        self.assertEqual(new.governance_action, "hold")
        self.assertFalse(new.production_ready)
        db.close()

    def test_plan_then_publish_local_only(self):
        source = self.inbox / "report.md"
        source.write_text("# 财务分析报告\n营收增长，利润稳定，现金流健康。", encoding="utf-8")
        pipeline = GovernancePipeline(local_config(str(self.inbox), self.db_path))
        pipeline.drive_pub.cli_path = ""
        pipeline.bitable_pub.cli_path = ""
        pipeline.permission_gov.cli_path = ""
        try:
            planned = pipeline.plan(source="inbox")
            self.assertEqual(planned["stats"]["planned"], 1)
            self.assertEqual(planned["manifest"]["summary"]["ready"], 1)
            queue = pipeline.db.get_review_queue()
            self.assertEqual(queue[0]["status"], "planned")
            repeated = pipeline.plan(source="inbox")
            self.assertEqual(repeated["stats"]["total"], 0)

            published = pipeline.publish_review_queue()
            self.assertEqual(published["stats"]["done"], 1)
            self.assertEqual(pipeline.db.get_stats()["done"], 1)
        finally:
            pipeline.close()

    def test_auto_mode_holds_high_sensitivity(self):
        source = self.inbox / "secret.txt"
        source.write_text("password = super-secret-credential-001", encoding="utf-8")
        pipeline = GovernancePipeline(local_config(str(self.inbox), self.db_path))
        pipeline.drive_pub.cli_path = ""
        pipeline.bitable_pub.cli_path = ""
        pipeline.permission_gov.cli_path = ""
        try:
            result = pipeline.run(source="inbox")
            self.assertEqual(result["stats"]["pending_review"], 1)
            queue = pipeline.db.get_review_queue()
            self.assertEqual(queue[0]["sensitivity_level"], "high")
        finally:
            pipeline.close()

    def test_high_sensitivity_cannot_be_force_published(self):
        source = self.inbox / "secret.txt"
        source.write_text("api_key = sk-sensitive-secret-001", encoding="utf-8")
        pipeline = GovernancePipeline(local_config(str(self.inbox), self.db_path))
        pipeline.drive_pub.cli_path = ""
        pipeline.bitable_pub.cli_path = ""
        pipeline.permission_gov.cli_path = ""
        try:
            pipeline.plan(source="inbox")
            published = pipeline.publish_review_queue(approve_risk=True)
            self.assertEqual(published["stats"]["blocked"], 1)
            self.assertEqual(published["stats"]["done"], 0)
        finally:
            pipeline.close()

    def test_high_sensitivity_is_hard_gate_even_if_block_on_omits_it(self):
        source = self.inbox / "api_key=sk-secret-metadata-001.txt"
        source.write_text("普通正文", encoding="utf-8")
        config = local_config(str(self.inbox), self.db_path)
        config["governance"]["block_on"] = []
        pipeline = GovernancePipeline(config)
        try:
            result = pipeline.run(source="inbox")
            queue = pipeline.db.get_review_queue()
            self.assertEqual(result["stats"]["pending_review"], 1)
            self.assertEqual(queue[0]["sensitivity_level"], "high")
            self.assertEqual(queue[0]["status"], "pending_review")
        finally:
            pipeline.close()

    def test_unparsed_media_is_not_production_ready(self):
        path = self.root / "poster.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
        record = FileRecord.from_path(str(path), "test")
        record.compute_hash()
        record.text_content = "[图片文件] poster.png，大小 19 字节"
        record.domain = "通用"
        record.doc_type = "其他文档"
        record.category = "通用"
        record.tags = ["PNG"]
        record.summary = "仅提取到图片元数据。"
        record.log_step("parse", "placeholder only")
        QualityReviewer().process(record)
        self.assertFalse(record.production_ready)
        self.assertEqual(record.governance_action, "hold")
        self.assertEqual(record.review_priority, "P0")

    def test_due_review_query(self):
        db = GovernanceDB(self.db_path)
        record = FileRecord(
            source_path="/tmp/policy.md",
            file_name="policy.md",
            file_hash="hash-policy",
            captured_at="2026-01-01T00:00:00",
            status="done",
            next_review_at="2026-02-01",
            review_priority="P1",
        )
        db.insert_file(record.to_dict())
        due = db.get_due_reviews("2026-03-01")
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["file_name"], "policy.md")
        db.close()


if __name__ == "__main__":
    unittest.main()
