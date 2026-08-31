---
name: "file-governance"
description: "企业级文件与数据治理 Skill：从微信/本地/URL 等来源收集资料，经解析、分类、去重、风险与质量门禁后，生成飞书标准知识页、多维表格人机双通道、关系图和复核任务。Invoke when user says '沉淀知识'/'治理文件'/'整理资料'/'sync files'/'governance'，或需要将分散文件统一归档到飞书知识库时。"
---

# File Governance · 企业级文件与数据治理 Skill

## 一句话概述

**表层操作极简，底层逻辑复杂** — 拖入文件或说一句话，完成收集→解析→分类→风险/质量审核→目录映射→确认发布→标准知识页→双表沉淀→回读验收→持续复核。

---

## 何时调用本 Skill

触发场景：

- 用户说「沉淀知识」「整理文件」「治理资料」「归档到飞书」「sync knowledge」「govern files」
- 用户将文件拖拽到收件箱或提及微信/本地散落文件需要整理
- 需要将分散在微信、下载目录、桌面的业务材料统一沉淀到飞书知识库
- 需要生成面向人类协作的看板 + 面向 Agent 消费的结构化上下文
- 需要对文件进行权限治理（密级、内部可见）、去重、版本管理

---

## 核心能力矩阵

| 能力域 | 功能点 | 状态 |
|--------|--------|------|
| **多来源收集** | 微信本地目录扫描、本地收件箱拖拽、指定文件夹监控、**文件地址自动爬取（URL/本地路径）**、可插拔适配器接口 | ✅ 核心可用 / 🔌 扩展中 |
| **格式解析** | docx/pptx/xlsx/pdf/txt/md/csv/json/zip/图片元数据；doc 通过 textutil；xls 通过 xlrd | ✅ |
| **跨行业智能分类** | 基于内容的双轴分类：**行业领域**（技术研发/金融财务/法律合规/医疗健康/市场营销/人力行政/教育科研/运营管理/生产制造/数据分析）× **文档类型**（流程SOP/制度规范/合同协议/报告分析/会议纪要/培训资料/数据报表/方案设计/案例记录/通知公告）；配置项目 taxonomy 时优先按其分类，否则通用维度兜底，任意行业开箱即用；自动打标签、生成第一性原理摘要 | ✅ |
| **去重引擎** | SHA-256 精确哈希 + 路径去重 + 文件名相似度三级策略 | ✅ |
| **版本控制** | 同名/同内容文件版本链追踪，版本历史可追溯 | ✅ |
| **元数据提取** | 文件大小、类型、创建/修改时间、作者、页数、来源会话 | ✅ |
| **发布前盘点** | 只读生成资料台账、目录映射建议、冲突清单、敏感清单、无法解析清单和发布计划 | ✅ |
| **Wiki 子树接管** | 只读取用户显式配置的 space/node 范围，生成节点台账、观察权限并映射分类目标 | ✅ |
| **七类发布决策** | create/update/merge/split/reference/pending/exclude，输出逐项状态、原因和权限缺口 | ✅ |
| **敏感与冲突治理** | 脱敏证据初筛；高敏、同名不同内容、无法解析项自动进入 P0 待审核，不自动发布 | ✅ |
| **AI 质量审核** | 按“内容可靠、易找易懂、易维护”评分，生成 production_ready 与 P0/P1/P2 建议 | ✅ |
| **媒体证据治理** | 图片/音视频读取可追溯 sidecar 或显式注入文本，解析视频时间码；无 OCR/ASR 证据不生成伪正文 | ✅ |
| **权限治理** | 自动设置密级（L2-Internal）+ tenant_readable（组织内可见） | ✅ |
| **标准知识页** | 生产就绪资料生成飞书 Docx 治理页，支持创建/更新、脱敏和创建后回读 | ✅ |
| **双通道沉淀** | 飞书多维表格「知识材料」表（人类协作）+「Agent上下文窗口」表（机器消费，含 domain/doc_type 结构化字段） | ✅ |
| **统一发布验收** | 校验知识页、原始来源、治理表、正文、production_ready、权限和回读证据 | ✅ |
| **知识关系图** | 输出分类/知识/来源/版本 nodes+edges；可选同步到显式配置的飞书画板 | ✅ |
| **持续复核任务** | 30/90/180 天到期清单，幂等创建飞书任务并回写负责人、GUID、链接和提醒 | ✅ |
| **Dashboard 观察台** | 指标卡、分类环图、文件类型构成、覆盖状态、协作进度 | ✅ |
| **审计追溯** | 全链路处理日志、状态记录、失败重试 | ✅ |
| **飞书机器人** | 通过飞书消息直接发送文件即时沉淀 | 🔌 预留 |
| **邮件/钉钉/企微** | 多平台适配器 | 🔌 预留 |

---

## 操作流程（极简表层）

### 入口 A：对话触发（最常用）

在 TRAE 中说：
```
沉淀知识
```
或
```
整理文件到飞书
```
Skill 自动：扫描配置的来源 → 增量处理新文件 → 上传飞书 → 同步双表 → 返回处理结果卡片。

### 入口 B：收件箱拖拽（手动上传）

1. 启动收件箱服务：`python3 scripts/start_inbox.py`
2. 浏览器打开 `http://127.0.0.1:8765`
3. 拖拽文件到页面 → 自动归档

### 入口 C：CLI 命令（高级）

```bash
# 全量扫描处理（默认增量）
python3 src/cli.py run

# 仅扫描收件箱
python3 src/cli.py run --source inbox

# 文件地址自动爬取（支持 URL 或本地路径，可多个）
python3 src/cli.py fetch "https://example.com/report.pdf" "/path/to/合同.docx"

# 官方治理模式：先只读盘点，不写入飞书
python3 src/cli.py plan --source inbox
python3 src/cli.py plan --url "https://example.com/report.pdf"

# 确认发布盘点结果；冲突/无法解析项可显式批准，高敏资料仍保持拦截
python3 src/cli.py publish --yes
python3 src/cli.py publish --yes --approve-risk

# 持续运维：列出到期复核知识
python3 src/cli.py review-due
python3 src/cli.py review-due --create-tasks

# 独立质检：不重新采集、不上传、不修改正文
python3 src/cli.py quality-review

# 全量刷新（清空重跑）
python3 src/cli.py refresh

# 权限治理（批量设密级+内部可见）
python3 src/cli.py govern-permissions
```

---

## 处理流水线（底层复杂逻辑）

```
[多来源采集]
     ↓
[哈希计算] → SHA-256 → 去重检查（哈希/路径/版本）
     ↓
[元数据提取] → 大小/类型/时间/作者/页数/来源
     ↓
[格式解析] → 多格式统一文本提取（含 ZIP 展开、大文件分片）
     ↓
[媒体证据] → sidecar/显式文本/OCR-ASR provider；无证据保持 P0 阻断
     ↓
[智能分类] → 内容规则 + 可扩展 LLM → 行业/文档类型/项目分类/标签/第一性原理摘要
     ↓
[版本检测] → 同名同哈希 → 版本链关联；内容变更 → 新版本
     ↓
[治理审查] → Wiki 目录映射/权限预检 → 敏感/冲突/质量 → 七类发布决策
     ↓
[发布门禁] → 自动模式低风险直发；高风险待审核 / gated 模式全部先盘点
     ↓
[权限治理] → 密级 L2-Internal → tenant_readable 组织内可见
     ↓
[飞书沉淀] → 上传原件 → 创建/更新标准 Docx 知识页 → 写入 Bitable 双表
     ↓
[统一验收] → 知识页/来源/治理表/正文/权限/回读证据必须一致
     ↓
[关系与运维] → 知识关系图 → Dashboard → 到期飞书任务 → SQLite 审计
```

---

## 数据架构（知识页 + 双通道 + 关系图）

### 0. 标准知识页（正式知识成品）

生产就绪资料可生成飞书 Docx，统一包含治理概览、结构化正文、来源、例外和变更记录。原始文件保留为来源证据，知识页创建后必须回读验证；未验证成功不计为发布完成。

### 1. 知识材料表（给人看）

飞书多维表格「知识材料」表字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 文件名 | 文本 | 原始文件名 |
| 分类 | 单选 | 项目 taxonomy；无项目规则时回退为行业领域 |
| 子分类 | 单选 | 项目子分类；无项目规则时回退为文档类型 |
| 标签 | 多选 | 自动生成的关键词标签 |
| 摘要 | 文本 | 第一性原理概括，适配注意力机制 |
| 文件类型 | 单选 | docx/pptx/xlsx/pdf/zip/图片等 |
| 文件大小 | 数字 | KB |
| 来源 | 单选 | 微信/收件箱/本地目录 |
| 来源会话 | 文本 | 来自哪个聊天/目录 |
| 直达链接 | URL | 飞书云空间 `/file/<token>` 可点击链接 |
| 协作状态 | 单选 | 待审核/已确认/需补充/已归档 |
| 人工标签 | 多选 | 人工补充标签 |
| 协作备注 | 文本 | 行业/类型、敏感等级、质量评分、复核优先级与人工备注 |
| 审核结论 | 文本 | production_ready 或待人工审核 |
| 版本号 | 数字 | 文件版本 |
| 父文件 | 文本 | 压缩包子文件关联父包 |
| 密级 | 单选 | L1/L2/L3/L4（默认 L2-Internal） |
| 同步状态 | 单选 | 待处理/解析中/已完成/失败/已跳过 |
| 处理时间 | 日期时间 | 自动记录 |

### 2. Agent 上下文窗口（给 Agent 看）

结构化 JSON 记录，按 5 类 Context Key 组织：

- **Project**: 项目元信息、知识边界
- **Coverage**: 已治理文件覆盖统计
- **Taxonomy**: 分类体系及各类别材料数量
- **Knowledge Record**: 每个文件的结构化摘要+直达链接
- **Governance Rule**: 治理规则、分类标准、权限策略

### 3. 知识关系图与 Dashboard

可视化面板（黑白灰 SpaceX/iOS 风格）：

- 指标卡：材料总数、分类数、覆盖率、最新同步时间
- 分类环图：各分类占比
- 文件类型条形图：格式构成
- 识别覆盖状态饼图：成功/跳过/失败
- 协作进度条：待审核/已确认/已归档
- 关系图：项目→分类→知识页→原始来源，以及版本 supersedes 关系

飞书看板直达：配置文件 `feishu.bitable.url` 字段。

---

## 目录结构

```
file-governance/
├── SKILL.md                    # 本文件（Skill 入口）
├── config.yaml                 # 默认配置（复制为 config.local.yaml 本地覆盖）
├── requirements.txt            # Python 依赖
├── src/
│   ├── cli.py                  # CLI 命令入口
│   ├── pipeline.py             # 治理流水线编排
│   ├── collectors/             # 多来源采集适配器
│   │   ├── base.py             # 采集器基类（接口定义）
│   │   ├── wechat.py           # 微信本地目录
│   │   ├── inbox.py            # 本地收件箱
│   │   ├── local_folder.py     # 指定本地目录
│   │   └── url_fetch.py        # URL/本地路径抓取
│   ├── processors/             # 处理模块
│   │   ├── hasher.py           # 哈希计算
│   │   ├── metadata.py         # 元数据提取
│   │   ├── parser.py           # 多格式解析
│   │   ├── media.py            # 媒体证据、sidecar、时间码
│   │   ├── classifier.py       # LLM/规则分类
│   │   └── dedup.py            # 去重引擎
│   ├── governance/             # 治理模块
│   │   ├── version.py          # 版本控制
│   │   ├── permission.py       # 权限治理（密级+共享）
│   │   ├── sensitivity.py      # 敏感信息脱敏初筛
│   │   ├── quality.py          # 三维质量评分
│   │   ├── planner.py          # 发布计划与问题清单
│   │   ├── wiki_catalog.py     # 授权 Wiki 子树台账与节点映射
│   │   ├── acceptance.py       # 统一发布验收
│   │   └── audit.py            # 审计日志
│   ├── publishers/             # 发布模块
│   │   ├── feishu_drive.py     # 飞书云空间上传
│   │   ├── feishu_docx.py      # 标准知识页创建/更新与回读
│   │   ├── feishu_bitable.py   # 多维表格双表写入
│   │   ├── feishu_tasks.py     # 到期复核任务
│   │   ├── knowledge_graph.py  # 关系图与可选飞书画板
│   │   └── reporter.py         # 结果卡片生成
│   ├── models/                 # 数据模型
│   │   └── file_record.py      # 文件记录模型
│   └── utils/                  # 工具
│       ├── config.py           # 配置加载
│       ├── db.py               # SQLite 本地存储
│       └── logger.py           # 日志
├── scripts/                    # 运维/工具脚本
│   ├── start_inbox.py          # 启动收件箱 Web UI
│   ├── repair_links.py         # 修复文件链接
│   ├── set_permissions.py      # 批量权限治理
│   └── refresh_all.py          # 全量刷新
├── config/                     # 配置模板
│   └── config.example.yaml     # 完整配置示例
├── templates/                  # 模板
│   └── inbox.html              # 收件箱页面
├── data/                       # 运行时数据
│   └── governance.db           # SQLite 数据库
├── inbox/                      # 收件箱拖拽目录
└── tests/                      # 治理门禁自动化测试
```

---

## 配置说明

核心配置项（`config.yaml`）：

```yaml
sources:
  wechat:
    enabled: true
    base_path: "~/Library/Containers/com.tencent.xinWeChat/..."
    fixed_chat_path: ""
  inbox:
    enabled: true
    path: "./inbox"
    web_ui:
      enabled: true
      host: "127.0.0.1"
      port: 8765
  local_folders: []  # 额外监控的本地目录列表
  url_fetch:
    enabled: false
    timeout: 60
    urls: []

feishu:
  provider: "cli"  # 使用 lark-cli OAuth，不存密钥
  drive:
    root_folder: "知识沉淀"
  bitable:
    base_token: ""
    knowledge_table_id: ""
    agent_context_table_id: ""
    dashboard_id: ""
  wiki:
    enabled: false
    space_id: ""
    parent_node_token: ""

knowledge_pages:
  enabled: false
  require_confirmation: true

review_tasks:
  enabled: false
  owner_open_id: ""

knowledge_graph:
  enabled: false
  whiteboard_token: ""

governance:
  default_security_level: "L2-Internal"
  default_share_permission: "tenant_readable"
  enable_versioning: true
  dedup_strategy: "hash+path"  # hash / path / hash+path
  max_file_size_mb: 500
  publication_mode: "auto"  # auto / gated
  quality_threshold: 75
  block_on: ["high_sensitivity", "name_conflict", "unparseable"]

taxonomy:
  categories: [...]  # 分类体系，可按项目自定义
```

首次使用：复制 `config/config.example.yaml` 为 `config.yaml`，填写飞书 Bitable 配置，运行 `python3 src/cli.py run` 即可。

---

## 飞书权限依赖

通过 `lark-cli` 使用用户 OAuth 身份，需以下 scope：

- `drive:drive` — 云空间文件读写
- `base:app` — 多维表格记录读写
- `base:record:delete` — 记录删除（全量刷新需要）
- `drive:secure_label:update` — 密级设置
- `drive:permission:update` — 共享权限设置
- `docs:permission.setting:write_only` — 更新组织内链接访问策略
- `docx:document:create` — 创建标准知识页（启用 knowledge_pages 时）
- `docx:document:write_only` — 更新已有标准知识页
- `task:task:write` — 创建复核任务（启用 review_tasks 时）
- `board:whiteboard:node:create` — 同步知识关系图到飞书画板

授权方式：已通过 `lark-cli auth login` 登录的设备自动复用，新增权限时 CLI 自动弹出设备码授权。

---

## 与旧版 wx-feishu-kb 的关系

本 Skill 是 `wx-feishu-kb` v0.1 MVP 的产品化升级：

- 更规范的目录结构和模块划分（pipelines/collectors/processors/governance/publishers）
- 完整的可插拔 Source Adapter 接口，方便扩展新来源
- 内置版本控制模块（version chain）
- 内置权限自动治理（密级+tenant_readable 流水线步骤）
- 审计日志模块（全链路追溯）
- 更完善的 CLI 命令体系
- 面向 Agent 的标准 Context Key 格式上下文窗口
- 产品级文档（本 SKILL.md）

旧数据（SQLite、飞书 Bitable）可无缝迁移。

---

## 设计原则（第一性原理）

1. **表层极简**：用户只需拖文件或说一句话，零配置启动
2. **底层透明**：所有治理动作可追溯、可审计、可回滚
3. **双通道输出**：同一数据源同时服务人类协作（表格+Dashboard）和机器消费（结构化JSON）
4. **不存密钥**：复用 `lark-cli` OAuth 登录态，无应用密钥泄漏风险
5. **增量优先**：默认增量处理，全量刷新作为显式命令
6. **权限自动治理**：文件上传即自动设置合理密级和内部可见权限，避免人工疏漏
7. **可扩展架构**：Source Adapter / Processor / Publisher 均可插拔扩展
8. **先盘点再发布**：批量或高风险范围先输出资料台账与问题清单，确认后再写飞书
9. **最小阶段执行**：盘点、发布、质量审核和增量维护可独立执行，避免每次重跑完整流程
10. **AI 审核不替代业务审核**：三维评分用于排序与发现风险，P0 项必须人工确认
11. **证据先于结论**：媒体没有 sidecar/OCR/ASR 证据、知识页未回读、权限命令失败时，不得标记发布成功
12. **不虚构常驻能力**：只创建飞书复核任务和提醒，到期后由负责人或 Agent 再次调用
