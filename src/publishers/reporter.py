import json
from typing import Optional


class ResultReporter:
    def build_card(self, results: list[dict], stats: dict, dashboard_url: str = "") -> dict:
        done = stats.get("done", 0)
        skipped = stats.get("skipped", 0)
        failed = stats.get("failed", 0)
        total = stats.get("total", 0)
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📊 文件治理报告**\n\n总计 **{total}** 个文件，新增 **{done}**，跳过 **{skipped}**，失败 **{failed}**"
                }
            },
            {"tag": "hr"},
        ]
        if results:
            items = []
            for r in results[:10]:
                cat = r.get("category", "其他")
                name = r.get("file_name", "")
                summary = r.get("summary", "")
                if summary and len(summary) > 80:
                    summary = summary[:77] + "..."
                items.append(f"• **{name}** → {cat}\n  {summary}")
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n\n".join(items)
                }
            })
        if dashboard_url:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开 Dashboard 观察台"},
                        "type": "primary",
                        "url": dashboard_url,
                    }
                ]
            })
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "文件治理完成"},
                "template": "grey" if failed == 0 else "orange",
            },
            "elements": elements,
        }

    def build_error_card(self, message: str) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "文件治理出错"},
                "template": "red",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**错误信息**\n{message}"}
                }
            ],
        }

    def build_empty_card(self) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "文件治理"},
                "template": "grey",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "没有发现新文件，所有文件均已处理过。"}
                }
            ],
        }
