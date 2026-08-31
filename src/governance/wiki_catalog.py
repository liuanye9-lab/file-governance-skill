"""Read-only inventory of an explicitly configured Feishu Wiki subtree."""

from __future__ import annotations

import json
import shutil
import subprocess
import unicodedata
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


DEFAULT_MAX_NODES = 500
DEFAULT_MAX_DEPTH = 8
HARD_MAX_NODES = 5000
HARD_MAX_DEPTH = 32


class WikiCatalogError(RuntimeError):
    """Raised when the configured Wiki subtree cannot be read safely."""


@dataclass(frozen=True)
class WikiCatalogEntry:
    node_token: str
    obj_token: str
    obj_type: str
    path: str
    observed_permission: str
    title: str
    parent_node_token: str
    depth: int
    has_child: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WikiCatalog:
    """Build a bounded, deterministic ledger for one configured Wiki scope.

    The configured parent, or each space root node when no parent is configured,
    has depth zero. A successful read only proves readability; this module never
    infers write permission and never calls ``wiki +space-list``.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        cli_path: str = "",
        max_nodes: Optional[int] = None,
        max_depth: Optional[int] = None,
    ):
        self.config = dict(config or {})
        self.feishu_config, self.wiki_config = self._extract_config(self.config)

        self.enabled = _as_bool(self.wiki_config.get("enabled", False))
        provider = str(
            self.feishu_config.get(
                "provider",
                self.config.get("provider", "cli"),
            )
        ).strip().lower().replace("_", "-")
        mode = str(
            self.wiki_config.get("mode", self.config.get("mode", ""))
        ).strip().lower().replace("_", "-")
        self.local_only = (
            _as_bool(self.wiki_config.get("local_only", False))
            or _as_bool(self.config.get("local_only", False))
            or provider in {"none", "disabled", "local", "local-only"}
            or mode in {"disabled", "local", "local-only"}
        )

        self.space_id = str(self.wiki_config.get("space_id", "") or "").strip()
        self.parent_node_token = str(
            self.wiki_config.get("parent_node_token", "") or ""
        ).strip()
        self.max_nodes = _bounded_int(
            max_nodes if max_nodes is not None else self.wiki_config.get("max_nodes"),
            default=DEFAULT_MAX_NODES,
            minimum=1,
            maximum=HARD_MAX_NODES,
        )
        self.max_depth = _bounded_int(
            max_depth if max_depth is not None else self.wiki_config.get("max_depth"),
            default=DEFAULT_MAX_DEPTH,
            minimum=0,
            maximum=HARD_MAX_DEPTH,
        )
        self.timeout = _bounded_int(
            self.wiki_config.get("timeout"),
            default=30,
            minimum=1,
            maximum=300,
        )
        configured_cli = (
            cli_path
            or str(self.wiki_config.get("cli_path", "") or "")
            or str(self.feishu_config.get("cli_path", "") or "")
        )
        self.cli_path = self._resolve_cli(configured_cli)

        self.status = "not_loaded"
        self.error = ""
        self.truncated = False
        self.limit_reasons: list[str] = []
        self.resolved_space_id = self.space_id
        self._ledger: list[dict[str, Any]] = []

    @staticmethod
    def _extract_config(
        config: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        feishu_value = config.get("feishu", {})
        feishu = dict(feishu_value) if isinstance(feishu_value, Mapping) else {}
        nested_wiki = feishu.get("wiki")
        if isinstance(nested_wiki, Mapping):
            return feishu, dict(nested_wiki)

        top_level_wiki = config.get("wiki")
        if isinstance(top_level_wiki, Mapping):
            return feishu, dict(top_level_wiki)

        direct_keys = {
            "enabled",
            "space_id",
            "parent_node_token",
            "max_nodes",
            "max_depth",
            "timeout",
            "local_only",
            "mode",
            "cli_path",
        }
        if direct_keys.intersection(config):
            return feishu, {key: config[key] for key in direct_keys if key in config}
        return feishu, {}

    @staticmethod
    def _resolve_cli(cli_path: str) -> str:
        explicit = str(cli_path or "").strip()
        if explicit:
            return explicit
        executable = shutil.which("lark-cli")
        if executable:
            return executable
        candidates = list(
            (Path.home() / "Library/pnpm/store").glob(
                "**/node_modules/@larksuite/cli/bin/lark-cli"
            )
        )
        if not candidates:
            return ""
        return str(
            sorted(
                candidates,
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[0]
        )

    @property
    def ledger(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._ledger]

    def scan(self) -> list[dict[str, Any]]:
        """Read the configured scope and return a stable directory ledger."""
        self.error = ""
        self.truncated = False
        self.limit_reasons = []
        self.resolved_space_id = self.space_id
        self._ledger = []

        inactive_status = self._inactive_status()
        if inactive_status:
            self.status = inactive_status
            return []

        self.status = "loading"
        try:
            entries = self._crawl()
        except (OSError, ValueError, WikiCatalogError) as exc:
            self.status = "error"
            self.error = str(exc)
            raise WikiCatalogError(str(exc)) from exc

        entries.sort(key=_entry_sort_key)
        self._ledger = [entry.to_dict() for entry in entries]
        self.status = "ok"
        return self.ledger

    def build_catalog(self) -> list[dict[str, Any]]:
        """Compatibility name for callers that treat the ledger as a catalog."""
        return self.scan()

    def snapshot(self, raise_on_error: bool = False) -> dict[str, Any]:
        """Return ledger metadata without making disabled/local-only modes fail."""
        try:
            entries = self.scan()
        except WikiCatalogError:
            if raise_on_error:
                raise
            entries = []
        return {
            "status": self.status,
            "space_id": self.resolved_space_id,
            "parent_node_token": self.parent_node_token,
            "max_nodes": self.max_nodes,
            "max_depth": self.max_depth,
            "node_count": len(entries),
            "truncated": self.truncated,
            "limit_reasons": list(self.limit_reasons),
            "error": self.error,
            "entries": entries,
        }

    def resolve_target_node(
        self,
        category: str,
        subcategory: str = "",
        fallback_to_category: bool = True,
    ) -> Optional[dict[str, Any]]:
        """Map classification labels to the deepest matching existing node."""
        if self.status == "not_loaded":
            self.scan()
        return resolve_catalog_target(
            self._ledger,
            category,
            subcategory,
            parent_node_token=self.parent_node_token,
            fallback_to_category=fallback_to_category,
        )

    def map_target_node(
        self,
        category: str,
        subcategory: str = "",
        fallback_to_category: bool = True,
    ) -> Optional[dict[str, Any]]:
        return self.resolve_target_node(
            category,
            subcategory,
            fallback_to_category=fallback_to_category,
        )

    def target_node_token(
        self,
        category: str,
        subcategory: str = "",
        fallback_to_category: bool = True,
    ) -> str:
        target = self.resolve_target_node(
            category,
            subcategory,
            fallback_to_category=fallback_to_category,
        )
        return str(target.get("node_token", "")) if target else ""

    def _inactive_status(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.local_only:
            return "local-only"
        if not self.space_id and not self.parent_node_token:
            return "unconfigured"
        if not self.cli_path:
            return "cli_unavailable"
        return ""

    def _crawl(self) -> list[WikiCatalogEntry]:
        entries: list[WikiCatalogEntry] = []
        seen: set[str] = set()
        queue: deque[tuple[str, str, int]] = deque()

        if self.parent_node_token:
            root_node = self._get_node(self.parent_node_token)
            root_node.setdefault("node_token", self.parent_node_token)
            discovered_space = _text_value(root_node, "space_id", "spaceId")
            if not self.resolved_space_id:
                self.resolved_space_id = discovered_space
            if not self.resolved_space_id:
                raise WikiCatalogError(
                    "Wiki parent node response did not include a space_id"
                )

            root_entry = self._make_entry(root_node, "", 0)
            entries.append(root_entry)
            seen.add(root_entry.node_token)
            if root_entry.has_child and self.max_depth == 0:
                self._mark_limited("max_depth")
            elif root_entry.has_child and len(entries) < self.max_nodes:
                queue.append((root_entry.node_token, root_entry.path, 1))
            elif root_entry.has_child:
                self._mark_limited("max_nodes")
        else:
            roots, page_limited = self._list_children("", self.max_nodes)
            if page_limited:
                self._mark_limited("max_nodes")
            for node in roots:
                entry = self._make_entry(node, "", 0)
                if entry.node_token in seen:
                    continue
                entries.append(entry)
                seen.add(entry.node_token)
                if entry.has_child and self.max_depth == 0:
                    self._mark_limited("max_depth")
                elif entry.has_child and len(entries) < self.max_nodes:
                    queue.append((entry.node_token, entry.path, 1))
                elif entry.has_child:
                    self._mark_limited("max_nodes")

        while queue and len(entries) < self.max_nodes:
            parent_token, parent_path, depth = queue.popleft()
            remaining = self.max_nodes - len(entries)
            children, page_limited = self._list_children(parent_token, remaining)
            if page_limited:
                self._mark_limited("max_nodes")

            for node in children:
                entry = self._make_entry(node, parent_path, depth)
                if entry.node_token in seen:
                    continue
                entries.append(entry)
                seen.add(entry.node_token)
                if entry.has_child and depth >= self.max_depth:
                    self._mark_limited("max_depth")
                elif entry.has_child and len(entries) < self.max_nodes:
                    queue.append((entry.node_token, entry.path, depth + 1))
                elif entry.has_child:
                    self._mark_limited("max_nodes")
                if len(entries) >= self.max_nodes:
                    break

        if queue:
            self._mark_limited("max_nodes")
        return entries

    def _get_node(self, node_token: str) -> dict[str, Any]:
        args = [
            self.cli_path,
            "wiki",
            "+node-get",
            "--as",
            "user",
            "--node-token",
            node_token,
        ]
        if self.space_id:
            args.extend(["--space-id", self.space_id])
        args.extend(["--format", "json"])
        payload = self._run_json(args)
        body = _api_data(payload)
        if isinstance(body, Mapping):
            for key in ("node", "item"):
                candidate = body.get(key)
                if isinstance(candidate, Mapping):
                    return dict(candidate)
            if _text_value(body, "node_token", "nodeToken"):
                return dict(body)
        raise WikiCatalogError("wiki +node-get returned no node")

    def _list_children(
        self,
        parent_node_token: str,
        budget: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        if budget <= 0:
            return [], True

        nodes: list[dict[str, Any]] = []
        page_token = ""
        seen_page_tokens: set[str] = set()
        limited = False

        while len(nodes) < budget:
            page_size = min(50, budget - len(nodes))
            args = [
                self.cli_path,
                "wiki",
                "+node-list",
                "--as",
                "user",
                "--space-id",
                self.resolved_space_id,
                "--page-size",
                str(page_size),
            ]
            if parent_node_token:
                args.extend(["--parent-node-token", parent_node_token])
            if page_token:
                args.extend(["--page-token", page_token])
            args.extend(["--format", "json"])

            payload = self._run_json(args)
            body = _api_data(payload)
            page_items = _node_items(body)
            page_items.sort(key=_node_sort_key)
            remaining = budget - len(nodes)
            nodes.extend(page_items[:remaining])

            has_more = (
                _as_bool(body.get("has_more", body.get("hasMore", False)))
                if isinstance(body, Mapping)
                else False
            )
            next_page_token = (
                _text_value(body, "page_token", "pageToken")
                if isinstance(body, Mapping)
                else ""
            )
            if len(page_items) > remaining or (has_more and len(nodes) >= budget):
                limited = True
                break
            if not has_more:
                break
            if not next_page_token or next_page_token in seen_page_tokens:
                raise WikiCatalogError(
                    "wiki +node-list pagination did not advance"
                )
            seen_page_tokens.add(next_page_token)
            page_token = next_page_token

        return nodes, limited

    def _run_json(self, args: list[str]) -> Any:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr
                or completed.stdout
                or "lark-cli command failed"
            ).strip()
            raise WikiCatalogError(detail[:500])
        output = (completed.stdout or "").strip()
        if not output:
            raise WikiCatalogError("lark-cli returned an empty response")
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise WikiCatalogError("lark-cli returned invalid JSON") from exc
        if isinstance(payload, Mapping):
            code = payload.get("code")
            if code not in (None, 0, "0"):
                message = str(payload.get("msg") or payload.get("message") or code)
                raise WikiCatalogError(message[:500])
        return payload

    def _make_entry(
        self,
        node: Mapping[str, Any],
        parent_path: str,
        depth: int,
    ) -> WikiCatalogEntry:
        node_token = _text_value(node, "node_token", "nodeToken")
        if not node_token:
            raise WikiCatalogError("Wiki node is missing node_token")
        title = _clean_title(
            _text_value(node, "title", "name") or node_token
        )
        path = f"{parent_path}/{title}" if parent_path else title
        raw_has_child = node.get("has_child", node.get("hasChild"))
        return WikiCatalogEntry(
            node_token=node_token,
            obj_token=_text_value(node, "obj_token", "objToken"),
            obj_type=_text_value(node, "obj_type", "objType"),
            path=path,
            observed_permission="readable",
            title=title,
            parent_node_token=_text_value(
                node,
                "parent_node_token",
                "parentNodeToken",
            ),
            depth=depth,
            has_child=_as_bool(raw_has_child),
        )

    def _mark_limited(self, reason: str) -> None:
        self.truncated = True
        if reason not in self.limit_reasons:
            self.limit_reasons.append(reason)


def build_wiki_catalog(
    config: Mapping[str, Any],
    cli_path: str = "",
    max_nodes: Optional[int] = None,
    max_depth: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Convenience wrapper returning only the stable ledger."""
    return WikiCatalog(
        config,
        cli_path=cli_path,
        max_nodes=max_nodes,
        max_depth=max_depth,
    ).scan()


def resolve_catalog_target(
    ledger: Iterable[Mapping[str, Any]],
    category: str,
    subcategory: str = "",
    parent_node_token: str = "",
    fallback_to_category: bool = True,
) -> Optional[dict[str, Any]]:
    """Resolve a category/subcategory to a node inside the supplied ledger."""
    category_key = _match_key(category)
    if not category_key:
        return None
    subcategory_key = _match_key(subcategory)
    rows = [dict(entry) for entry in ledger]
    rows.sort(key=_mapping_sort_key)

    if parent_node_token:
        category_candidates = [
            row
            for row in rows
            if (
                str(row.get("parent_node_token", "")) == parent_node_token
                and _match_key(row.get("title", "")) == category_key
            )
        ]
        parent_match = [
            row
            for row in rows
            if (
                str(row.get("node_token", "")) == parent_node_token
                and _match_key(row.get("title", "")) == category_key
            )
        ]
        category_candidates.extend(parent_match)
    else:
        minimum_depth = min(
            (int(row.get("depth", 0)) for row in rows),
            default=0,
        )
        category_candidates = [
            row
            for row in rows
            if (
                int(row.get("depth", 0)) == minimum_depth
                and _match_key(row.get("title", "")) == category_key
            )
        ]

    if not category_candidates:
        return None
    category_node = sorted(category_candidates, key=_mapping_sort_key)[0]
    if not subcategory_key:
        return category_node

    category_token = str(category_node.get("node_token", ""))
    subcategory_candidates = [
        row
        for row in rows
        if (
            str(row.get("parent_node_token", "")) == category_token
            and _match_key(row.get("title", "")) == subcategory_key
        )
    ]
    if subcategory_candidates:
        return sorted(subcategory_candidates, key=_mapping_sort_key)[0]
    return category_node if fallback_to_category else None


def _api_data(payload: Any) -> Any:
    current = payload
    while isinstance(current, Mapping) and isinstance(current.get("data"), Mapping):
        current = current["data"]
    return current


def _node_items(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        values = body
    elif isinstance(body, Mapping):
        values = body.get("items", body.get("nodes", []))
    else:
        values = []
    if not isinstance(values, list):
        raise WikiCatalogError("wiki +node-list returned invalid items")
    return [dict(value) for value in values if isinstance(value, Mapping)]


def _text_value(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _clean_title(value: Any) -> str:
    return " ".join(str(value or "").split())


def _match_key(value: Any) -> str:
    return unicodedata.normalize("NFKC", _clean_title(value)).casefold()


def _node_sort_key(node: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _match_key(_text_value(node, "title", "name")),
        _text_value(node, "node_token", "nodeToken"),
    )


def _entry_sort_key(entry: WikiCatalogEntry) -> tuple[str, str]:
    return (_match_key(entry.path), entry.node_token)


def _mapping_sort_key(entry: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _match_key(entry.get("path", "")),
        str(entry.get("node_token", "")),
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _bounded_int(
    value: Any,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


__all__ = [
    "WikiCatalog",
    "WikiCatalogEntry",
    "WikiCatalogError",
    "build_wiki_catalog",
    "resolve_catalog_target",
]
