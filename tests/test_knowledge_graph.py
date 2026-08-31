import unittest
from unittest.mock import patch

from src.models.file_record import FileRecord
from src.publishers.knowledge_graph import KnowledgeGraphPublisher


class KnowledgeGraphTests(unittest.TestCase):
    def test_builds_source_category_and_version_relations(self):
        records = [
            FileRecord(
                file_name="policy.md",
                file_hash="a" * 64,
                version=1,
                status="done",
                category="制度",
                source="inbox",
            ),
            FileRecord(
                file_name="policy.md",
                file_hash="b" * 64,
                version=2,
                status="done",
                category="制度",
                source="url_fetch",
            ),
        ]

        graph = KnowledgeGraphPublisher.build(records)
        relations = {edge["relation"] for edge in graph["edges"]}

        self.assertIn("contains", relations)
        self.assertIn("derived_from", relations)
        self.assertIn("supersedes", relations)

    def test_disabled_does_not_publish(self):
        publisher = KnowledgeGraphPublisher({"knowledge_graph": {"enabled": False}})
        result = publisher.publish({"nodes": [], "edges": []})
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["published"])

    def test_publish_reports_cli_failure(self):
        publisher = KnowledgeGraphPublisher({
            "knowledge_graph": {
                "enabled": True,
                "whiteboard_token": "wb-token",
            },
        })
        publisher.cli_path = "/tmp/fake-cli"
        with patch("src.publishers.knowledge_graph.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "permission denied"
            run.return_value.stdout = ""
            result = publisher.publish({"nodes": [], "edges": []})

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["published"])
        command = run.call_args.args[0]
        self.assertIn("--overwrite", command)

    def test_provider_none_never_writes_whiteboard(self):
        publisher = KnowledgeGraphPublisher({
            "feishu": {"provider": "none"},
            "knowledge_graph": {
                "enabled": True,
                "whiteboard_token": "wb-token",
            },
        })
        publisher.cli_path = "/tmp/fake-cli"
        with patch("src.publishers.knowledge_graph.subprocess.run") as run:
            result = publisher.publish({"nodes": [], "edges": []})

        self.assertEqual(result["status"], "local-only")
        self.assertFalse(result["published"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
