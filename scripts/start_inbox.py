#!/usr/bin/env python3
"""本地收件箱 Web UI：拖拽文件即可自动归档到飞书。"""
import sys
import os
import json
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import cgi
import urllib.parse

SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR))

from src.utils.config import load_config
from src.pipeline import GovernancePipeline
from src.utils.logger import setup_logger

logger = setup_logger()


class InboxHandler(BaseHTTPRequestHandler):
    config = None
    inbox_path = None

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/api/stats":
            self._serve_stats()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/upload":
            self._handle_upload()
        else:
            self.send_error(404)

    def _serve_html(self):
        template = SKILL_DIR / "templates" / "inbox.html"
        try:
            content = template.read_text(encoding="utf-8")
        except Exception:
            content = "<html><body><h1>Inbox</h1><p>Template not found</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _serve_stats(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            from src.utils.db import GovernanceDB
            cfg = load_config()
            db = GovernanceDB(cfg.get("db", {}).get("path", "./data/governance.db"))
            stats = db.get_stats()
            db.close()
            self.wfile.write(json.dumps(stats, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

    def _handle_upload(self):
        ctype, pdict = cgi.parse_header(self.headers.get("Content-Type", ""))
        if ctype == "multipart/form-data":
            pdict["boundary"] = pdict.get("boundary", "").encode("utf-8")
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type", "")},
            )
            saved = []
            for field in form.keys():
                field_item = form[field]
                if hasattr(field_item, "filename") and field_item.filename:
                    fn = Path(field_item.filename).name
                    dest = Path(self.inbox_path) / fn
                    counter = 1
                    while dest.exists():
                        stem = Path(fn).stem
                        suf = Path(fn).suffix
                        dest = Path(self.inbox_path) / f"{stem}_{counter}{suf}"
                        counter += 1
                    with open(dest, "wb") as f:
                        shutil_copyfileobj(field_item.file, f)
                    saved.append(str(dest))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            result = {"saved": saved, "count": len(saved)}
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            logger.info(f"收件箱收到 {len(saved)} 个文件: {saved}")
            try:
                pipeline = GovernancePipeline(self.config)
                pipeline.run(source="inbox")
                pipeline.close()
            except Exception as e:
                logger.error(f"自动处理失败: {e}")
        else:
            self.send_error(400)

    def log_message(self, format, *args):
        logger.info(f"[inbox] {args[0]}")


import shutil
def shutil_copyfileobj(fsrc, fdst, length=16*1024):
    while True:
        buf = fsrc.read(length)
        if not buf:
            break
        fdst.write(buf)


def main():
    config = load_config()
    inbox_cfg = config.get("sources", {}).get("inbox", {})
    host = inbox_cfg.get("web_ui", {}).get("host", "127.0.0.1")
    port = int(inbox_cfg.get("web_ui", {}).get("port", 8765))
    inbox_path = Path(inbox_cfg.get("path", "./inbox")).expanduser()
    inbox_path.mkdir(parents=True, exist_ok=True)
    InboxHandler.config = config
    InboxHandler.inbox_path = str(inbox_path)
    print(f"📥 文件治理收件箱已启动: http://{host}:{port}")
    print(f"📁 收件目录: {inbox_path}")
    print("拖拽文件到浏览器页面即可自动归档到飞书")
    server = HTTPServer((host, port), InboxHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n收件箱已停止")
        server.server_close()


if __name__ == "__main__":
    main()
