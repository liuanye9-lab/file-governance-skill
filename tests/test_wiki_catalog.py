import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.governance.wiki_catalog import WikiCatalog, WikiCatalogError


def wiki_config(**overrides):
    wiki = {
        "enabled": True,
        "space_id": "space-1",
        "parent_node_token": "root",
        "max_nodes": 20,
        "max_depth": 3,
    }
    wiki.update(overrides)
    return {
        "feishu": {
            "provider": "cli",
            "wiki": wiki,
        }
    }


def cli_result(payload, returncode=0, stderr=""):
    return SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps(payload),
        stderr=stderr,
    )


class WikiCatalogTests(unittest.TestCase):
    @patch("src.governance.wiki_catalog.subprocess.run")
    def test_disabled_and_local_only_never_call_lark_cli(self, run):
        disabled = WikiCatalog(
            wiki_config(enabled=False),
            cli_path="/mock/lark-cli",
        )
        self.assertEqual(disabled.scan(), [])
        self.assertEqual(disabled.status, "disabled")

        local_only_config = wiki_config()
        local_only_config["feishu"]["provider"] = "none"
        local_only = WikiCatalog(
            local_only_config,
            cli_path="/mock/lark-cli",
        )
        snapshot = local_only.snapshot()
        self.assertEqual(snapshot["status"], "local-only")
        self.assertEqual(snapshot["entries"], [])
        run.assert_not_called()

    @patch("src.governance.wiki_catalog.subprocess.run")
    def test_reads_only_configured_subtree_and_builds_stable_ledger(self, run):
        nodes = {
            "root": {
                "node_token": "root",
                "obj_token": "doc-root",
                "obj_type": "docx",
                "space_id": "space-1",
                "title": "治理目录",
                "has_child": True,
            },
            "category-a": {
                "node_token": "category-a",
                "obj_token": "doc-a",
                "obj_type": "docx",
                "parent_node_token": "root",
                "title": "运营管理",
                "has_child": True,
            },
            "category-b": {
                "node_token": "category-b",
                "obj_token": "doc-b",
                "obj_type": "docx",
                "parent_node_token": "root",
                "title": "技术研发",
                "has_child": False,
            },
            "subcategory-a": {
                "node_token": "subcategory-a",
                "obj_token": "doc-a-1",
                "obj_type": "docx",
                "parent_node_token": "category-a",
                "title": "流程 SOP",
                "has_child": False,
            },
        }

        def respond(args, **kwargs):
            self.assertEqual(kwargs["timeout"], 30)
            self.assertFalse(kwargs["check"])
            if "+node-get" in args:
                self.assertEqual(args[args.index("--node-token") + 1], "root")
                self.assertEqual(args[args.index("--space-id") + 1], "space-1")
                return cli_result({"code": 0, "data": {"node": nodes["root"]}})

            self.assertIn("+node-list", args)
            self.assertEqual(args[args.index("--space-id") + 1], "space-1")
            parent = args[args.index("--parent-node-token") + 1]
            if parent == "root":
                return cli_result({
                    "code": 0,
                    "data": {
                        "items": [nodes["category-a"], nodes["category-b"]],
                        "has_more": False,
                    },
                })
            if parent == "category-a":
                return cli_result({
                    "code": 0,
                    "data": {
                        "items": [nodes["subcategory-a"]],
                        "has_more": False,
                    },
                })
            self.fail(f"unexpected parent: {parent}")

        run.side_effect = respond
        catalog = WikiCatalog(wiki_config(), cli_path="/mock/lark-cli")
        ledger = catalog.scan()

        self.assertEqual(
            [entry["path"] for entry in ledger],
            [
                "治理目录",
                "治理目录/技术研发",
                "治理目录/运营管理",
                "治理目录/运营管理/流程 SOP",
            ],
        )
        for entry in ledger:
            self.assertEqual(entry["observed_permission"], "readable")
            self.assertTrue({
                "node_token",
                "obj_token",
                "obj_type",
                "path",
                "observed_permission",
            }.issubset(entry))

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertTrue(all("+space-list" not in command for command in commands))
        self.assertEqual(catalog.status, "ok")
        self.assertFalse(catalog.truncated)

    @patch("src.governance.wiki_catalog.subprocess.run")
    def test_paginates_within_node_limit(self, run):
        root = {
            "node_token": "root",
            "obj_token": "doc-root",
            "obj_type": "docx",
            "space_id": "space-1",
            "title": "Root",
            "has_child": True,
        }
        child_a = {
            "node_token": "a",
            "obj_token": "doc-a",
            "obj_type": "docx",
            "parent_node_token": "root",
            "title": "A",
            "has_child": False,
        }
        child_b = {
            "node_token": "b",
            "obj_token": "doc-b",
            "obj_type": "docx",
            "parent_node_token": "root",
            "title": "B",
            "has_child": False,
        }

        def respond(args, **kwargs):
            if "+node-get" in args:
                return cli_result({"data": {"node": root}})
            if "--page-token" not in args:
                return cli_result({
                    "data": {
                        "items": [child_a],
                        "has_more": True,
                        "page_token": "page-2",
                    },
                })
            self.assertEqual(args[args.index("--page-token") + 1], "page-2")
            return cli_result({
                "data": {
                    "items": [child_b],
                    "has_more": False,
                },
            })

        run.side_effect = respond
        catalog = WikiCatalog(
            wiki_config(max_nodes=3),
            cli_path="/mock/lark-cli",
        )
        ledger = catalog.scan()

        self.assertEqual([entry["node_token"] for entry in ledger], ["root", "a", "b"])
        self.assertFalse(catalog.truncated)
        self.assertEqual(run.call_count, 3)

    @patch("src.governance.wiki_catalog.subprocess.run")
    def test_max_nodes_stops_recursion_and_marks_catalog_truncated(self, run):
        root = {
            "node_token": "root",
            "obj_token": "doc-root",
            "obj_type": "docx",
            "space_id": "space-1",
            "title": "Root",
            "has_child": True,
        }
        children = [
            {
                "node_token": token,
                "obj_token": f"doc-{token}",
                "obj_type": "docx",
                "parent_node_token": "root",
                "title": title,
                "has_child": True,
            }
            for token, title in (("c", "C"), ("a", "A"), ("b", "B"))
        ]
        run.side_effect = [
            cli_result({"data": {"node": root}}),
            cli_result({"data": {"items": children, "has_more": False}}),
        ]
        catalog = WikiCatalog(
            wiki_config(max_nodes=3, max_depth=8),
            cli_path="/mock/lark-cli",
        )

        ledger = catalog.scan()

        self.assertEqual([entry["node_token"] for entry in ledger], ["root", "a", "b"])
        self.assertTrue(catalog.truncated)
        self.assertIn("max_nodes", catalog.limit_reasons)
        self.assertEqual(run.call_count, 2)

    @patch("src.governance.wiki_catalog.subprocess.run")
    def test_max_depth_does_not_read_below_boundary(self, run):
        root = {
            "node_token": "root",
            "obj_token": "doc-root",
            "obj_type": "docx",
            "space_id": "space-1",
            "title": "Root",
            "has_child": True,
        }
        category = {
            "node_token": "category",
            "obj_token": "doc-category",
            "obj_type": "docx",
            "parent_node_token": "root",
            "title": "Category",
            "has_child": True,
        }
        subcategory = {
            "node_token": "subcategory",
            "obj_token": "doc-subcategory",
            "obj_type": "docx",
            "parent_node_token": "category",
            "title": "Subcategory",
            "has_child": True,
        }
        run.side_effect = [
            cli_result({"data": {"node": root}}),
            cli_result({"data": {"items": [category], "has_more": False}}),
        ]
        catalog = WikiCatalog(
            wiki_config(max_depth=1),
            cli_path="/mock/lark-cli",
        )

        ledger = catalog.scan()

        self.assertEqual(
            [entry["node_token"] for entry in ledger],
            ["root", "category"],
        )
        self.assertTrue(catalog.truncated)
        self.assertEqual(catalog.limit_reasons, ["max_depth"])
        self.assertEqual(run.call_count, 2)

    @patch("src.governance.wiki_catalog.subprocess.run")
    def test_maps_category_and_subcategory_without_remote_write(self, run):
        catalog = WikiCatalog(wiki_config(), cli_path="/mock/lark-cli")
        catalog.status = "ok"
        catalog._ledger = [
            {
                "node_token": "root",
                "obj_token": "doc-root",
                "obj_type": "docx",
                "path": "治理目录",
                "observed_permission": "readable",
                "title": "治理目录",
                "parent_node_token": "",
                "depth": 0,
                "has_child": True,
            },
            {
                "node_token": "category",
                "obj_token": "doc-category",
                "obj_type": "docx",
                "path": "治理目录/运营管理",
                "observed_permission": "readable",
                "title": "运营管理",
                "parent_node_token": "root",
                "depth": 1,
                "has_child": True,
            },
            {
                "node_token": "subcategory",
                "obj_token": "doc-subcategory",
                "obj_type": "docx",
                "path": "治理目录/运营管理/流程 SOP",
                "observed_permission": "readable",
                "title": "流程 SOP",
                "parent_node_token": "category",
                "depth": 2,
                "has_child": False,
            },
        ]

        exact = catalog.map_target_node(" 运营管理 ", "流程 sop")
        fallback = catalog.map_target_node("运营管理", "不存在")
        missing = catalog.map_target_node(
            "运营管理",
            "不存在",
            fallback_to_category=False,
        )

        self.assertEqual(exact["node_token"], "subcategory")
        self.assertEqual(fallback["node_token"], "category")
        self.assertIsNone(missing)
        self.assertEqual(
            catalog.target_node_token("运营管理", "流程 SOP"),
            "subcategory",
        )
        run.assert_not_called()

    @patch("src.governance.wiki_catalog.subprocess.run")
    def test_snapshot_reports_cli_failure_without_partial_ledger(self, run):
        run.return_value = cli_result(
            {"error": "not used"},
            returncode=1,
            stderr="permission denied",
        )
        catalog = WikiCatalog(wiki_config(), cli_path="/mock/lark-cli")

        snapshot = catalog.snapshot()

        self.assertEqual(snapshot["status"], "error")
        self.assertEqual(snapshot["entries"], [])
        self.assertIn("permission denied", snapshot["error"])
        with self.assertRaises(WikiCatalogError):
            catalog.scan()


if __name__ == "__main__":
    unittest.main()
