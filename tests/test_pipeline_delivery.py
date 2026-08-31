import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.models.file_record import FileRecord
from src.pipeline import GovernancePipeline


def local_config(root: Path) -> dict:
    return {
        "sources": {
            "inbox": {"enabled": False},
            "wechat": {"enabled": False},
            "local_folders": [],
        },
        "feishu": {
            "provider": "none",
            "bitable": {"enabled": False},
            "wiki": {"enabled": False},
        },
        "knowledge_pages": {"enabled": False},
        "review_tasks": {"enabled": False},
        "knowledge_graph": {"enabled": False},
        "knowledge": {"project_name": "测试项目"},
        "taxonomy": {"categories": []},
        "governance": {
            "publication_mode": "gated",
            "quality_threshold": 75,
            "block_on": ["high_sensitivity", "name_conflict", "unparseable"],
        },
        "processing": {"max_text_length": 8000},
        "db": {"path": str(root / "governance.db")},
    }


class PipelineDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "policy.md"
        source.write_text("# 制度\n用于规范发布流程。", encoding="utf-8")
        self.record = FileRecord.from_path(str(source), "test")
        self.record.compute_hash()
        self.record.text_content = source.read_text(encoding="utf-8")
        self.record.summary = "规范发布流程。"
        self.record.category = "制度"
        self.record.doc_type = "制度规范"
        self.record.production_ready = True
        self.record.quality_score = 90
        self.record.publication_action = "create"
        self.record.status = "done"
        self.pipeline = GovernancePipeline(local_config(self.root))

    def tearDown(self):
        self.pipeline.close()
        self.temp.cleanup()

    def _install_remote_fakes(self, verified: bool = True):
        def upload(record):
            record.drive_url = "https://example.feishu.cn/file/source"

        def permission(record):
            record.permission_status = "verified"
            record.security_level = "L2-Internal"
            record.share_permission = "tenant_readable"
            record.log_step(
                "permission",
                "密级=L2-Internal, 共享=tenant_readable",
            )

        def publish_doc(record):
            record.doc_token = "doc-token"
            record.doc_url = "https://example.feishu.cn/docx/doc-token"
            record.knowledge_page_status = (
                "verified" if verified else "created_unverified"
            )
            record.readback_verified = verified
            return {
                "status": record.knowledge_page_status,
                "readback_verified": verified,
            }

        def publish_bitable(record):
            record.record_id = "record-id"
            return record.record_id

        self.pipeline.drive_pub = SimpleNamespace(
            available=True,
            upload=upload,
            find_existing_url=lambda _: None,
        )
        self.pipeline.permission_gov = SimpleNamespace(process=permission)
        self.pipeline.docx_pub = SimpleNamespace(
            enabled=True,
            publish_record=publish_doc,
        )
        self.pipeline.bitable_pub = SimpleNamespace(
            available=True,
            publish_record=publish_bitable,
            update_record=lambda _: True,
            verify_record=lambda _: True,
        )

    def test_full_delivery_requires_all_acceptance_evidence(self):
        self._install_remote_fakes(verified=True)

        success = self.pipeline._publish_prepared_record(self.record)

        self.assertTrue(success)
        self.assertEqual(self.record.acceptance_status, "success")
        self.assertEqual(self.record.knowledge_page_status, "verified")
        self.assertTrue(self.record.readback_verified)

    def test_unverified_knowledge_page_is_not_reported_successful(self):
        self._install_remote_fakes(verified=False)

        success = self.pipeline._publish_prepared_record(self.record)

        self.assertFalse(success)
        self.assertIn("未通过回读验证", self.record.error_message)
        self.assertEqual(self.record.status, "pending_review")
        self.assertNotEqual(self.record.acceptance_status, "success")

    def test_due_review_task_is_persisted(self):
        self.record.next_review_at = "2026-01-01"
        self.pipeline.db.insert_file(self.record.to_dict())
        self.pipeline.task_pub = SimpleNamespace(
            create_review_task=lambda _: {
                "task_guid": "task-guid",
                "task_url": "https://applink.feishu.cn/task-guid",
                "owner": "ou_owner",
                "reminder": "2025-12-31T00:00:00",
            }
        )

        result = self.pipeline.due_reviews(
            "2026-08-31",
            create_tasks=True,
        )
        restored = self.pipeline.db.get_all_files()[0]

        self.assertEqual(result["tasks"]["created"], 1)
        self.assertEqual(restored["review_task_guid"], "task-guid")
        self.assertEqual(restored["review_owner"], "ou_owner")

    def test_retry_updates_existing_bitable_record(self):
        self._install_remote_fakes(verified=True)
        self.record.record_id = "existing-record"
        calls = {"create": 0, "update": 0}

        def create(_):
            calls["create"] += 1
            return "duplicate"

        def update(_):
            calls["update"] += 1
            return True

        self.pipeline.bitable_pub.publish_record = create
        self.pipeline.bitable_pub.update_record = update

        success = self.pipeline._publish_prepared_record(self.record)

        self.assertTrue(success)
        self.assertEqual(calls, {"create": 0, "update": 2})

    def test_reference_requires_real_remote_readback(self):
        self._install_remote_fakes(verified=True)
        self.record.publication_action = "reference"
        self.pipeline.drive_pub.verify_reference = lambda _: False

        success = self.pipeline._publish_prepared_record(self.record)

        self.assertFalse(success)
        self.assertEqual(self.record.status, "pending_review")
        self.assertIn("引用目标回读验证失败", self.record.error_message)

    def test_final_bitable_update_failure_defers_completion(self):
        self._install_remote_fakes(verified=True)
        calls = {"updates": 0}

        def update(_):
            calls["updates"] += 1
            return calls["updates"] == 1

        self.record.record_id = "existing-record"
        self.pipeline.bitable_pub.update_record = update

        success = self.pipeline._publish_prepared_record(self.record)

        self.assertFalse(success)
        self.assertEqual(self.record.status, "pending_review")
        self.assertEqual(self.record.acceptance_status, "pending")
        self.assertIn("验收状态回写或回读失败", self.record.error_message)

    def test_retry_reuses_existing_drive_url(self):
        self._install_remote_fakes(verified=True)
        self.record.drive_url = "https://example.feishu.cn/file/existing"
        uploads = []
        self.pipeline.drive_pub.upload = lambda _: uploads.append(True)

        success = self.pipeline._publish_prepared_record(self.record)

        self.assertTrue(success)
        self.assertEqual(uploads, [])
        self.assertEqual(
            self.record.drive_url,
            "https://example.feishu.cn/file/existing",
        )


if __name__ == "__main__":
    unittest.main()
