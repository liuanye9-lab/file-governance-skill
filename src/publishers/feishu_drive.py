import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


class FeishuDrivePublisher:
    def __init__(self, config: dict):
        self.config = config
        drive_cfg = config.get("feishu", {}).get("drive", {})
        knowledge_cfg = config.get("knowledge", {})
        self.root_folder = drive_cfg.get("root_folder", "知识沉淀")
        self.project_name = knowledge_cfg.get("project_name", "默认项目")
        self.cli_path = self._resolve_cli(config.get("feishu", {}).get("cli_path", ""))
        self._folder_cache = {}
        self._root_token = ""

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
        raise FileNotFoundError("未找到 lark-cli，请先安装并登录飞书 CLI（npm i -g @larksuite/cli）")

    @property
    def available(self) -> bool:
        return bool(self.cli_path)

    def upload(self, record: FileRecord) -> str:
        root = self._get_project_root()
        category_folder = self._get_or_create_folder(
            record.category or "未分类", root
        )
        with tempfile.TemporaryDirectory(prefix="fg-") as staging_dir:
            staged = Path(staging_dir) / record.file_name
            shutil.copy2(record.source_path, staged)
            result = self._run([
                "drive", "+upload", "--file", f"./{record.file_name}",
                "--name", record.file_name, "--folder-token", category_folder,
                "--as", "user",
            ], cwd=staging_dir)
        token = self._find_value(result, "file_token") or self._find_value(result, "token")
        if not token:
            raise RuntimeError("飞书上传未返回 file_token")
        url = f"https://bytedance.larkoffice.com/file/{token}"
        record.drive_url = url
        record.log_step("upload", f"已上传 {url}")
        logger.info(f"已上传: {record.file_name} -> {url}")
        return url

    def find_existing_url(self, file_name: str) -> Optional[str]:
        try:
            result = self._run([
                "drive", "+search", "--as", "user", "--doc-types", "file",
                "--only-title", "--query", file_name[:30], "--page-size", "5", "--format", "json"
            ])
            for item in result.get("data", {}).get("results", []):
                meta = item.get("result_meta", {})
                url = meta.get("url", "")
                if url and "/file/" in url:
                    return url
        except Exception as e:
            logger.debug(f"搜索已有文件失败: {e}")
        return None

    def _get_project_root(self) -> str:
        if self._root_token:
            return self._root_token
        path = [self.root_folder, self.project_name, "原始附件"]
        parent = ""
        for name in path:
            parent = self._get_or_create_folder(name, parent)
        self._root_token = parent
        return parent

    def _get_or_create_folder(self, name: str, parent_token: str = "") -> str:
        cache_key = f"{parent_token}:{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]
        args = ["drive", "files", "list", "--as", "user"]
        if parent_token:
            args.extend(["--folder-token", parent_token])
        try:
            existing = self._find_folder(self._run(args), name)
        except Exception:
            existing = ""
        if existing:
            self._folder_cache[cache_key] = existing
            return existing
        create_args = ["drive", "+create-folder", "--name", name, "--as", "user"]
        if parent_token:
            create_args.extend(["--folder-token", parent_token])
        created = self._run(create_args)
        token = self._find_value(created, "folder_token") or self._find_value(created, "token")
        if not token:
            raise RuntimeError(f"创建目录失败: {name}")
        self._folder_cache[cache_key] = token
        return token

    def _run(self, args: list[str], cwd: str = None) -> dict:
        completed = subprocess.run(
            [self.cli_path, *args], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, timeout=120,
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or output or "lark-cli 调用失败")
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"raw": output}

    @classmethod
    def _find_folder(cls, value, name: str) -> str:
        if isinstance(value, dict):
            if value.get("name") == name and value.get("type") == "folder":
                return value.get("token", "")
            for v in value.values():
                t = cls._find_folder(v, name)
                if t:
                    return t
        elif isinstance(value, list):
            for v in value:
                t = cls._find_folder(v, name)
                if t:
                    return t
        return ""

    @classmethod
    def _find_value(cls, value, key: str):
        if isinstance(value, dict):
            if value.get(key):
                return value[key]
            for v in value.values():
                f = cls._find_value(v, key)
                if f:
                    return f
        elif isinstance(value, list):
            for v in value:
                f = cls._find_value(v, key)
                if f:
                    return f
        return ""
