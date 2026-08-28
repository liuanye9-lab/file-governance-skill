import re
from ..models.file_record import FileRecord
from ..utils.logger import setup_logger

logger = setup_logger()


# ============ 通用行业领域词典（跨行业，基于内容判定）============
# 每个领域给一组高区分度关键词；命中即加分，取最高分领域。
DOMAIN_LEXICON = {
    "技术研发": ["代码", "算法", "接口", "api", "架构", "部署", "数据库", "服务器", "bug",
                "需求文档", "技术方案", "开发", "测试用例", "sdk", "python", "java", "前端", "后端", "模型训练"],
    "金融财务": ["财务", "报表", "利润", "营收", "成本", "预算", "现金流", "资产负债", "审计",
                "发票", "报销", "投资", "估值", "股票", "融资", "会计", "税务", "结算", "收银"],
    "法律合规": ["合同", "协议", "条款", "甲方", "乙方", "法律", "合规", "违约", "知识产权",
                "保密", "诉讼", "仲裁", "授权", "隐私政策", "监管", "许可", "责任"],
    "医疗健康": ["患者", "临床", "诊断", "药物", "医院", "病历", "治疗", "康复", "护理",
                "医疗", "药品", "健康", "手术", "检验", "疫苗", "康养"],
    "市场营销": ["营销", "推广", "品牌", "投放", "转化率", "获客", "文案", "活动策划",
                "用户增长", "社媒", "广告", "内容运营", "私域", "曝光", "kol"],
    "人力行政": ["招聘", "入职", "绩效", "薪酬", "考勤", "培训", "员工", "岗位", "组织架构",
                "面试", "劳动合同", "福利", "人事", "行政", "考核"],
    "教育科研": ["教学", "课程", "学生", "论文", "研究", "实验", "教材", "考试", "培养方案",
                "学术", "科研", "教育", "课题", "文献"],
    "运营管理": ["sop", "流程", "运营", "手册", "制度", "规范", "标准", "服务流程", "岗位职责",
                "客诉", "前厅", "客房", "接待", "值班", "巡检", "门店"],
    "生产制造": ["生产", "制造", "工艺", "质检", "供应链", "库存", "设备", "车间", "物料",
                "产线", "良品率", "工单", "采购"],
    "数据分析": ["数据分析", "指标", "看板", "可视化", "报告", "统计", "趋势", "同比", "环比",
                "留存", "漏斗", "画像", "bi", "数据集"],
}

# ============ 通用文档类型词典（跨行业维度）============
DOCTYPE_LEXICON = {
    "流程SOP": ["sop", "流程", "步骤", "操作规范", "作业指导", "岗位手册", "标准作业", "操作手册"],
    "制度规范": ["制度", "规定", "管理办法", "规范", "准则", "章程", "条例", "守则"],
    "合同协议": ["合同", "协议", "甲方", "乙方", "条款", "签署", "授权书", "备忘录"],
    "报告分析": ["报告", "分析", "总结", "复盘", "调研", "研究", "白皮书", "评估"],
    "会议纪要": ["会议", "纪要", "议题", "决议", "行动项", "参会", "会议记录"],
    "培训资料": ["培训", "课件", "教程", "应知应会", "学习", "带教", "讲义"],
    "数据报表": ["报表", "台账", "明细表", "统计表", "清单", "数据表", "汇总表"],
    "方案设计": ["方案", "计划", "设计", "规划", "策划", "蓝图", "提案", "路线图"],
    "案例记录": ["案例", "客诉", "投诉", "整改", "事故", "问题汇总", "复盘记录"],
    "通知公告": ["通知", "公告", "通报", "公示", "告知"],
}


class Classifier:
    """基于内容的通用分类器（跨行业）。

    输出三个维度：
    - domain：行业领域（技术研发/金融财务/法律合规/...），跨行业通用，始终产出
    - doc_type：文档类型（流程SOP/合同协议/报告分析/...），跨行业通用，始终产出
    - category/sub_category：若配置了项目专属 taxonomy 则优先按其分类（如酒店运营场景）；
      否则回退为 category=domain、sub_category=doc_type，保证任何行业开箱即用。
    """

    DEFAULT_CATEGORY = "待人工复核"

    def __init__(self, taxonomy: dict):
        self.taxonomy = taxonomy or {}
        self.categories = self.taxonomy.get("categories", [])

    def process(self, record: FileRecord) -> FileRecord:
        text = (record.text_content or "") + " " + (record.file_name or "")
        text_lower = text.lower()

        domain, domain_hits = self._best_match(text_lower, DOMAIN_LEXICON)
        doc_type, doctype_hits = self._best_match(text_lower, DOCTYPE_LEXICON)

        record.domain = domain or "通用"
        record.doc_type = doc_type or "其他文档"

        # 项目专属 taxonomy 优先（兼容酒店等既有场景）；无命中则回退到通用维度
        proj_cat, proj_sub, proj_hits = self._match_project_taxonomy(text_lower)
        if proj_cat:
            record.category = proj_cat
            record.sub_category = proj_sub
            tag_source = proj_hits
        else:
            record.category = record.domain
            record.sub_category = record.doc_type
            tag_source = domain_hits + doctype_hits

        tags = list(dict.fromkeys(tag_source))[:10]
        if not tags and record.file_ext:
            tags.append(record.file_ext.upper())
        record.tags = tags

        record.summary = self._generate_summary(record)
        record.log_step(
            "classify",
            f"domain={record.domain} | doc_type={record.doc_type} | category={record.category}",
        )
        return record

    def _best_match(self, text_lower: str, lexicon: dict) -> tuple[str, list[str]]:
        best, best_score, best_hits = "", 0, []
        for name, keywords in lexicon.items():
            hits = [kw for kw in keywords if kw and kw.lower() in text_lower]
            if len(hits) > best_score:
                best, best_score, best_hits = name, len(hits), hits
        return best, best_hits

    def _match_project_taxonomy(self, text_lower: str) -> tuple[str, str, list[str]]:
        if not self.categories:
            return "", "", []
        scores, matched_by_cat = {}, {}
        for cat in self.categories:
            cat_name = cat.get("name")
            if not cat_name:
                continue
            hits = [kw for kw in cat.get("keywords", []) if kw and kw.lower() in text_lower]
            score = len(hits) * 2
            for sub in cat.get("sub", []):
                if sub and sub.lower() in text_lower:
                    score += 1
            scores[cat_name] = score
            matched_by_cat[cat_name] = hits
        if not scores:
            return "", "", []
        best_cat = max(scores, key=scores.get)
        if scores.get(best_cat, 0) == 0:
            return "", "", []  # 项目 taxonomy 无命中，交给通用维度
        sub_cat = ""
        for cat in self.categories:
            if cat.get("name") == best_cat:
                for sub in cat.get("sub", []):
                    if sub and sub.lower() in text_lower:
                        sub_cat = sub
                        break
                if not sub_cat and cat.get("sub"):
                    sub_cat = cat["sub"][0]
                break
        return best_cat, sub_cat, matched_by_cat.get(best_cat, [])

    def _generate_summary(self, record: FileRecord) -> str:
        text = record.text_content or ""
        name = record.file_name
        label = f"{record.domain}/{record.doc_type}"
        if not text.strip():
            return f"{name}：{label}（文本解析为空，基于文件名与类型判定）。"
        sentences = re.split(r"[。\n\r！？.!?]", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10][:3]
        if sentences:
            excerpt = "；".join(sentences[:2])[:150]
            return f"{name}：属【{label}】，核心内容涉及：{excerpt}。"
        return f"{name}：属【{label}】，包含约 {record.file_size // 1024}KB 内容。"
