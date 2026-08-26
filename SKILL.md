---
name: "file-governance"
description: "企业级文件与数据治理 Skill：从微信/本地/多来源自动收集文件，经格式转换、元数据提取、分类标签、去重版本控制后安全沉淀到飞书多维表格+云空间。Invoke when user says '沉淀知识'/'治理文件'/'整理资料'/'sync files'/'governance'，或需要将分散文件统一归档到飞书知识库时。"
---

# File Governance · 企业级文件与数据治理 Skill

## 一句话概述

**表层操作极简，底层逻辑复杂** — 拖入文件或说一句话，自动完成收集→解析→分类→去重→版本→权限→飞书沉淀全链路治理，输出「人类协作看板 + Agent 上下文窗口 + Dashboard 观察台」三重产物。

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
| **多来源收集** | 微信本地目录扫描、本地收件箱拖拽、指定文件夹监控、可插拔适配器接口 | ✅ 核心可用 / 🔌 扩展中 |
| **格式解析** | docx/pptx/xlsx/pdf/txt/md/csv/json/zip/图片元数据；doc 通过 textutil；xls 通过 xlrd | ✅ |
| **智能分类** | LLM + 规则双引擎分类，自动打标签、生成第一性原理摘要 | ✅ |
| **去重引擎** | SHA-256 精确哈希 + 路径去重 + 文件名相似度三级策略 | ✅ |
| **版本控制** | 同名/同内容文件版本链追踪，版本历史可追溯 | ✅ |
| **元数据提取** | 文件大小、类型、创建/修改时间、作者、页数、来源会话 | ✅ |
| **权限治理** | 自动设置密级（L2-Internal）+ tenant_readable（组织内可见） | ✅ |
| **双通道沉淀** | 飞书多维表格「知识材料」表（人类协作）+「Agent上下文窗口」表（机器消费） | ✅ |
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

# 全量刷新（清空重跑）
python3 src/cli.py refresh

# 权限治理（批量设密级+内部可见）
python3 src/cli.py govern-permissions

# 修复文件链接
python3 src/cli.py fix-links
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
[智能分类] → LLM + 规则双引擎 → 分类/子分类/标签/第一性原理摘要
     ↓
[版本检测] → 同名同哈希 → 版本链关联；内容变更 → 新版本
     ↓
[权限治理] → 密级 L2-Internal → tenant_readable 组织内可见
     ↓
[飞书沉淀] → 上传 Drive → 写入知识材料表 → 同步 Agent 上下文窗口
     ↓
[Dashboard 刷新] → 指标卡/环图/条形图自动更新
     ↓
[审计记录] → 本地 SQLite 记录全链路状态
```

---

## 数据架构（双通道 + Dashboard）

### 1. 知识材料表（给人看）

飞书多维表格「知识材料」表字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 文件名 | 文本 | 原始文件名 |
| 分类 | 单选 | 运营SOP/制度与安全/培训与服务标准/客户体验/经营文化/待复核 |
| 子分类 | 单选 | 二级分类 |
| 标签 | 多选 | 自动生成的关键词标签 |
| 摘要 | 文本 | 第一性原理概括，适配注意力机制 |
| 文件类型 | 单选 | docx/pptx/xlsx/pdf/zip/图片等 |
| 文件大小 | 数字 | KB |
| 来源 | 单选 | 微信/收件箱/本地目录 |
| 来源会话 | 文本 | 来自哪个聊天/目录 |
| 直达链接 | URL | 飞书云空间 `/file/<token>` 可点击链接 |
| 协作状态 | 单选 | 待审核/已确认/需补充/已归档 |
| 人工标签 | 多选 | 人工补充标签 |
| 协作备注 | 文本 | 人工备注 |
| 审核结论 | 文本 | 人工审核结论 |
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

### 3. Dashboard 观察台

可视化面板（黑白灰 SpaceX/iOS 风格）：

- 指标卡：材料总数、分类数、覆盖率、最新同步时间
- 分类环图：各分类占比
- 文件类型条形图：格式构成
- 识别覆盖状态饼图：成功/跳过/失败
- 协作进度条：待审核/已确认/已归档

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
│   │   └── local_folder.py     # 指定本地目录
│   ├── processors/             # 处理模块
│   │   ├── hasher.py           # 哈希计算
│   │   ├── metadata.py         # 元数据提取
│   │   ├── parser.py           # 多格式解析
│   │   ├── classifier.py       # LLM/规则分类
│   │   └── dedup.py            # 去重引擎
│   ├── governance/             # 治理模块
│   │   ├── version.py          # 版本控制
│   │   ├── permission.py       # 权限治理（密级+共享）
│   │   └── audit.py            # 审计日志
│   ├── publishers/             # 发布模块
│   │   ├── feishu_drive.py     # 飞书云空间上传
│   │   ├── feishu_bitable.py   # 多维表格双表写入
│   │   ├── dashboard.py        # Dashboard 刷新
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
└── inbox/                      # 收件箱拖拽目录
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

feishu:
  provider: "cli"  # 使用 lark-cli OAuth，不存密钥
  drive:
    root_folder: "知识沉淀"
  bitable:
    base_token: ""
    knowledge_table_id: ""
    agent_context_table_id: ""
    dashboard_id: ""

governance:
  default_security_level: "L2-Internal"
  default_share_permission: "tenant_readable"
  enable_versioning: true
  dedup_strategy: "hash+path"  # hash / path / hash+path
  max_file_size_mb: 500

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
