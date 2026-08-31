import json
import shutil
import subprocess
from pathlib import Path

from ..models.file_record import FileRecord
from ..utils.logger import setup_logger
from .sensitivity import SensitivityScanner

logger = setup_logger()

L2_INTERNAL_LABEL_ID = "7439288234140483587"


class PermissionGovernor:
    def __init__(self, config: dict, cli_path: str = ""):
        self.config = config
        self.provider = str(
            config.get("feishu", {}).get("provider", "cli")
        ).lower()
        self.default_level = config.get("governance", {}).get("default_security_level", "L2-Internal")
        self.default_share = config.get("governance", {}).get("default_share_permission", "tenant_readable")
        self.cli_path = self._resolve_cli(cli_path)
        self.scanner = SensitivityScanner()
        self.enabled = bool(
            self.cli_path and self.provider in {"cli", "lark-cli"}
        )

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

    def process(self, record: FileRecord) -> FileRecord:
        if not self.enabled or not record.drive_url:
            record.log_step("permission", "跳过（CLI 不可用或未上传）", success=False)
            return record
        file_token = self._extract_token(record.drive_url)
        if not file_token:
            record.log_step("permission", "无法提取 token", success=False)
            return record
        try:
            if self.default_level == "L2-Internal":
                self._set_security_label(file_token)
                record.security_level = "L2-Internal"
            if self.default_share == "tenant_readable":
                self._set_tenant_readable(file_token)
                record.share_permission = "tenant_readable"
            record.permission_status = "verified"
            record.permission_issues = []
            record.log_step("permission", f"密级={record.security_level}, 共享={record.share_permission}")
        except Exception as e:
            safe_name = self.scanner.redact(record.file_name)
            safe_error = self.scanner.redact(str(e))
            logger.debug(f"权限治理失败 {safe_name}: {safe_error}")
            record.permission_status = "blocked"
            record.permission_issues = [{
                "code": "permission_apply_failed",
                "severity": "blocker",
                "message": self.scanner.redact(str(e))[:500],
            }]
            record.log_step(
                "permission",
                f"设置失败: {safe_error}",
                success=False,
            )
        return record

    def preflight_target(self, record: FileRecord) -> FileRecord:
        """读取目标知识节点，记录可观测权限；不把可读误判为可写。"""
        target = record.target_node_token or record.target_space_id
        if not target:
            record.permission_status = "not_configured"
            record.permission_issues = [{
                "code": "target_missing",
                "message": "未配置目标知识空间或父节点",
            }]
            record.log_step("permission_preflight", "未配置目标知识节点", success=False)
            return record
        if not self.enabled:
            record.permission_status = "unknown"
            record.permission_issues = [{
                "code": "cli_unavailable",
                "message": "飞书 CLI 未启用，无法预检目标权限",
            }]
            record.log_step("permission_preflight", "CLI 不可用", success=False)
            return record

        if record.target_node_token:
            args = [
                self.cli_path, "wiki", "+node-get", "--as", "user",
                "--node-token", record.target_node_token, "--format", "json",
            ]
        else:
            args = [
                self.cli_path, "wiki", "+node-list", "--as", "user",
                "--space-id", record.target_space_id, "--page-size", "1",
                "--format", "json",
            ]
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode == 0:
            record.permission_status = "readable"
            record.permission_issues = [{
                "code": "write_unverified",
                "message": "目标可读；写权限需在实际发布时验证",
            }]
            record.log_step("permission_preflight", "目标可读，写权限待发布验证")
        else:
            detail = self.scanner.redact(
                (completed.stderr or completed.stdout or "目标不可访问").strip()
            )
            record.permission_status = "blocked"
            record.permission_issues = [{
                "code": "target_unreadable",
                "message": detail[:500],
            }]
            record.log_step("permission_preflight", "目标不可访问", success=False)
        return record

    def govern_existing(self, file_tokens: list[str]) -> dict:
        results = {"success": 0, "failed": 0, "details": []}
        for token in file_tokens:
            try:
                if self.default_level == "L2-Internal":
                    self._set_security_label(token)
                if self.default_share == "tenant_readable":
                    self._set_tenant_readable(token)
                results["success"] += 1
                results["details"].append({"token": token, "status": "ok"})
            except Exception as e:
                results["failed"] += 1
                results["details"].append({"token": token, "status": "error", "error": str(e)})
        return results

    def _extract_token(self, url: str) -> str:
        if "/file/" in url:
            return url.rsplit("/file/", 1)[-1].split("?")[0].rstrip("/")
        return ""

    def _set_security_label(self, file_token: str):
        if not self.cli_path:
            return
        result = subprocess.run(
            [self.cli_path, "drive", "+secure-label-update", "--as", "user",
             "--token", file_token, "--type", "file",
             "--label-id", L2_INTERNAL_LABEL_ID],
            capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip()
                or "设置飞书密级失败"
            )

    def _set_tenant_readable(self, file_token: str):
        if not self.cli_path:
            return
        result = subprocess.run(
            [
                self.cli_path, "drive", "permission.public", "patch",
                "--as", "user", "--token", file_token, "--type", "file",
                "--data", json.dumps({
                    "link_share_entity": "tenant_readable",
                    "external_access": False,
                    "invite_external": False,
                    "share_entity": "same_tenant",
                }, ensure_ascii=False),
                "--yes", "--format", "json",
            ],
            capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip()
                or "设置组织内可读失败"
            )
