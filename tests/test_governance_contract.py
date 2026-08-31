import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.governance.permission import PermissionGovernor
from src.models.file_record import FileRecord
from src.processors.dedup import DedupProcessor
from src.publishers.feishu_bitable import FeishuBitablePublisher
from src.publishers.feishu_drive import FeishuDrivePublisher
from src.utils.db import GovernanceDB


class GovernanceContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = GovernanceDB(str(self.root / "governance.db"))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_extended_fields_round_trip(self):
        record = FileRecord(
            source_path="/tmp/policy.md",
            file_name="policy.md",
            file_hash="hash-policy",
            target_space_id="space-1",
            target_node_token="node-1",
            target_page_path="制度/请假",
            publication_action="update",
            permission_status="readable",
            permission_issues=[{"code": "write_unverified"}],
            doc_token="doc-1",
            doc_url="https://example.com/docx/doc-1",
            knowledge_page_status="verified",
            readback_verified=True,
            review_owner="ou_owner",
            review_task_guid="task-1",
            media_status="resolved",
            media_evidence={"kind": "sidecar"},
            acceptance_status="success",
            acceptance_details=[{"check": "readback", "passed": True}],
        )
        self.db.insert_file(record.to_dict())

        restored = self.db.get_all_files()[0]
        self.assertEqual(restored["publication_action"], "update")
        self.assertEqual(restored["permission_issues"][0]["code"], "write_unverified")
        self.assertEqual(restored["media_evidence"]["kind"], "sidecar")
        self.assertTrue(restored["readback_verified"])
        self.assertEqual(restored["acceptance_status"], "success")

    def test_permission_preflight_reports_observed_readability(self):
        record = FileRecord(target_node_token="wikcn123")
        governor = PermissionGovernor(
            {"governance": {}},
            cli_path="/tmp/fake-lark-cli",
        )
        governor.cli_path = "/tmp/fake-lark-cli"
        with patch("src.governance.permission.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "{}"
            run.return_value.stderr = ""
            governor.preflight_target(record)

        self.assertEqual(record.permission_status, "readable")
        self.assertEqual(record.permission_issues[0]["code"], "write_unverified")

    def test_provider_none_disables_all_remote_publishers(self):
        config = {
            "feishu": {
                "provider": "none",
                "drive": {"enabled": True},
                "bitable": {
                    "enabled": True,
                    "base_token": "base",
                    "knowledge_table_id": "table",
                },
            },
            "governance": {},
        }
        drive = FeishuDrivePublisher(config)
        drive.cli_path = "/tmp/fake-cli"
        bitable = FeishuBitablePublisher(config, self.db)
        bitable.cli_path = "/tmp/fake-cli"
        governor = PermissionGovernor(config, cli_path="/tmp/fake-cli")
        governor.cli_path = "/tmp/fake-cli"
        record = FileRecord(target_node_token="wikcn123")

        with patch("src.governance.permission.subprocess.run") as run:
            governor.preflight_target(record)

        self.assertFalse(drive.available)
        self.assertFalse(bitable.available)
        self.assertFalse(governor.enabled)
        self.assertEqual(record.permission_status, "unknown")
        run.assert_not_called()

    def test_permission_command_failure_is_blocking(self):
        record = FileRecord(
            file_name="policy.md",
            drive_url="https://example.feishu.cn/file/token",
        )
        governor = PermissionGovernor(
            {"feishu": {"provider": "cli"}, "governance": {}},
            cli_path="/tmp/fake-cli",
        )
        governor.cli_path = "/tmp/fake-cli"
        governor.enabled = True
        with patch("src.governance.permission.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "permission denied"
            governor.process(record)

        self.assertEqual(record.permission_status, "blocked")
        self.assertEqual(
            record.permission_issues[0]["code"],
            "permission_apply_failed",
        )

    def test_permission_uses_supported_public_patch_command(self):
        record = FileRecord(
            file_name="policy.md",
            drive_url="https://example.feishu.cn/file/token",
        )
        governor = PermissionGovernor(
            {"feishu": {"provider": "cli"}, "governance": {}},
            cli_path="/tmp/fake-cli",
        )
        governor.cli_path = "/tmp/fake-cli"
        governor.enabled = True
        with patch("src.governance.permission.subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0,
                stdout="{}",
                stderr="",
            )
            governor.process(record)

        public_command = run.call_args_list[1].args[0]
        secure_label_command = run.call_args_list[0].args[0]
        self.assertIn("--type", secure_label_command)
        self.assertEqual(
            secure_label_command[secure_label_command.index("--type") + 1],
            "file",
        )
        self.assertEqual(
            public_command[1:4],
            ["drive", "permission.public", "patch"],
        )
        self.assertIn("--yes", public_command)
        self.assertIn("--data", public_command)
        self.assertEqual(record.permission_status, "verified")

    def test_bitable_context_contains_knowledge_lifecycle(self):
        config = {
            "feishu": {
                "provider": "none",
                "bitable": {
                    "enabled": False,
                    "knowledge_page_field": "知识页",
                },
            },
            "knowledge": {"project_name": "测试"},
            "governance": {},
            "taxonomy": {},
        }
        publisher = FeishuBitablePublisher(config, self.db)
        record = FileRecord(
            file_name="policy.md",
            file_hash="abc123",
            status="done",
            drive_url="https://example.com/file/a",
            doc_url="https://example.com/docx/b",
            doc_token="b",
            publication_action="create",
            acceptance_status="success",
            permission_status="readable",
        )
        fields = publisher._build_knowledge_fields(record)
        contexts = publisher._build_agent_context([record])
        knowledge = next(item for item in contexts if item["key"].startswith("knowledge:"))

        self.assertEqual(fields["知识页"], record.doc_url)
        self.assertEqual(fields["分类"], ["待人工复核"])
        self.assertEqual(fields["文件类型"], ["other"])
        self.assertIn('"doc_url": "https://example.com/docx/b"', knowledge["fields"]["内容"])
        self.assertIn('"acceptance_status": "success"', knowledge["fields"]["内容"])

    def test_bitable_uses_supported_upsert_and_readback_commands(self):
        config = {
            "feishu": {
                "provider": "cli",
                "bitable": {
                    "enabled": True,
                    "base_token": "base",
                    "knowledge_table_id": "table",
                },
            },
            "governance": {},
            "taxonomy": {},
        }
        publisher = FeishuBitablePublisher(config, self.db)
        publisher.cli_path = "/tmp/fake-cli"
        record = FileRecord(file_name="policy.md", file_hash="hash")
        responses = [
            {"data": {"record": {"record_id": "rec-1"}}},
            {
                "data": {
                    "items": [{
                        "record_id": "rec-1",
                        "fields": {"文件名": "policy.md"},
                    }]
                }
            },
        ]

        with patch("src.publishers.feishu_bitable.subprocess.run") as run:
            run.side_effect = [
                SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(response),
                    stderr="",
                )
                for response in responses
            ]
            self.assertEqual(publisher.publish_record(record), "rec-1")
            self.assertTrue(publisher.verify_record(record))

        create_command = run.call_args_list[0].args[0]
        read_command = run.call_args_list[1].args[0]
        self.assertIn("+record-upsert", create_command)
        self.assertIn("--json", create_command)
        self.assertNotIn("+record-create", create_command)
        self.assertIn("+record-get", read_command)

    def test_new_version_inherits_remote_identity_for_update(self):
        existing = FileRecord(
            source_path="/tmp/old/policy.md",
            file_name="policy.md",
            file_hash="same-hash",
            file_size=100,
            status="done",
            version=1,
            drive_url="https://example.feishu.cn/file/source",
            doc_token="doc-existing",
            doc_url="https://example.feishu.cn/docx/doc-existing",
            record_id="rec-existing",
        )
        self.db.insert_file(existing.to_dict())
        candidate = FileRecord(
            source_path="/tmp/new/policy.md",
            file_name="policy.md",
            file_hash="same-hash",
            file_size=100,
        )

        DedupProcessor(self.db).process(candidate)

        self.assertTrue(candidate.is_new_version)
        self.assertEqual(candidate.version, 2)
        self.assertEqual(candidate.doc_token, "doc-existing")
        self.assertEqual(candidate.record_id, "rec-existing")
        self.assertEqual(candidate.drive_url, existing.drive_url)


if __name__ == "__main__":
    unittest.main()
