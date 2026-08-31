import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from ..governance.sensitivity import SensitivityScanner
from ..models.file_record import FileRecord


class FeishuDocxPublishError(RuntimeError):
    """Raised when remote document creation fails without a local fallback."""


class FeishuDocxPublisher:
    """Create a governed Feishu knowledge page and verify it by reading it back."""

    REQUIRED_SECTIONS = ("治理概览", "结构化正文", "来源", "例外", "变更记录")

    def __init__(self, config: dict, runner: Optional[Callable] = None):
        self.config = config
        feishu_cfg = config.get("feishu", {})
        wiki_cfg = feishu_cfg.get("wiki", {})
        pages_cfg = config.get("knowledge_pages", {})
        docx_cfg = feishu_cfg.get("docx", {})
        self.provider = str(feishu_cfg.get("provider", "cli")).lower()

        self.enabled = bool(docx_cfg.get(
            "enabled",
            pages_cfg.get("enabled", False),
        ))
        self.dry_run = bool(docx_cfg.get(
            "dry_run",
            pages_cfg.get("dry_run", False),
        ))
        self.local_only = bool(
            docx_cfg.get(
                "local_only",
                pages_cfg.get(
                    "local_only",
                    config.get("local_only")
                    or feishu_cfg.get("local_only")
                    or self.provider in {
                        "none", "local", "local-only", "local_only",
                    },
                ),
            )
        )
        self.fallback_local = bool(docx_cfg.get(
            "fallback_local",
            pages_cfg.get("fallback_local", True),
        ))
        self.fallback_on_error = bool(docx_cfg.get(
            "fallback_on_error",
            pages_cfg.get("fallback_on_error", False),
        ))
        self.verify_after_create = bool(docx_cfg.get(
            "verify_after_create",
            pages_cfg.get("verify_after_create", True),
        ))
        self.parent_token = str(
            docx_cfg.get("parent_token")
            or wiki_cfg.get("parent_node_token")
            or ""
        )
        self.parent_position = str(docx_cfg.get("parent_position", ""))
        if (
            not self.parent_token
            and not self.parent_position
            and wiki_cfg.get("space_id") == "my_library"
        ):
            self.parent_position = "my_library"
        self.local_output_dir = str(
            docx_cfg.get("local_output_dir")
            or pages_cfg.get("local_output_dir")
            or ""
        )
        doc_base_url = str(docx_cfg.get("base_url", "")).rstrip("/")
        wiki_base_url = str(wiki_cfg.get("base_url", "")).rstrip("/")
        self.doc_base_url = doc_base_url or (
            f"{wiki_base_url}/docx" if wiki_base_url else ""
        )
        self.max_body_chars = max(
            1000,
            int(docx_cfg.get(
                "max_body_chars",
                pages_cfg.get("max_body_chars", 20000),
            )),
        )
        self.timeout = max(
            1,
            int(docx_cfg.get("timeout", pages_cfg.get("timeout", 120))),
        )
        self.cli_path = self._resolve_cli(feishu_cfg.get("cli_path", ""))
        self._runner = runner or subprocess.run
        self._scanner = SensitivityScanner()

    @staticmethod
    def _resolve_cli(cli_path: str) -> str:
        if cli_path and Path(cli_path).is_file():
            return str(cli_path)
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
            and self.cli_path
            and not self.local_only
            and self.provider in {"cli", "lark-cli"}
        )

    def build_page(
        self,
        record: FileRecord,
        exceptions: Optional[Sequence] = None,
        changes: Optional[Sequence] = None,
    ) -> dict:
        """Build the safe, canonical Markdown page without remote I/O."""
        exception_values = self._as_list(exceptions)
        change_values = self._as_list(changes)
        candidate_values = [
            record.file_name,
            record.summary,
            record.text_content,
            record.source_session,
            record.review_note,
            record.review_conclusion,
            exception_values,
            change_values,
        ]
        detected = self._detect_sensitive(candidate_values)
        blocked_sensitive = (
            str(record.sensitivity_level).lower() == "high"
            or any(item["level"] == "high" for item in detected)
            or any(
                str(item.get("level", "")).lower() == "high"
                for item in (record.sensitivity_findings or [])
                if isinstance(item, Mapping)
            )
        )

        title = self._build_title(record, blocked_sensitive)
        fingerprint = self._fingerprint(record, title)
        body = self._build_body(record, blocked_sensitive)
        exception_items = self._build_exceptions(
            record,
            detected,
            blocked_sensitive,
            exception_values,
        )
        change_rows = self._build_change_rows(record, change_values)

        governance_rows = [
            ("文档名称", record.file_name or "未命名"),
            ("治理状态", "高敏阻断" if blocked_sensitive else "可发布"),
            ("领域", record.domain or "未识别"),
            ("文档类型", record.doc_type or record.file_type or "未识别"),
            (
                "分类路径",
                " / ".join(
                    part for part in (record.category, record.sub_category) if part
                ) or "待分类",
            ),
            ("标签", "、".join(record.tags) or "无"),
            ("版本", f"v{record.version}"),
            ("密级", record.security_level or "L2-Internal"),
            ("质量评分", str(record.quality_score)),
            ("生产就绪", "是" if record.production_ready else "否"),
            ("复核优先级", record.review_priority or "无"),
            ("下次复核", record.next_review_at or "未安排"),
        ]
        governance_table = self._table(
            ("治理字段", "值"),
            governance_rows,
        )

        source_lines = [
            f"- 原始文件：{self._inline(record.file_name or '未命名')}",
            f"- 来源类型：{self._inline(record.source or '未知')}",
            f"- 来源会话：{self._inline(record.source_session or '未记录')}",
            f"- 采集时间：{self._inline(record.captured_at or '未记录')}",
        ]
        source_url = self._safe_source_url(record.drive_url)
        if source_url:
            source_lines.append(f"- 来源附件：[查看内部附件]({source_url})")

        summary = (
            "高敏原文已阻断，未生成可远端发布的正文。"
            if blocked_sensitive
            else self._block(record.summary or "暂无摘要。")
        )
        content = "\n".join([
            "## 治理概览",
            "",
            governance_table,
            "",
            "## 结构化正文",
            "",
            "### 核心摘要",
            "",
            summary,
            "",
            "### 正文",
            "",
            body,
            "",
            "## 来源",
            "",
            *source_lines,
            "",
            "## 例外",
            "",
            *[f"- {self._block(item)}" for item in exception_items],
            "",
            "## 变更记录",
            "",
            self._table(
                ("版本", "时间", "变更", "来源修订"),
                change_rows,
            ),
            "",
            "---",
            "",
            f"FG-DOCX-ID: `{fingerprint}`",
        ]).strip() + "\n"

        return {
            "title": title,
            "content": content,
            "fingerprint": fingerprint,
            "blocked_sensitive": blocked_sensitive,
            "detected_sensitive_types": [
                {"type": item["type"], "level": item["level"], "count": item["count"]}
                for item in detected
            ],
        }

    def publish_record(
        self,
        record: FileRecord,
        *,
        dry_run: Optional[bool] = None,
        local_only: Optional[bool] = None,
        exceptions: Optional[Sequence] = None,
        changes: Optional[Sequence] = None,
    ) -> dict:
        page = self.build_page(record, exceptions=exceptions, changes=changes)
        if page["blocked_sensitive"]:
            return self._finish_without_remote(
                record,
                page,
                status="blocked_sensitive",
                mode="blocked",
                warning="检测到高敏信息，已阻断远端知识页创建。",
                save_local=False,
            )

        use_dry_run = self.dry_run if dry_run is None else bool(dry_run)
        use_local_only = self.local_only if local_only is None else bool(local_only)
        if use_dry_run:
            return self._finish_without_remote(
                record,
                page,
                status="dry_run",
                mode="dry",
                warning="dry 模式未调用飞书。",
                save_local=False,
            )

        if use_local_only or not self.available:
            if not use_local_only and not self.fallback_local:
                raise FeishuDocxPublishError("lark-cli 不可用，且未启用本地降级")
            return self._finish_without_remote(
                record,
                page,
                status="local_only",
                mode="local",
                warning="飞书发布不可用，已降级为本地安全稿。",
                save_local=True,
            )

        action = getattr(record, "publication_action", "create") or "create"
        if action == "update" and not record.doc_token:
            return self._finish_without_remote(
                record,
                page,
                status="blocked_missing_document",
                mode="blocked",
                warning="更新动作缺少已有 doc_token，已阻断以避免重复创建。",
                save_local=False,
            )

        try:
            if record.doc_token and action in {"create", "update"}:
                created = self._update_remote(
                    record.doc_token,
                    record.doc_url,
                    page["content"],
                )
            else:
                parent_token = (
                    getattr(record, "target_node_token", "")
                    or self.parent_token
                )
                created = self._create_remote(
                    page["title"],
                    page["content"],
                    parent_token=parent_token,
                )
        except FeishuDocxPublishError as exc:
            if not self.fallback_on_error:
                self._set_record_state(record, "failed", False)
                raise
            return self._finish_without_remote(
                record,
                page,
                status="local_only_error",
                mode="local",
                warning=f"飞书创建失败，已降级为本地安全稿：{self._safe_error(exc)}",
                save_local=True,
            )

        document_id = created["document_id"]
        document_url = created["document_url"]
        verification = {
            "ok": False,
            "skipped": not self.verify_after_create,
            "fingerprint_found": False,
            "missing_sections": list(self.REQUIRED_SECTIONS),
        }
        if self.verify_after_create:
            try:
                verification = self.verify_readback(
                    document_id,
                    page["fingerprint"],
                )
            except FeishuDocxPublishError as exc:
                verification = {
                    "ok": False,
                    "skipped": False,
                    "fingerprint_found": False,
                    "missing_sections": list(self.REQUIRED_SECTIONS),
                    "error": self._safe_error(exc),
                }

        verified = bool(verification.get("ok"))
        status = (
            "verified"
            if verified
            else ("created" if not self.verify_after_create else "created_unverified")
        )
        self._set_record_state(
            record,
            status,
            verified,
            document_id=document_id,
            document_url=document_url,
        )
        warnings = created.get("warnings", [])
        if self.verify_after_create and not verified:
            warnings.append("知识页已创建，但回读校验未通过。")
        return {
            **page,
            "status": status,
            "mode": "remote",
            "created": True,
            "document_id": document_id,
            "document_url": document_url,
            "readback_verified": verified,
            "verification": verification,
            "local_path": "",
            "warnings": warnings,
        }

    def publish(self, record: FileRecord, **kwargs) -> dict:
        """Alias matching publisher-style callers that use ``publish``."""
        return self.publish_record(record, **kwargs)

    def verify_readback(self, document_id: str, fingerprint: str) -> dict:
        response = self._run_cli([
            "docs",
            "+fetch",
            "--as",
            "user",
            "--doc",
            document_id,
            "--doc-format",
            "markdown",
            "--detail",
            "simple",
            "--scope",
            "full",
        ])
        content = str(self._find_value(response, "content") or "")
        returned_id = str(self._find_value(response, "document_id") or "")
        missing_sections = [
            section for section in self.REQUIRED_SECTIONS if section not in content
        ]
        fingerprint_found = bool(fingerprint and fingerprint in content)
        document_id_matches = not returned_id or returned_id == document_id
        return {
            "ok": (
                bool(content)
                and fingerprint_found
                and not missing_sections
                and document_id_matches
            ),
            "skipped": False,
            "fingerprint_found": fingerprint_found,
            "missing_sections": missing_sections,
            "document_id_matches": document_id_matches,
        }

    def _create_remote(
        self,
        title: str,
        content: str,
        *,
        parent_token: str = "",
    ) -> dict:
        args = [
            "docs",
            "+create",
            "--as",
            "user",
            "--doc-format",
            "markdown",
            "--title",
            title,
            "--content",
            "-",
        ]
        if parent_token:
            args.extend(["--parent-token", parent_token])
        elif self.parent_position:
            args.extend(["--parent-position", self.parent_position])
        response = self._run_cli(args, input_text=content)
        document_id = str(
            self._find_value(response, "document_id")
            or self._find_value(response, "documentId")
            or ""
        )
        if not document_id:
            raise FeishuDocxPublishError("飞书创建响应缺少 document_id")
        document_url = str(
            self._find_value(response, "url")
            or (
                f"{self.doc_base_url}/{document_id}"
                if self.doc_base_url
                else ""
            )
        )
        raw_warnings = self._find_value(response, "warnings") or []
        if not isinstance(raw_warnings, list):
            raw_warnings = [raw_warnings]
        warnings = [self._safe_error(item) for item in raw_warnings if item]
        return {
            "document_id": document_id,
            "document_url": document_url,
            "warnings": warnings,
        }

    def _update_remote(
        self,
        document_id: str,
        document_url: str,
        content: str,
    ) -> dict:
        self._run_cli([
            "docs",
            "+update",
            "--as",
            "user",
            "--doc",
            document_id,
            "--command",
            "overwrite",
            "--doc-format",
            "markdown",
            "--content",
            "-",
        ], input_text=content)
        return {
            "document_id": document_id,
            "document_url": document_url or (
                f"{self.doc_base_url}/{document_id}"
                if self.doc_base_url else ""
            ),
            "warnings": [],
        }

    def _run_cli(self, args: list[str], input_text: Optional[str] = None) -> dict:
        if not self.cli_path:
            raise FeishuDocxPublishError("lark-cli 不可用")
        completed = self._runner(
            [self.cli_path, *args],
            check=False,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout,
        )
        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            detail = completed.stderr or output or "lark-cli 调用失败"
            raise FeishuDocxPublishError(self._safe_error(detail))
        try:
            response = json.loads(output)
        except json.JSONDecodeError as exc:
            raise FeishuDocxPublishError("lark-cli 未返回有效 JSON") from exc
        if isinstance(response, Mapping) and response.get("ok") is False:
            detail = self._find_value(response, "message") or "飞书 API 返回失败"
            raise FeishuDocxPublishError(self._safe_error(detail))
        return response

    def _finish_without_remote(
        self,
        record: FileRecord,
        page: dict,
        *,
        status: str,
        mode: str,
        warning: str,
        save_local: bool,
    ) -> dict:
        local_path = self._write_local(record, page) if save_local else ""
        self._set_record_state(record, status, False)
        return {
            **page,
            "status": status,
            "mode": mode,
            "created": False,
            "document_id": "",
            "document_url": "",
            "readback_verified": False,
            "verification": {
                "ok": False,
                "skipped": True,
                "fingerprint_found": False,
                "missing_sections": [],
            },
            "local_path": local_path,
            "warnings": [warning],
        }

    def _write_local(self, record: FileRecord, page: dict) -> str:
        if not self.local_output_dir:
            return ""
        output_dir = Path(self.local_output_dir).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._plain(Path(record.file_name or "knowledge").stem)
        stem = re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            safe_name,
        ).strip("-._") or "knowledge"
        output_path = output_dir / f"{stem}-{page['fingerprint']}.md"
        output_path.write_text(
            f"# {self._block(page['title'])}\n\n{page['content']}",
            encoding="utf-8",
        )
        return str(output_path.resolve())

    def _build_title(self, record: FileRecord, blocked_sensitive: bool) -> str:
        if blocked_sensitive:
            suffix = (record.id or "blocked")[:8]
            return f"标准知识页（高敏阻断）-{suffix}"
        name = Path(record.file_name or "未命名材料").stem
        title = f"标准知识页｜{self._plain(name)}"
        return title[:120]

    def _build_body(self, record: FileRecord, blocked_sensitive: bool) -> str:
        if blocked_sensitive:
            return "高敏原文未写入知识页；需完成合规审核后重新生成。"
        body = self._block(record.text_content or "")
        if not body.strip():
            return "暂无可发布正文。"
        if len(body) <= self.max_body_chars:
            return body
        return (
            body[:self.max_body_chars].rstrip()
            + "\n\n[正文已按知识页长度策略截断]"
        )

    def _build_exceptions(
        self,
        record: FileRecord,
        detected: list[dict],
        blocked_sensitive: bool,
        extra: Sequence,
    ) -> list[str]:
        items = []
        if detected or record.sensitivity_findings:
            counts = {}
            for finding in detected:
                key = (finding["type"], finding["level"])
                counts[key] = counts.get(key, 0) + finding["count"]
            for finding in record.sensitivity_findings or []:
                if not isinstance(finding, Mapping):
                    continue
                key = (
                    str(finding.get("type", "unknown")),
                    str(finding.get("level", "unknown")),
                )
                try:
                    count = max(0, int(finding.get("count", 0)))
                except (TypeError, ValueError):
                    count = 0
                counts[key] = max(counts.get(key, 0), count)
            detail = "、".join(
                f"{kind}/{level} {count} 项"
                for (kind, level), count in sorted(counts.items())
            )
            items.append(f"敏感信息：{detail or '已识别'}；证据和原文均未写入。")
        if blocked_sensitive:
            items.append("发布门禁：高敏内容禁止自动发布。")
        if record.conflict_status:
            items.append(
                f"内容冲突：{record.conflict_status}，"
                f"关联 {len(record.conflict_details or [])} 条记录。"
            )
        if not record.production_ready:
            items.append("质量门禁：当前材料未达到 production_ready。")
        if record.review_conclusion and not record.production_ready:
            items.append(f"审核结论：{record.review_conclusion}")
        if record.review_note:
            items.append(f"复核备注：{record.review_note}")
        items.extend(self._stringify(item) for item in extra)
        return items or ["无已知例外。"]

    def _build_change_rows(
        self,
        record: FileRecord,
        changes: Sequence,
    ) -> list[tuple]:
        rows = []
        for item in changes:
            if isinstance(item, Mapping):
                rows.append((
                    item.get("version", f"v{record.version}"),
                    item.get("time") or item.get("at") or "未记录",
                    item.get("change") or item.get("description") or "内容更新",
                    item.get("source_revision") or "",
                ))
            else:
                rows.append((
                    f"v{record.version}",
                    record.modified_at or record.captured_at or "未记录",
                    self._stringify(item),
                    getattr(record, "source_revision", "") or "",
                ))
        if rows:
            return rows
        action = "版本更新" if record.is_new_version or record.version > 1 else "首次治理生成"
        return [(
            f"v{record.version}",
            record.modified_at or record.captured_at or "未记录",
            action,
            getattr(record, "source_revision", "") or (record.file_hash or "")[:12],
        )]

    def _detect_sensitive(self, values) -> list[dict]:
        counts = {}
        for text in self._flatten_strings(values):
            for kind, level, pattern in self._scanner.PATTERNS:
                count = sum(1 for _ in pattern.finditer(text))
                if count:
                    counts[(kind, level)] = counts.get((kind, level), 0) + count
        return [
            {"type": kind, "level": level, "count": count}
            for (kind, level), count in sorted(counts.items())
        ]

    def _flatten_strings(self, value):
        if value is None:
            return
        if isinstance(value, str):
            yield value
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                yield from self._flatten_strings(key)
                yield from self._flatten_strings(item)
            return
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            for item in value:
                yield from self._flatten_strings(item)
            return
        yield str(value)

    def _sanitize(self, value) -> str:
        text = self._scanner.redact(self._stringify(value))
        return "".join(
            char if char in "\n\t" or ord(char) >= 32 else " "
            for char in text
        )

    def _plain(self, value) -> str:
        return re.sub(r"\s+", " ", self._sanitize(value)).strip()

    def _inline(self, value) -> str:
        text = self._plain(value)
        text = self._escape_markdown(text)
        return text.replace("|", r"\|")

    def _block(self, value) -> str:
        lines = self._sanitize(value).splitlines() or [""]
        escaped = []
        for line in lines:
            line = self._escape_markdown(line)
            line = re.sub(r"^(\s*)([#>+-])", r"\1\\\2", line)
            escaped.append(line)
        return "\n".join(escaped)

    @staticmethod
    def _escape_markdown(text: str) -> str:
        for source, target in (
            ("\\", r"\\"),
            ("`", r"\`"),
            ("*", r"\*"),
            ("_", r"\_"),
            ("[", r"\["),
            ("]", r"\]"),
            ("$", r"\$"),
            ("~", r"\~"),
            ("<", r"\<"),
        ):
            text = text.replace(source, target)
        return text

    def _table(self, headers: tuple, rows: Sequence[Sequence]) -> str:
        header = "| " + " | ".join(self._inline(item) for item in headers) + " |"
        separator = "| " + " | ".join("---" for _ in headers) + " |"
        body = [
            "| " + " | ".join(self._inline(item) for item in row) + " |"
            for row in rows
        ]
        return "\n".join([header, separator, *body])

    def _safe_source_url(self, value: str) -> str:
        if not value:
            return ""
        try:
            parsed = urlsplit(value)
        except ValueError:
            return ""
        if parsed.scheme != "https" or not parsed.netloc or parsed.username:
            return ""
        safe_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if (
            self._scanner.redact(safe_url) != safe_url
            or re.search(r"[\s()<>'\"]", safe_url)
        ):
            return ""
        return safe_url

    def _safe_error(self, value) -> str:
        return self._plain(value)[:500]

    @staticmethod
    def _stringify(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (Mapping, list, tuple)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    @staticmethod
    def _as_list(value) -> list:
        if value is None:
            return []
        if isinstance(value, (str, bytes, bytearray, Mapping)):
            return [value]
        return list(value)

    @staticmethod
    def _fingerprint(record: FileRecord, title: str) -> str:
        stable = "|".join([
            record.id or "",
            record.file_hash or "",
            str(record.version),
            title,
        ])
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _set_record_state(
        record: FileRecord,
        status: str,
        verified: bool,
        *,
        document_id: str = "",
        document_url: str = "",
    ):
        if document_id and hasattr(record, "doc_token"):
            record.doc_token = document_id
        if document_url and hasattr(record, "doc_url"):
            record.doc_url = document_url
        if hasattr(record, "knowledge_page_status"):
            record.knowledge_page_status = status
        if hasattr(record, "readback_verified"):
            record.readback_verified = verified
        if hasattr(record, "log_step"):
            record.log_step(
                "feishu_docx",
                f"status={status}, readback_verified={verified}",
                success=status in {"verified", "created", "dry_run", "local_only"},
            )

    @classmethod
    def _find_value(cls, value, key: str):
        if isinstance(value, Mapping):
            if value.get(key) is not None:
                return value[key]
            for item in value.values():
                found = cls._find_value(item, key)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._find_value(item, key)
                if found is not None:
                    return found
        return None
