import json
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from ..governance.sensitivity import SensitivityScanner


class FeishuTaskPublisher:
    """Create Feishu review tasks through the authenticated lark-cli."""

    DEFAULT_REMINDER_MINUTES = 24 * 60

    def __init__(self, config: dict):
        feishu_cfg = config.get("feishu", {})
        governance_cfg = config.get("governance", {})
        task_cfg = (
            feishu_cfg.get("tasks")
            or feishu_cfg.get("review_tasks")
            or governance_cfg.get("review_tasks")
            or config.get("review_tasks")
            or {}
        )

        self.enabled = bool(task_cfg.get("enabled", False))
        self.provider = str(feishu_cfg.get("provider", "cli")).lower()
        self.local_only = bool(
            config.get("local_only")
            or feishu_cfg.get("local_only")
            or task_cfg.get("local_only")
            or self.provider in {"none", "local", "local-only", "local_only"}
        )
        self.owner = str(
            task_cfg.get("owner")
            or task_cfg.get("default_owner")
            or task_cfg.get("owner_open_id")
            or task_cfg.get("assignee")
            or ""
        )
        self.tasklist_id = str(
            task_cfg.get("tasklist_id")
            or task_cfg.get("tasklist_guid")
            or ""
        )
        self.title_prefix = str(task_cfg.get("title_prefix", "知识复核："))
        reminder_value = task_cfg.get(
            "reminder_minutes_before",
            task_cfg.get("reminder_minutes", task_cfg.get(
                "reminder", self.DEFAULT_REMINDER_MINUTES
            )),
        )
        self.reminder_minutes = self._parse_reminder_minutes(reminder_value)
        self.cli_path = self._resolve_cli(feishu_cfg.get("cli_path", ""))
        self._scanner = SensitivityScanner()
        self._created: dict[str, dict[str, str]] = {}

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
            return str(sorted(
                candidates,
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[0])
        return ""

    @property
    def available(self) -> bool:
        return bool(
            self.enabled
            and not self.local_only
            and self.provider == "cli"
            and self.cli_path
        )

    def publish_due_reviews(self, records: Iterable[Any]) -> list[dict[str, str]]:
        return [self.create_review_task(record) for record in records]

    def create_review_task(self, record: Any) -> dict[str, str]:
        owner = str(self._value(record, "review_owner") or self.owner)
        due = str(self._value(record, "next_review_at") or "")
        existing_reminder = str(
            self._value(record, "review_reminder_at") or ""
        )
        reminder = existing_reminder or self._reminder_at(
            due, self.reminder_minutes
        )

        existing_guid = str(
            self._value(record, "review_task_guid")
            or self._value(record, "task_guid")
            or ""
        )
        existing_url = str(
            self._value(record, "review_task_url")
            or self._value(record, "task_url")
            or ""
        )
        if existing_guid:
            return self._result(
                existing_guid,
                existing_url or self._task_url(existing_guid),
                owner,
                reminder,
            )

        idempotency_key = self._idempotency_key(record)
        if idempotency_key in self._created:
            return dict(self._created[idempotency_key])

        no_op_result = self._result("", "", owner, reminder)
        if not self.available:
            return no_op_result

        args = [
            "task", "+create",
            "--as", "user",
            "--summary", self._summary(record),
            "--description", self._description(record),
            "--idempotency-key", idempotency_key,
        ]
        if due:
            args.extend(["--due", due])
        if owner:
            args.extend(["--assignee", owner])
        if self.tasklist_id:
            args.extend(["--tasklist-id", self.tasklist_id])
        if due and self.reminder_minutes is not None:
            args.extend([
                "--data",
                json.dumps({
                    "reminders": [{
                        "relative_fire_minute": self.reminder_minutes,
                    }],
                }, ensure_ascii=False, separators=(",", ":")),
            ])

        response = self._run(args)
        task_guid = str(
            self._find_value(response, "guid")
            or self._find_value(response, "task_guid")
            or ""
        )
        if not task_guid:
            raise RuntimeError("飞书任务创建成功响应缺少 task guid")
        task_url = str(
            self._find_value(response, "url")
            or self._find_value(response, "task_url")
            or self._task_url(task_guid)
        )
        result = self._result(task_guid, task_url, owner, reminder)
        self._created[idempotency_key] = result
        return dict(result)

    def create_task(self, record: Any) -> dict[str, str]:
        return self.create_review_task(record)

    def create_due_review_tasks(
        self, records: Iterable[Any]
    ) -> list[dict[str, str]]:
        return self.publish_due_reviews(records)

    def _run(self, args: list[str]) -> dict:
        if not self.cli_path:
            raise RuntimeError("lark-cli 不可用")
        if "--format" not in args:
            args = [*args, "--format", "json"]
        completed = subprocess.run(
            [self.cli_path, *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            raise RuntimeError(
                completed.stderr.strip() or output or "lark-cli 调用失败"
            )
        try:
            response = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError("lark-cli 未返回有效 JSON") from error
        if response.get("ok") is False:
            message = self._find_value(response.get("error", {}), "message")
            raise RuntimeError(str(message or "飞书任务创建失败"))
        if response.get("code") not in (None, 0):
            raise RuntimeError(str(
                response.get("msg") or response.get("message")
                or "飞书任务创建失败"
            ))
        return response

    def _summary(self, record: Any) -> str:
        file_name = self._scanner.redact(
            str(self._value(record, "file_name") or "未命名材料")
        )
        return f"{self.title_prefix}{file_name}"[:3000]

    def _description(self, record: Any) -> str:
        fields = [
            ("文件", self._scanner.redact(str(self._value(record, "file_name") or ""))),
            ("分类", self._scanner.redact(str(self._value(record, "category") or ""))),
            ("文档类型", self._scanner.redact(str(self._value(record, "doc_type") or ""))),
            ("版本", self._value(record, "version")),
            ("质量评分", self._value(record, "quality_score")),
            ("复核优先级", self._value(record, "review_priority")),
            ("原复核日期", self._value(record, "next_review_at")),
            (
                "材料链接",
                self._value(record, "doc_url")
                or self._value(record, "drive_url"),
            ),
        ]
        lines = ["请复核以下知识材料，并在完成后更新审核结论。"]
        lines.extend(
            f"{label}：{value}"
            for label, value in fields
            if value not in (None, "")
        )
        return "\n".join(lines)[:3000]

    def _idempotency_key(self, record: Any) -> str:
        identity = "|".join(str(value or "") for value in (
            self._value(record, "id"),
            self._value(record, "file_hash"),
            self._value(record, "file_name"),
            self._value(record, "version"),
            self._value(record, "next_review_at"),
        ))
        return str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"file-governance:review:{identity}",
        ))

    @staticmethod
    def _parse_reminder_minutes(value: Any):
        if value is None or value is False or value == "":
            return None
        if isinstance(value, bool):
            return 0 if value else None
        if isinstance(value, (int, float)):
            minutes = int(value)
        else:
            raw = str(value).strip().lower()
            multipliers = {"m": 1, "h": 60, "d": 24 * 60}
            if raw[-1:] in multipliers:
                minutes = int(float(raw[:-1]) * multipliers[raw[-1]])
            else:
                minutes = int(raw)
        if minutes < 0:
            raise ValueError("reminder minutes must be non-negative")
        return minutes

    @staticmethod
    def _reminder_at(due: str, reminder_minutes) -> str:
        if not due or reminder_minutes is None:
            return ""
        normalized = due.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            if len(normalized) == 10:
                due_at = datetime.fromisoformat(f"{normalized}T00:00:00")
            else:
                due_at = datetime.fromisoformat(normalized)
        except ValueError:
            return ""
        reminder_at = due_at - timedelta(minutes=reminder_minutes)
        return reminder_at.isoformat(timespec="seconds")

    @staticmethod
    def _value(record: Any, key: str):
        if isinstance(record, Mapping):
            return record.get(key)
        getter = getattr(record, "get", None)
        if callable(getter):
            return getter(key)
        return getattr(record, key, None)

    @classmethod
    def _find_value(cls, value: Any, key: str):
        if isinstance(value, dict):
            if value.get(key) is not None:
                return value[key]
            for child in value.values():
                found = cls._find_value(child, key)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_value(child, key)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _task_url(task_guid: str) -> str:
        return (
            "https://applink.feishu.cn/client/todo/detail?guid="
            f"{quote(task_guid, safe='')}"
        )

    @staticmethod
    def _result(
        task_guid: str,
        task_url: str,
        owner: str,
        reminder: str,
    ) -> dict[str, str]:
        return {
            "task_guid": task_guid,
            "task_url": task_url,
            "owner": owner,
            "reminder": reminder,
        }
