import json
import shutil
import subprocess
from pathlib import Path

from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()

L2_INTERNAL_LABEL_ID = "7439288234140483587"


class PermissionGovernor:
    def __init__(self, config: dict, cli_path: str = ""):
        self.config = config
        self.default_level = config.get("governance", {}).get("default_security_level", "L2-Internal")
        self.default_share = config.get("governance", {}).get("default_share_permission", "tenant_readable")
        self.cli_path = self._resolve_cli(cli_path)
        self.enabled = bool(self.cli_path)

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
            record.log_step("permission", f"密级={record.security_level}, 共享={record.share_permission}")
        except Exception as e:
            logger.debug(f"权限治理失败 {record.file_name}: {e}")
            record.log_step("permission", f"设置失败: {e}", success=False)
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
        subprocess.run(
            [self.cli_path, "drive", "+secure-label-update", "--as", "user",
             "--token", file_token, "--label-id", L2_INTERNAL_LABEL_ID],
            capture_output=True, text=True, timeout=30, check=False
        )

    def _set_tenant_readable(self, file_token: str):
        if not self.cli_path:
            return
        result = subprocess.run(
            [self.cli_path, "drive", "+permission-public-set", "--as", "user",
             "--token", file_token, "--link-share-entity", "tenant_readable",
             "--comment", "false", "--copy", "false", "--read", "true",
             "--save", "false", "--share", "false"],
            capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            alt_result = subprocess.run(
                [self.cli_path, "drive", "permissions", "public", "set", "--as", "user",
                 "--token", file_token, "--type", "tenant_readable"],
                capture_output=True, text=True, timeout=30, check=False
            )
            if alt_result.returncode != 0:
                logger.debug(f"tenant_readable 设置响应: {result.stderr} | alt: {alt_result.stderr}")
