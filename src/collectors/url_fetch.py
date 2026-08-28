import os
import re
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Iterator

from .base import BaseCollector
from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


class UrlFetchCollector(BaseCollector):
    """文件地址自动爬取采集器。

    支持给定一组文件地址（http/https URL 或本地绝对路径），自动下载/读取
    到临时目录并进入治理流水线。用于把"散落各处、只有一个链接"的文件
    转化为可治理、可沉淀的统一上下文。
    """

    source_type = "url_fetch"

    def __init__(self, config: dict, db):
        super().__init__(config, db)
        # urls 可来自配置，也可运行时注入（CLI fetch 命令）
        self.urls = config.get("urls", []) or []
        download_dir = config.get("download_dir", "")
        if download_dir:
            self.download_dir = Path(download_dir).expanduser()
        else:
            self.download_dir = Path(tempfile.gettempdir()) / "file-governance-fetch"

    def add_url(self, url: str):
        if url and url not in self.urls:
            self.urls.append(url)

    def scan(self) -> Iterator[FileRecord]:
        if not self.urls:
            return
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"下载目录不可用: {self.download_dir} ({e})")
            return

        for url in self.urls:
            local_path = self._fetch_one(url)
            if not local_path:
                continue
            resolved = str(Path(local_path).resolve())
            if self.db.is_path_processed(resolved):
                logger.info(f"已处理过，跳过: {url}")
                continue
            try:
                record = FileRecord.from_path(
                    resolved, source="url_fetch", source_session=self._short_url(url)
                )
                record.compute_hash()
                yield record
            except Exception as e:
                logger.debug(f"构建记录失败 {url}: {e}")

    def _fetch_one(self, url: str) -> str:
        """下载/定位单个文件，返回本地路径；失败返回空串。"""
        # 本地路径：直接使用
        if not url.lower().startswith(("http://", "https://")):
            p = Path(url).expanduser()
            if p.is_file():
                return str(p)
            logger.warning(f"本地文件不存在: {url}")
            return ""
        # 远程 URL：下载
        try:
            filename = self._infer_filename(url)
            dest = self.download_dir / filename
            # 同名冲突则加序号
            counter = 1
            while dest.exists():
                stem, suf = dest.stem, dest.suffix
                dest = self.download_dir / f"{stem}_{counter}{suf}"
                counter += 1
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (file-governance-bot)"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                # 尝试从响应头补全文件名/扩展名
                dest = self._refine_name_from_headers(resp, dest)
                data = resp.read(self.max_file_size_mb * 1024 * 1024 + 1)
            if len(data) > self.max_file_size_mb * 1024 * 1024:
                logger.warning(f"文件超过大小上限，跳过: {url}")
                return ""
            with open(dest, "wb") as f:
                f.write(data)
            logger.info(f"已爬取: {url} -> {dest}")
            return str(dest)
        except Exception as e:
            logger.warning(f"爬取失败 {url}: {e}")
            return ""

    def _infer_filename(self, url: str) -> str:
        path = urllib.parse.urlparse(url).path
        name = os.path.basename(urllib.parse.unquote(path)) or "downloaded_file"
        # 去除非法字符
        name = re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "downloaded_file"
        return name

    def _refine_name_from_headers(self, resp, dest: Path) -> Path:
        # Content-Disposition 里的 filename 优先
        cd = resp.headers.get("Content-Disposition", "") if hasattr(resp, "headers") else ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.IGNORECASE)
        if m:
            fn = re.sub(r'[<>:"/\\|?*]', "_", urllib.parse.unquote(m.group(1))).strip()
            if fn:
                return dest.with_name(fn)
        # 无扩展名时按 Content-Type 补
        if not dest.suffix:
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip() if hasattr(resp, "headers") else ""
            ext = {
                "application/pdf": ".pdf",
                "text/plain": ".txt",
                "text/markdown": ".md",
                "text/html": ".html",
                "text/csv": ".csv",
                "application/json": ".json",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                "application/zip": ".zip",
                "image/png": ".png",
                "image/jpeg": ".jpg",
            }.get(ctype, "")
            if ext:
                return dest.with_suffix(ext)
        return dest

    def _short_url(self, url: str) -> str:
        if url.lower().startswith(("http://", "https://")):
            parsed = urllib.parse.urlparse(url)
            return f"URL:{parsed.netloc}"
        return "本地路径"
