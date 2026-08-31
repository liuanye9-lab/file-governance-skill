import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.models.file_record import FileRecord
from src.publishers.feishu_docx import (
    FeishuDocxPublishError,
    FeishuDocxPublisher,
)


def make_record(root: Path, text: str = "这是已经治理的正文。") -> FileRecord:
    source = root / "policy.md"
    source.write_text(text, encoding="utf-8")
    record = FileRecord.from_path(str(source), "inbox", "治理收件箱")
    record.compute_hash()
    record.text_content = text
    record.summary = "说明制度目标、适用范围和执行要求。"
    record.domain = "运营管理"
    record.doc_type = "制度规范"
    record.category = "制度"
    record.sub_category = "内部流程"
    record.tags = ["治理", "流程"]
    record.quality_score = 88
    record.production_ready = True
    record.review_conclusion = "生产就绪"
    return record


def publisher_config(**docx_overrides) -> dict:
    docx = {
        "enabled": True,
        "verify_after_create": True,
        "fallback_local": True,
    }
    docx.update(docx_overrides)
    return {
        "feishu": {
            "provider": "cli",
            "docx": docx,
        }
    }


class FeishuDocxPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_builds_standard_governance_page_without_local_source_path(self):
        record = make_record(self.root)
        record.drive_url = (
            "https://example.feishu.cn/file/file-token"
            "?signed_secret=must-not-leak"
        )
        publisher = FeishuDocxPublisher(publisher_config())

        page = publisher.build_page(
            record,
            exceptions=["需由制度负责人确认生效日期"],
            changes=[{
                "version": "v1",
                "time": "2026-08-31",
                "change": "首次发布",
                "source_revision": "rev-001",
            }],
        )

        for section in publisher.REQUIRED_SECTIONS:
            self.assertIn(f"## {section}", page["content"])
        self.assertIn("| 治理字段 | 值 |", page["content"])
        self.assertIn("### 核心摘要", page["content"])
        self.assertIn("首次发布", page["content"])
        self.assertIn("https://example.feishu.cn/file/file-token", page["content"])
        self.assertNotIn("signed_secret", page["content"])
        self.assertNotIn(str(self.root), page["content"])

    def test_medium_sensitive_values_are_redacted_before_cli_input(self):
        record = make_record(
            self.root,
            "联系人 13800138000，邮箱 alice@example.com。",
        )
        sent = {}

        def runner(command, **kwargs):
            if "+create" in command:
                sent["command"] = command
                sent["content"] = kwargs["input"]
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": True,
                        "data": {
                            "document": {
                                "document_id": "docx-medium",
                                "url": "https://example.feishu.cn/docx/docx-medium",
                            }
                        },
                    }),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "ok": True,
                    "data": {
                        "document": {
                            "document_id": "docx-medium",
                            "content": sent["content"],
                        }
                    },
                }),
                stderr="",
            )

        publisher = FeishuDocxPublisher(publisher_config(), runner=runner)
        publisher.cli_path = "/fake/lark-cli"
        result = publisher.publish_record(record)

        self.assertEqual(result["status"], "verified")
        self.assertNotIn("13800138000", sent["content"])
        self.assertNotIn("alice@example.com", sent["content"])
        self.assertNotIn("13800138000", " ".join(sent["command"]))
        self.assertIn("REDACTED:mobile", sent["content"])
        self.assertIn("REDACTED:email", sent["content"])

    def test_high_sensitive_original_is_blocked_without_cli_call(self):
        secret = "sk-test-1234567890abcdef"
        record = make_record(self.root, f"api_key = {secret}")
        called = []

        def runner(command, **kwargs):
            called.append((command, kwargs))
            raise AssertionError("高敏记录不应调用飞书")

        publisher = FeishuDocxPublisher(publisher_config(), runner=runner)
        publisher.cli_path = "/fake/lark-cli"
        result = publisher.publish_record(record)

        self.assertEqual(result["status"], "blocked_sensitive")
        self.assertEqual(result["mode"], "blocked")
        self.assertFalse(result["created"])
        self.assertEqual(called, [])
        self.assertNotIn(secret, result["content"])
        self.assertNotIn(record.text_content, result["content"])
        self.assertEqual(record.knowledge_page_status, "blocked_sensitive")

    def test_dry_run_performs_no_io(self):
        record = make_record(self.root)

        def runner(command, **kwargs):
            raise AssertionError("dry 模式不应启动子进程")

        publisher = FeishuDocxPublisher(publisher_config(), runner=runner)
        publisher.cli_path = "/fake/lark-cli"
        result = publisher.publish_record(record, dry_run=True)

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["mode"], "dry")
        self.assertFalse(result["created"])
        self.assertEqual(result["local_path"], "")

    def test_local_only_writes_sanitized_markdown(self):
        output_dir = self.root / "out"
        record = make_record(
            self.root,
            "值班电话 13800138000，按内部流程执行。",
        )
        publisher = FeishuDocxPublisher(publisher_config(
            local_only=True,
            local_output_dir=str(output_dir),
        ))
        result = publisher.publish_record(record)

        self.assertEqual(result["status"], "local_only")
        self.assertEqual(result["mode"], "local")
        local_path = Path(result["local_path"])
        self.assertTrue(local_path.is_file())
        content = local_path.read_text(encoding="utf-8")
        self.assertNotIn("13800138000", content)
        self.assertIn("REDACTED:mobile", content)

    def test_create_and_readback_verification(self):
        record = make_record(self.root)
        calls = []
        created_content = {"value": ""}

        def runner(command, **kwargs):
            calls.append(command)
            if "+create" in command:
                created_content["value"] = kwargs["input"]
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": True,
                        "data": {
                            "document": {
                                "document_id": "docx-verified",
                                "revision_id": 1,
                                "url": "https://example.feishu.cn/docx/docx-verified",
                            },
                            "warnings": [],
                        },
                    }),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "ok": True,
                    "data": {
                        "document": {
                            "document_id": "docx-verified",
                            "content": created_content["value"],
                        }
                    },
                }),
                stderr="",
            )

        publisher = FeishuDocxPublisher(
            publisher_config(parent_token="folder-token"),
            runner=runner,
        )
        publisher.cli_path = "/fake/lark-cli"
        result = publisher.publish_record(record)

        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["created"])
        self.assertTrue(result["readback_verified"])
        self.assertEqual(record.doc_token, "docx-verified")
        self.assertEqual(
            record.doc_url,
            "https://example.feishu.cn/docx/docx-verified",
        )
        self.assertTrue(record.readback_verified)
        self.assertIn("--content", calls[0])
        self.assertIn("-", calls[0])
        self.assertIn("--parent-token", calls[0])
        self.assertIn("+fetch", calls[1])

    def test_readback_mismatch_is_reported_without_duplicate_create(self):
        record = make_record(self.root)
        create_count = 0

        def runner(command, **kwargs):
            nonlocal create_count
            if "+create" in command:
                create_count += 1
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": True,
                        "data": {
                            "document": {
                                "document_id": "docx-unverified",
                                "url": "https://example.feishu.cn/docx/docx-unverified",
                            }
                        },
                    }),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "ok": True,
                    "data": {
                        "document": {
                            "document_id": "docx-unverified",
                            "content": "只有部分内容",
                        }
                    },
                }),
                stderr="",
            )

        publisher = FeishuDocxPublisher(publisher_config(), runner=runner)
        publisher.cli_path = "/fake/lark-cli"
        result = publisher.publish_record(record)

        self.assertEqual(create_count, 1)
        self.assertEqual(result["status"], "created_unverified")
        self.assertFalse(result["readback_verified"])
        self.assertFalse(result["verification"]["fingerprint_found"])
        self.assertEqual(record.doc_token, "docx-unverified")

    def test_existing_document_is_updated_without_duplicate_create(self):
        record = make_record(self.root)
        record.publication_action = "update"
        record.doc_token = "docx-existing"
        record.doc_url = "https://example.feishu.cn/docx/docx-existing"
        calls = []
        updated_content = {"value": ""}

        def runner(command, **kwargs):
            calls.append(command)
            if "+update" in command:
                updated_content["value"] = kwargs["input"]
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"ok": True, "data": {}}),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "ok": True,
                    "data": {
                        "document": {
                            "document_id": "docx-existing",
                            "content": updated_content["value"],
                        }
                    },
                }),
                stderr="",
            )

        publisher = FeishuDocxPublisher(publisher_config(), runner=runner)
        publisher.cli_path = "/fake/lark-cli"
        result = publisher.publish_record(record)

        self.assertEqual(result["status"], "verified")
        self.assertTrue(any("+update" in command for command in calls))
        self.assertFalse(any("+create" in command for command in calls))

    def test_retry_of_create_action_updates_persisted_document(self):
        record = make_record(self.root)
        record.publication_action = "create"
        record.doc_token = "docx-partial"
        record.doc_url = "https://example.feishu.cn/docx/docx-partial"
        calls = []
        content = {"value": ""}

        def runner(command, **kwargs):
            calls.append(command)
            if "+update" in command:
                content["value"] = kwargs["input"]
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"ok": True, "data": {}}),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "data": {
                        "document": {
                            "document_id": "docx-partial",
                            "content": content["value"],
                        }
                    }
                }),
                stderr="",
            )

        publisher = FeishuDocxPublisher(publisher_config(), runner=runner)
        publisher.cli_path = "/fake/lark-cli"

        result = publisher.publish_record(record)

        self.assertEqual(result["status"], "verified")
        self.assertTrue(any("+update" in command for command in calls))
        self.assertFalse(any("+create" in command for command in calls))

    def test_update_without_document_token_is_blocked(self):
        record = make_record(self.root)
        record.publication_action = "update"
        publisher = FeishuDocxPublisher(publisher_config())
        publisher.cli_path = "/fake/lark-cli"

        result = publisher.publish_record(record)

        self.assertEqual(result["status"], "blocked_missing_document")
        self.assertFalse(result["created"])

    def test_cli_failure_raises_sanitized_error_without_implicit_retry(self):
        record = make_record(self.root)
        secret = "sk-test-1234567890abcdef"

        def runner(command, **kwargs):
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=f"request failed: api_key = {secret}",
            )

        publisher = FeishuDocxPublisher(
            publisher_config(fallback_on_error=False),
            runner=runner,
        )
        publisher.cli_path = "/fake/lark-cli"

        with self.assertRaises(FeishuDocxPublishError) as raised:
            publisher.publish_record(record)
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn("REDACTED:credential", str(raised.exception))
        self.assertEqual(record.knowledge_page_status, "failed")

    def test_failed_update_preserves_existing_document_identity(self):
        record = make_record(self.root)
        record.publication_action = "update"
        record.doc_token = "docx-existing"
        record.doc_url = "https://example.feishu.cn/docx/docx-existing"

        def runner(command, **kwargs):
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="update failed",
            )

        publisher = FeishuDocxPublisher(
            publisher_config(fallback_on_error=False),
            runner=runner,
        )
        publisher.cli_path = "/fake/lark-cli"

        with self.assertRaises(FeishuDocxPublishError):
            publisher.publish_record(record)

        self.assertEqual(record.doc_token, "docx-existing")
        self.assertEqual(
            record.doc_url,
            "https://example.feishu.cn/docx/docx-existing",
        )


if __name__ == "__main__":
    unittest.main()
