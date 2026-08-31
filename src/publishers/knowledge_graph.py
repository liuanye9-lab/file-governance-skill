import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from ..governance.sensitivity import SensitivityScanner
from ..models.file_record import FileRecord


class KnowledgeGraphPublisher:
    """生成知识关系图，并可选择同步到已配置的飞书画板。"""

    def __init__(self, config: dict):
        graph_cfg = config.get("knowledge_graph", {})
        feishu_cfg = config.get("feishu", {})
        self.enabled = bool(graph_cfg.get("enabled", False))
        self.provider = str(feishu_cfg.get("provider", "cli")).lower()
        self.local_only = bool(
            config.get("local_only")
            or feishu_cfg.get("local_only")
            or graph_cfg.get("local_only")
            or self.provider in {"none", "local", "local-only", "local_only"}
        )
        self.whiteboard_token = graph_cfg.get("whiteboard_token", "")
        self.overwrite = bool(graph_cfg.get("overwrite", True))
        configured_cli = feishu_cfg.get("cli_path", "")
        self.cli_path = configured_cli if Path(configured_cli).is_file() else shutil.which("lark-cli") or ""
        self._scanner = SensitivityScanner()

    @staticmethod
    def build(records: list[FileRecord]) -> dict:
        nodes = {}
        edges = {}
        scanner = SensitivityScanner()

        def add_node(node_id: str, kind: str, label: str, **attrs):
            nodes[node_id] = {
                "id": node_id,
                "kind": kind,
                "label": label,
                **attrs,
            }

        def add_edge(source: str, target: str, relation: str):
            key = f"{source}|{relation}|{target}"
            edges[key] = {
                "source": source,
                "target": target,
                "relation": relation,
            }

        add_node("project", "project", "知识库")
        by_name = {}
        for record in records:
            if record.status != "done":
                continue
            category = record.category or record.domain or "未分类"
            category_id = f"category:{category}"
            add_node(
                category_id,
                "category",
                scanner.redact(category),
            )
            add_edge("project", category_id, "contains")

            digest = record.file_hash[:16] or hashlib.sha256(record.id.encode()).hexdigest()[:16]
            knowledge_id = f"knowledge:{digest}:v{record.version}"
            add_node(
                knowledge_id,
                "knowledge",
                scanner.redact(record.file_name),
                version=record.version,
                production_ready=record.production_ready,
                doc_url=record.doc_url,
            )
            add_edge(category_id, knowledge_id, "contains")

            source_id = f"source:{digest}:v{record.version}"
            add_node(source_id, "source", record.source or "unknown", url=record.drive_url)
            add_edge(knowledge_id, source_id, "derived_from")
            by_name.setdefault(record.file_name, []).append((record.version, knowledge_id))

        for versions in by_name.values():
            ordered = sorted(versions)
            for (_, previous), (_, current) in zip(ordered, ordered[1:]):
                add_edge(current, previous, "supersedes")

        return {
            "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
            "edges": sorted(
                edges.values(),
                key=lambda item: (item["source"], item["relation"], item["target"]),
            ),
        }

    def publish(self, graph: dict) -> dict:
        if not self.enabled:
            return {"status": "disabled", "published": False, "graph": graph}
        if self.local_only:
            return {"status": "local-only", "published": False, "graph": graph}
        if not self.whiteboard_token:
            return {
                "status": "pending",
                "published": False,
                "reason": "未配置 whiteboard_token",
                "graph": graph,
            }
        if not self.cli_path:
            return {
                "status": "blocked",
                "published": False,
                "reason": "lark-cli 不可用",
                "graph": graph,
            }

        source = self._to_mermaid(graph)
        idempotency = hashlib.sha256(
            json.dumps(graph, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:24]
        args = [
            self.cli_path, "whiteboard", "+update", "--as", "user",
            "--whiteboard-token", self.whiteboard_token,
            "--input_format", "mermaid", "--source", source,
            "--idempotent-token", idempotency, "--format", "json",
        ]
        if self.overwrite:
            args.append("--overwrite")
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "status": "failed",
                "published": False,
                "reason": (completed.stderr or completed.stdout).strip()[:1000],
                "graph": graph,
            }
        try:
            response = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            response = {"raw": completed.stdout.strip()}
        return {
            "status": "success",
            "published": True,
            "whiteboard_token": self.whiteboard_token,
            "response": response,
            "graph": graph,
        }

    @staticmethod
    def _to_mermaid(graph: dict) -> str:
        lines = ["flowchart LR"]
        for node in graph.get("nodes", []):
            node_id = hashlib.sha256(node["id"].encode()).hexdigest()[:12]
            label = str(node.get("label") or node["id"]).replace('"', "'")
            lines.append(f'  n{node_id}["{label}"]')
        lookup = {
            node["id"]: f"n{hashlib.sha256(node['id'].encode()).hexdigest()[:12]}"
            for node in graph.get("nodes", [])
        }
        for edge in graph.get("edges", []):
            source = lookup.get(edge["source"])
            target = lookup.get(edge["target"])
            if source and target:
                relation = edge.get("relation", "").replace("|", "/")
                lines.append(f"  {source} -->|{relation}| {target}")
        return "\n".join(lines)
