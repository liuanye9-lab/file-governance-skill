import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.publishers.feishu_tasks import FeishuTaskPublisher


def task_config(enabled=True, provider="cli") -> dict:
    return {
        "feishu": {
            "provider": provider,
            "cli_path": sys.executable,
            "tasks": {
                "enabled": enabled,
                "owner": "ou_review_owner",
                "tasklist_guid": "review-list-guid",
                "reminder_minutes_before": 1440,
            },
        },
    }


def flag_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


class FeishuTaskPublisherTests(unittest.TestCase):
    @patch("src.publishers.feishu_tasks.subprocess.run")
    def test_creates_review_task_with_owner_due_reminder_and_link(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "data": {
                    "guid": "task-guid-1",
                    "url": "https://applink.feishu.cn/task-guid-1",
                },
            }),
            stderr="",
        )
        record = {
            "id": "record-1",
            "file_name": "安全制度.pdf",
            "category": "制度与安全",
            "doc_type": "制度规范",
            "version": 2,
            "quality_score": 88,
            "review_priority": "P1",
            "next_review_at": "2026-09-15",
            "drive_url": "https://example.feishu.cn/file/token",
        }

        result = FeishuTaskPublisher(task_config()).create_review_task(record)

        self.assertEqual(result, {
            "task_guid": "task-guid-1",
            "task_url": "https://applink.feishu.cn/task-guid-1",
            "owner": "ou_review_owner",
            "reminder": "2026-09-14T00:00:00",
        })
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, "task", "+create"])
        self.assertEqual(flag_value(command, "--as"), "user")
        self.assertEqual(flag_value(command, "--due"), "2026-09-15")
        self.assertEqual(
            flag_value(command, "--assignee"), "ou_review_owner"
        )
        self.assertEqual(
            flag_value(command, "--tasklist-id"), "review-list-guid"
        )
        self.assertIn("安全制度.pdf", flag_value(command, "--summary"))
        self.assertIn(
            record["drive_url"], flag_value(command, "--description")
        )
        self.assertEqual(
            json.loads(flag_value(command, "--data")),
            {"reminders": [{"relative_fire_minute": 1440}]},
        )
        self.assertEqual(
            len(flag_value(command, "--idempotency-key")), 36
        )
        self.assertEqual(flag_value(command, "--format"), "json")
        self.assertFalse(run.call_args.kwargs["check"])
        self.assertTrue(run.call_args.kwargs["text"])

    @patch("src.publishers.feishu_tasks.subprocess.run")
    def test_repeated_record_is_created_once(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({
                "data": {
                    "task": {
                        "guid": "task-guid-2",
                        "url": "https://applink.feishu.cn/task-guid-2",
                    },
                },
            }),
            stderr="",
        )
        record = {
            "id": "record-2",
            "file_name": "运营手册.docx",
            "version": 1,
            "next_review_at": "2026-10-01",
        }
        publisher = FeishuTaskPublisher(task_config())

        results = publisher.publish_due_reviews([record, dict(record)])

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["task_guid"], "task-guid-2")
        run.assert_called_once()

    @patch("src.publishers.feishu_tasks.subprocess.run")
    def test_existing_task_is_reused_without_cli_call(self, run):
        record = SimpleNamespace(
            id="record-3",
            file_name="已有复核任务.md",
            next_review_at="2026-09-20",
            review_owner="ou_existing_owner",
            review_task_guid="existing-guid",
            review_task_url="https://applink.feishu.cn/existing-guid",
            review_reminder_at="2026-09-19T09:00:00",
        )

        result = FeishuTaskPublisher(task_config()).create_task(record)

        self.assertEqual(result, {
            "task_guid": "existing-guid",
            "task_url": "https://applink.feishu.cn/existing-guid",
            "owner": "ou_existing_owner",
            "reminder": "2026-09-19T09:00:00",
        })
        run.assert_not_called()

    @patch("src.publishers.feishu_tasks.subprocess.run")
    def test_disabled_config_is_a_no_op(self, run):
        publisher = FeishuTaskPublisher(task_config(enabled=False))

        result = publisher.create_review_task({
            "id": "record-4",
            "file_name": "本地记录.txt",
            "next_review_at": "2026-09-30",
        })

        self.assertFalse(publisher.available)
        self.assertEqual(result["task_guid"], "")
        self.assertEqual(result["task_url"], "")
        self.assertEqual(result["owner"], "ou_review_owner")
        self.assertEqual(result["reminder"], "2026-09-29T00:00:00")
        run.assert_not_called()

    @patch("src.publishers.feishu_tasks.subprocess.run")
    def test_local_only_provider_is_a_no_op(self, run):
        publisher = FeishuTaskPublisher(task_config(provider="none"))

        results = publisher.create_due_review_tasks([{
            "id": "record-5",
            "file_name": "仅本地.md",
            "next_review_at": "2026-09-30",
        }])

        self.assertFalse(publisher.available)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["task_guid"], "")
        run.assert_not_called()

    @patch("src.publishers.feishu_tasks.subprocess.run")
    def test_cli_failure_is_reported(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=3,
            stdout="",
            stderr="missing required scope: task:task:write",
        )
        publisher = FeishuTaskPublisher(task_config())

        with self.assertRaisesRegex(RuntimeError, "task:task:write"):
            publisher.create_review_task({
                "id": "record-6",
                "file_name": "待授权.md",
                "next_review_at": "2026-09-30",
            })


if __name__ == "__main__":
    unittest.main()
