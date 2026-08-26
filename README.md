<p align="center">
  <h1 align="center">📁 File Governance Skill</h1>
  <p align="center">
    <strong>企业级文件与数据治理 Skill — 飞书知识沉淀</strong>
  </p>
  <p align="center">
    表层操作极简 · 底层逻辑复杂 · 双通道输出（人类协作 + Agent 上下文）
  </p>
</p>

---

## 一句话概述

拖入文件或说一句话，自动完成 **收集 → 哈希去重 → 元数据提取 → 格式解析 → 智能分类 → 版本控制 → 权限治理 → 飞书沉淀** 全链路治理，输出「人类协作看板 + Agent 结构化上下文 + Dashboard 观察台」三重产物。

## 核心能力

| | |
|---|---|
| 🔌 **多来源采集** | 微信本地目录、拖拽收件箱、任意本地文件夹，可插拔 Source Adapter 接口 |
| 📄 **多格式解析** | docx / doc (textutil) / xlsx / xls (xlrd) / pptx / pdf / txt / md / csv / json / zip / 图片元数据 |
| 🏷️ **智能分类** | 规则 + 可扩展 LLM 双引擎，自动打标签、生成第一性原理摘要 |
| 🔍 **三级去重** | SHA-256 精确哈希 + 路径去重 + 文件名匹配 |
| 📚 **版本控制** | 同名同内容文件自动版本链追踪，历史可追溯 |
| 🔐 **权限自动治理** | 上传即设置 L2-Internal 密级 + tenant_readable（组织内可见） |
| 📊 **双通道沉淀** | 飞书多维表格「知识材料」表（人类协作）+「Agent上下文窗口」表（机器消费） |
| 📈 **Dashboard 观察台** | 指标卡、分类环图、文件类型构成、协作进度（黑白灰 iOS/SpaceX 风格） |
| 📝 **全链路审计** | 每步操作记录 audit log，可追溯可回滚 |

## 快速开始

### 前置依赖

- Python 3.10+
- [lark-cli](https://github.com/larksuite/cli)（`npm i -g @larksuite/cli`）并已通过 `lark-cli auth login` 登录
- 飞书企业账号（需创建一个多维表格 Base）

### 安装

```bash
# 将 Skill 放置到 TRAE 技能目录
mkdir -p ~/.trae/skills
git clone https://github.com/liuanye9-lab/file-governance-skill.git ~/.trae/skills/file-governance

# 安装 Python 依赖
cd ~/.trae/skills/file-governance
pip3 install -r requirements.txt
```

### 配置

1. 运行初始化命令：
   ```bash
   python3 src/cli.py init
   ```
2. 编辑 `config.yaml`，填入飞书 Bitable 配置：
   ```yaml
   feishu:
     bitable:
       base_token: "你的 Base Token"
       knowledge_table_id: "知识材料表 ID"
       agent_context_table_id: "Agent上下文窗口表 ID"
       url: "Bitable 完整 URL"
   knowledge:
     project_name: "你的项目名"
   ```

### 使用

**对话触发**（在 TRAE 中说）：
> 沉淀知识 / 整理文件到飞书 / sync files

**命令行**：
```bash
python3 src/cli.py run                    # 增量扫描治理（默认）
python3 src/cli.py run --source inbox     # 仅处理收件箱
python3 src/cli.py refresh                # 全量刷新（清空重跑）
python3 src/cli.py govern-permissions     # 批量治理文件权限
python3 src/cli.py stats                  # 查看治理统计
python3 scripts/start_inbox.py            # 启动拖拽收件箱 Web UI (127.0.0.1:8765)
```

## 架构

```
[多来源采集层]
    wechat / inbox / local_folder（可插拔 Collector 接口）
        ↓
[处理流水线 (Processors)]
    哈希计算 → 元数据提取 → 去重检查 → 格式解析 → 智能分类
        ↓
[治理层 (Governance)]
    版本控制 → 权限自动治理（密级+共享）→ 审计日志
        ↓
[发布层 (Publishers)]
    飞书云空间上传 → 知识材料表写入 → Agent上下文窗口同步 → Dashboard刷新
        ↓
[输出]
    ├── 知识材料表（人类：分类/标签/摘要/协作状态/直达链接/审核结论）
    ├── Agent上下文窗口（机器：Project/Coverage/Taxonomy/Knowledge/Governance Rule 五类结构化 JSON）
    └── Dashboard 观察台（指标卡/环图/条形图/进度条）
```

## 目录结构

```
file-governance/
├── SKILL.md                    # Skill 入口文档
├── README.md                   # 仓库首页
├── config.yaml                 # 用户配置（git 忽略，通过 init 生成）
├── requirements.txt            # Python 依赖
├── config/config.example.yaml  # 配置模板
├── src/
│   ├── cli.py                  # CLI 命令入口
│   ├── pipeline.py             # 治理流水线编排
│   ├── models/                 # 数据模型
│   ├── collectors/             # 多来源采集器（可插拔）
│   ├── processors/             # 处理模块（哈希/元数据/解析/分类/去重）
│   ├── governance/             # 治理模块（版本/权限/审计）
│   ├── publishers/             # 发布模块（飞书 Drive/Bitable/卡片）
│   └── utils/                  # 工具（配置/数据库/日志）
├── scripts/start_inbox.py      # 收件箱 Web UI
├── templates/inbox.html        # 收件箱页面模板
├── data/                       # 运行时 SQLite（git 忽略）
└── inbox/                      # 拖拽收件目录（git 忽略）
```

## 设计原则（第一性原理）

1. **表层极简**：用户只需拖文件或说一句话，零配置启动
2. **底层透明**：所有治理动作可追溯、可审计、可回滚
3. **双通道输出**：同一数据源同时服务人类协作（表格+Dashboard）和机器消费（结构化JSON）
4. **不存密钥**：复用 `lark-cli` OAuth 登录态，无应用密钥泄漏风险
5. **增量优先**：默认增量处理，全量刷新作为显式命令
6. **权限自动治理**：文件上传即自动设置合理密级和内部可见权限
7. **可扩展架构**：Source Adapter / Processor / Publisher 均可插拔扩展

## 飞书多维表格 Schema

### 知识材料表（给人看）

| 字段 | 类型 | 说明 |
|------|------|------|
| 文件名 | 文本 | 原始文件名 |
| 分类 | 单选 | 运营SOP/制度与安全/培训与服务标准/客户体验/经营文化/待复核 |
| 子分类 | 单选 | 二级分类 |
| 标签 | 多选 | 自动生成关键词 |
| 摘要 | 文本 | 第一性原理概括 |
| 文件类型 | 单选 | docx/pptx/xlsx/pdf/zip/image... |
| 文件大小 | 数字 | KB |
| 来源 | 单选 | wechat/inbox/local_folder |
| 直达链接 | URL | `/file/<token>` 可点击链接 |
| 协作状态 | 单选 | 待审核/已确认/需补充/已归档 |
| 人工标签 | 多选 | 人工补充 |
| 协作备注/审核结论 | 文本 | 人工审核 |
| 版本号 | 数字 | 文件版本 |
| 父文件 | 文本 | 压缩包子文件关联 |
| 密级 | 单选 | L1/L2/L3/L4 |
| 同步状态 | 单选 | 待处理/已完成/失败/跳过 |
| 处理时间 | 日期时间 | 自动 |

### Agent 上下文窗口（给 Agent 看）

| Context Key 类型 | 说明 |
|-----------------|------|
| `project:*` | 项目元信息、知识边界 |
| `coverage:*` | 已治理文件覆盖统计 |
| `taxonomy:*` | 分类体系定义 |
| `knowledge:*` | 每个文件的结构化摘要+直达链接 |
| `governance:*` | 治理规则与策略 |

## License

MIT
