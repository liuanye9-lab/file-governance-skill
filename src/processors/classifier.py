import re
from typing import Optional

from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


class Classifier:
    DEFAULT_CATEGORY = "待人工复核"

    def __init__(self, taxonomy: dict):
        self.taxonomy = taxonomy or {}
        self.categories = self.taxonomy.get("categories", [])
        self._build_index()

    def _build_index(self):
        self.keyword_map = {}
        for cat in self.categories:
            cat_name = cat["name"]
            for kw in cat.get("keywords", []):
                if kw:
                    self.keyword_map[kw.lower()] = (cat_name, cat.get("sub", [""])[0] if cat.get("sub") else "")

    def process(self, record: FileRecord) -> FileRecord:
        category, sub_category, tags, summary = self._classify(record)
        record.category = category
        record.sub_category = sub_category
        record.tags = tags
        record.summary = summary
        record.log_step("classify", f"{category} > {sub_category}")
        return record

    def _classify(self, record: FileRecord) -> tuple[str, str, list[str], str]:
        text = (record.text_content or "") + " " + record.file_name
        text_lower = text.lower()

        scores = {}
        matched_by_cat = {}  # 每个分类各自命中的关键词，避免 tags 跨分类泄漏
        for cat in self.categories:
            cat_name = cat.get("name")
            if not cat_name:
                continue
            score = 0
            hits = []
            for kw in cat.get("keywords", []):
                if kw and kw.lower() in text_lower:
                    score += 2
                    hits.append(kw)
            for sub in cat.get("sub", []):
                if sub and sub.lower() in text_lower:
                    score += 1
            scores[cat_name] = score
            matched_by_cat[cat_name] = hits

        best_cat = max(scores, key=scores.get) if scores else self.DEFAULT_CATEGORY
        if scores.get(best_cat, 0) == 0:
            best_cat = self.DEFAULT_CATEGORY

        sub_cat = ""
        for cat in self.categories:
            if cat.get("name") == best_cat:
                subs = cat.get("sub", [])
                for sub in subs:
                    if sub and sub.lower() in text_lower:
                        sub_cat = sub
                        break
                if not sub_cat and subs:
                    sub_cat = subs[0]
                break

        # tags 只取被选中分类命中的关键词，保证与最终分类一致
        tags = list(dict.fromkeys(matched_by_cat.get(best_cat, [])))[:10]
        if not tags and record.file_ext:
            tags.append(record.file_ext.upper())

        summary = self._generate_summary(record, best_cat, sub_cat)
        return best_cat, sub_cat, tags, summary

    def _generate_summary(self, record: FileRecord, category: str, sub_category: str) -> str:
        text = record.text_content or ""
        name = record.file_name
        if not text.strip():
            return f"{name}：{category}类材料（文本解析为空，基于文件名分类）。"
        sentences = re.split(r"[。\n\r！？.!?]", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10][:3]
        if sentences:
            excerpt = "；".join(sentences[:2])[:150]
            return f"{name}：{category}/{sub_category}相关材料，核心内容涉及：{excerpt}。"
        return f"{name}：{category}类材料，包含{record.file_size//1024}KB内容。"
