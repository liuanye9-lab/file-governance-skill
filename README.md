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

拖入文件或说一句话，自动完成 **收集 → 去重 → 解析 → 分类 → 风险/质量审核 → 目录映射 → 权限治理 → 原件归档 → 标准知识页 → 双表沉淀 → 回读验收 → 持续复核** 全链路治理。

## 核心能力

| | |
|---|---|
| 🔌 **多来源采集** | 微信本地目录、拖拽收件箱、任意本地文件夹、URL/本地路径抓取，可插拔 Source Adapter 接口 |
| 📄 **多格式解析** | docx / doc (textutil) / xlsx / xls (xlrd) / pptx / pdf / txt / md / csv / json / zip / 图片元数据 |
| 🏷️ **跨行业分类** | 行业领域 × 文档类型双轴分类，项目 taxonomy 优先、通用维度兜底，生成第一性原理摘要 |
| 🔍 **三级去重** | SHA-256 精确哈希 + 路径去重 + 文件名匹配 |
| 📚 **版本控制** | 同名同内容文件自动版本链追踪，历史可追溯 |
| 🛡️ **发布前治理** | 只读盘点、同名冲突、敏感信息脱敏初筛、无法解析清单、确认后发布 |
| 🗂️ **Wiki 目录接管** | 仅扫描显式授权的 Wiki 子树，生成节点台账并映射分类目标，不扫描全企业飞书 |
| 🧭 **七类发布决策** | 新增、更新、合并、拆分、引用、待确认、不入库，逐项输出可执行状态与原因 |
| ✅ **三维质量审核** | 内容可靠、易找易懂、易维护三维评分，输出 production_ready 与 P0/P1/P2 建议 |
| 🖼️ **媒体证据治理** | 图片/音视频支持 sidecar 或显式注入文字、视频时间码；无 OCR/ASR 证据时保持阻断 |
| 🔐 **权限自动治理** | 上传即设置 L2-Internal 密级 + tenant_readable（组织内可见） |
| 📖 **标准知识页** | 将生产就绪内容生成为飞书 Docx 治理页，支持创建/更新、脱敏和创建后回读验证 |
| 📊 **双通道沉淀** | 飞书多维表格「知识材料」表（人类协作）+「Agent上下文窗口」表（机器消费） |
| 🕸️ **知识关系图** | 生成来源、分类、知识页与版本关系；可显式同步到已有飞书画板 |
| 🧪 **统一发布验收** | 同时检查知识页、原始来源、治理表、正文、生产就绪、权限和回读证据 |
| ⏰ **复核任务闭环** | 到期清单可幂等创建飞书任务，回写负责人、任务 GUID、链接和提醒时间 |
| 📈 **Dashboard 观察台** | 指标卡、分类环图、文件类型构成、协作进度（黑白灰 iOS/SpaceX 风格） |
| 📝 **全链路审计** | 每步操作记录 audit log，可追溯可回滚 |

## 快速开始

### 前置依赖

- Python 3.9+
- [lark-cli](https://github.com/larksuite/cli)（`npm i -g @larksuite/cli`）并已通过 `lark-cli auth login` 登录
- 飞书企业账号（需创建一个多维表格 Base）
- 可选写能力需单独授权：`docx:document:create`、`docx:document:write_only`、`task:task:write`、`board:whiteboard:node:create`

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
     wiki:
       enabled: true
       space_id: "目标知识空间 ID"
       parent_node_token: "明确授权的目标子树节点"
   knowledge_pages:
     enabled: true
     require_confirmation: true
   review_tasks:
     enabled: false
     owner_open_id: "负责人 open_id"
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
python3 src/cli.py fetch "https://..."    # 按 URL/路径抓取并治理
python3 src/cli.py plan --source inbox    # 只读盘点，生成台账与发布计划
python3 src/cli.py plan --url "https://..." # 盘点指定 URL，可重复 --url
python3 src/cli.py publish --yes           # 发布已确认的计划
python3 src/cli.py publish --yes --approve-risk # 放行冲突/无法解析项；高敏仍拦截
python3 src/cli.py review-due              # 查看已到期的知识复核清单
python3 src/cli.py review-due --create-tasks # 为到期项创建飞书复核任务
python3 src/cli.py quality-review          # 独立重跑三维质量审核，不修改正文
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
    哈希计算 → 元数据提取 → 去重检查 → 格式解析/媒体证据 → 智能分类
        ↓
[治理层 (Governance)]
    Wiki 子树盘点 → 目录映射 → 版本/敏感/冲突 → 三维质量评分 → 七类发布决策
        ↓
[发布门禁]
    自动模式：低风险自动发布，高敏/冲突/无法解析拦截
    审核模式：plan 只读盘点 → 人工确认 → publish 发布
        ↓
[发布层 (Publishers)]
    权限治理 → 原件上传 → 标准 Docx 知识页 → Bitable 双表 → 回读验收
        ↓
[输出]
    ├── 知识材料表（人类：分类/标签/摘要/协作状态/直达链接/审核结论）
    ├── Agent上下文窗口（机器：Project/Coverage/Taxonomy/Knowledge/Governance Rule 五类结构化 JSON）
    ├── 标准知识页（治理概览/结构化正文/来源/例外/变更记录）
    ├── 知识关系图（本地 nodes/edges，可选同步飞书画板）
    └── Dashboard 观察台与飞书复核任务
```

## 治理运行模式

系统吸收了企业知识库治理中“盘点、确认、发布、审核、运维”的控制思路，并与本项目的跨行业分类和 Bitable 双通道结合：

- **自动模式（默认）**：低风险资料自动沉淀；高敏、同名冲突、无法解析项进入待审核队列。
- **门禁模式**：配置 `publication_mode: gated` 后，所有资料先执行 `plan`，确认后再执行 `publish --yes`。
- **质量审核**：每份资料按内容可靠、易找易懂、易维护三维评分，生成 `production_ready` 和 P0/P1/P2 优先级。
- **持续运维**：按文档类型自动安排 30/90/180 天复核周期，使用 `review-due` 获取到期清单。
- **正式知识页**：显式启用 `knowledge_pages.enabled` 后，默认要求先确认计划，再创建或更新知识页并回读验收。
- **媒体边界**：未配置 OCR/ASR provider 且不存在可追溯 sidecar/注入文本时，不生成伪正文，资料保持 P0 阻断。

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
│   ├── processors/             # 处理模块（哈希/元数据/解析/媒体/分类/去重）
│   ├── governance/             # 治理模块（版本/权限/Wiki目录/质量/验收/审计）
│   ├── publishers/             # 发布模块（Drive/Docx/Bitable/任务/关系图/卡片）
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
8. **先盘点再发布**：批量或高风险任务先生成台账、冲突/敏感/无法解析清单，不把“处理完成”混同于“可生产使用”
9. **AI 审核不替代业务审核**：评分用于排序和发现问题，P0 风险项必须由人确认
10. **不虚构后台能力**：Skill 只创建可追踪的任务和提醒；到期后由负责人或 Agent 再次调用执行复核

## 飞书多维表格 Schema

### 知识材料表（给人看）

| 字段 | 类型 | 说明 |
|------|------|------|
| 文件名 | 文本 | 原始文件名 |
| 分类 | 单选 | 项目 taxonomy 分类；无项目规则时回退为行业领域 |
| 子分类 | 单选 | 项目子分类；无项目规则时回退为文档类型 |
| 标签 | 多选 | 自动生成关键词 |
| 摘要 | 文本 | 第一性原理概括 |
| 文件类型 | 单选 | docx/pptx/xlsx/pdf/zip/image... |
| 文件大小 | 数字 | KB |
| 来源 | 单选 | wechat/inbox/local_folder |
| 直达链接 | URL | `/file/<token>` 可点击链接 |
| 协作状态 | 单选 | 待审核/已确认/需补充/已归档 |
| 人工标签 | 多选 | 人工补充 |
| 协作备注/审核结论 | 文本 | 行业/类型、敏感等级、质量评分、production_ready 与人工审核 |
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
