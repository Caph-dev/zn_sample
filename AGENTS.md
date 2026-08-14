# zn_sample · AGENTS.md

样品申请筛查：在 TikTok Shop **联盟中心 → 样品申请管理 → 待审核** 列表上，按 SOP 规则筛达人，导出 xlsx + csv + json。

业务 SOP 原文：`样品申请筛查sop/样品申请筛查sop.md`。

---

## 紫鸟浏览器（先读姊妹仓）

本仓**不**重复紫鸟客户端启停、GUI / WEBDRIVER、WebDriver 端口与 `ziniao-cli` 安装细节。  
做任何依赖紫鸟浏览器的操作前，先读姊妹仓库：

**总文档：** [`../zn_daren/AGENTS.md`](../zn_daren/AGENTS.md)

| 主题 | 链接 |
|------|------|
| 本机安装清单（`ziniao-gui` / `status` / `webdriver` 链到 `~/bin`） | [本机安装清单](../zn_daren/AGENTS.md#本机安装清单) |
| GUI vs WEBDRIVER 模式（互斥） | [模式说明](../zn_daren/AGENTS.md#模式说明) |
| 一键命令（`ziniao-status` / `ziniao-gui` / `ziniao-webdriver`） | [一键命令（优先用这些）](../zn_daren/AGENTS.md#一键命令优先用这些) |
| WebDriver 连通与开店（`16851`、`check_webdriver.py`） | [WebDriver 连通与开店](../zn_daren/AGENTS.md#webdriver-连通与开店) |
| 官方 CLI / ZClaw Bridge（`ziniao-cli`、`9481`） | [官方 CLI（可选另一条线）](../zn_daren/AGENTS.md#官方-cli可选另一条线) |
| Agent 操作纪律（改自动化前先看模式） | [Agent 操作纪律](../zn_daren/AGENTS.md#agent-操作纪律) |
| 紫鸟故障速查 | [故障速查](../zn_daren/AGENTS.md#故障速查) |

本仓样品筛查默认在 **GUI + 已 open 的店铺页** 上跑；扫表 / 详情 DOM 约定见下文「页面与 DOM 约定」。  
`zn_daren` 里的「定向合作清理」与本仓业务无关，勿混用其 `--execute` 取消逻辑。

---

## 非协商纪律（先读）

1. **默认禁止审批写操作**  
   - **默认**绝不点击：「同意」「批准」「Approve」「拒绝」「Reject」「发货」、私信「发送」。  
   - 默认只做：**读列表 / 读达人详情 / 本地判定 / 导出名单**。  
   - **例外（危险）**：仅当 CLI 同时带 `--execute --yes` 时，可对**筛查通过**行点「同意」（永不点拒绝），或发送 SOP 第 6 / 第 9 步私信。  
   - 测试环境默认 **不写飞书**；写「达人关系管理(新)」须再加 `--write-feishu`。  
   - 默认 `--execute-limit 1`；批准前写本地备份；平台「同意」与已发私信不可脚本撤销。

2. **导航采用显式两阶段流程**
   - 脚本 1 `open_sample_store.py` 只用 GUI + ZClaw 开店；目标店已运行时绝不关闭或重开。用户随后登录并停在商家中心首页。
   - 脚本 2 仅在显式 `--from-seller-home --store-id <id>` 时，可从商家中心首页安排页面内延迟导航到样品申请并切「待审核」；先返回脚本结果再跳转，避免页面销毁造成 Bridge 假 network。
   - 未带 `--from-seller-home` 时保持旧纪律：用户须先停在 **样品申请 → 待审核**，脚本不代进入口。
   - 禁止用 `ziniao-cli page extract --mode running` 识别店铺；它可能因无 debug port 触发 `runtime.reopen`。统一用 ZClaw `extract_data(mode=running)`。
   - 导航失败只报错退出，不关闭、不重开、不切换店铺。

3. **测试环境默认 1 号店**  
   - 店名：`跨境1号店（Lingerie Outlet）`  
   - `storeId`：`27437742526069`  
   - CLI 未传 `--store-id` / `--store-name` 且无法从 running 唯一解析时，可用该默认。  
   - 生产/多店：必须显式 `--store-id` 或 running 唯一；可用 `--no-default-store` 禁用默认。

4. **飞书多维表格默认「达人关系管理(新)」→ 视图「达人管理总表」**
   - 本项目涉及 **达人建联 / 达人关系** 的飞书读写，**默认**落在 Base「内部达人建联记录表」→ 数据表 **「达人关系管理(新)」**（`tblWT2SRKJ3CEZ5e`）→ 视图 **「达人管理总表」**（`vewNtqmTk4`）。  
   - 同 Base 另有旧表「达人关系管理」（`tbl1Sc97Gkpc96xI`）；**不要**默认写旧表，除非用户显式指定。  
   - **主推款**（SOP 条件 2）仍用知识库电子表格 `tk产品图+货号`（见下文「条件 2」），与达人关系表不是同一张。
   - 新建达人关系记录时，单选字段 **「人员」固定写「王良希（技术）」**；初始「合作状态=待发货」「是否已寄样=否」。
   - 写入 TikTok 物流单号后，勾选「是否已寄样」，并将合作状态从「待发货」改为「待发布」；不得把「已发布」等后续状态回退。
   - 标识与链接见下文「飞书数据源」。

5. **只改任务相关代码**；密钥、`apiKey`、账号密码 **不得**写入本文件或提交到 git。

6. **默认中文**沟通；代码标识符、CLI、错误原文可保留英文。

---

## Agent 维护范围与当前能力边界

本文件面向 Agent 和技术维护，不承担业务员逐条操作教程；复制命令、登录处理和日常运行步骤见 [使用方法.md](./使用方法.md)，项目概览见 [README.md](./README.md)。

当前能力必须按以下边界理解：

| 能力 | 当前实现 | 不可误解为 |
|---|---|---|
| 自动进入待审核 | `open_sample_store.py` + `--from-seller-home` 两阶段 GUI + ZClaw 流程 | 脚本不会代办登录、验证码，也不会擅自切换店铺 |
| 初筛 + 复筛 | 列表初判后，对目标达人读取详情再重判；正式要求 `--with-detail --require-detail` | 不能省略详情并把仅列表结果当正式名单 |
| 申请批准 | `--execute --yes` 保护；默认使用已捕获的窄 API，也可显式 `--write-source dom` 使用页面路径；默认 `execute-limit=1` | API 路径仍未用第二条真实申请重复执行验收；不能猜 endpoint、扩大接口或绕过门闩 |
| 飞书达人关系写入 | 批准状态确认成功后，显式 `--write-feishu` 写「达人关系管理(新)」 | 不是主推款数据源，也不能在批准未知时先写 |
| 介绍/物流私信 | 独立脚本，固定话术、语言判定和发送门闩 | 批准后不会自动发送，第 7 步也不会自动触发第 8/9 步 |
| 物流同步 | 独立脚本；北京时间 16:00 前拒绝运行，测试才可 `--force` | `main_order_id` 不是第 9 步发送的物流单号 |

只读 API 已覆盖待审核列表、达人详情、已发货列表和商家订单物流。`auto` 是日常只读推荐：API 失败时回退 DOM；`shadow` 以 DOM 为最终权威；`api` 失败即报错。样品批准 API 已根据一次真实 DOM 请求实现为 allowlist 窄接口，批准默认改为 API；`--write-source dom` 仅作为显式备用路径。第 6/9 步私信默认调用页面内 IM SDK API；DOM 仅为显式备用路径。

## 项目目标

| 输入 | 处理 | 输出 |
|------|------|------|
| 待审核列表（用户已打开） | SOP 条件 1 列表指标 | `exports/*.csv` + `*.xlsx` + `*.json` |
| 达人详情页（正式必拉） | 视频/直播 GPM、均播、互动等 | 写入同一导出；缺则不通过 |
| 飞书主推表 | 「是否主推=是」货号（API 只读） | 条件 2 过滤（正式必拉） |

SOP：满足条件的达人**整理成名单**；**默认**禁止同意。可选危险路径见 CLI `--execute --yes`（写飞书另加 `--write-feishu`）。

---

## 仓库结构

```text
zn_sample/
├── AGENTS.md                          # 本文件（Agent 约定）
├── README.md                          # 项目能力和边界概览
├── 使用方法.md                         # 非技术人员复制命令操作指南
├── scripts/
│   ├── screen_sample_requests.py      # CLI 入口（第 1–5 步）
│   ├── open_sample_store.py            # 脚本 1：GUI + ZClaw 开店（不重开已运行店）
│   ├── send_sample_intro.py           # 第 6 步介绍私信（默认不发送）
│   ├── sync_shipped_tracking.py       # 第 7–9 步物流回写 + 可选私信
│   └── lib/
│       ├── zclaw.py                   # ziniao-cli / execute_script 薄封装
│       ├── sample_dom.py              # 待审核列表扫表（假定已定位）
│       ├── page_api.py                # 页面上下文 API 传输（联盟/商家读与批准 allowlist）
│       ├── sample_api.py              # 待审核/已发货列表 API 适配
│       ├── sample_data_source.py      # dom/api/auto/shadow 编排
│       ├── sample_navigation.py       # 商家中心首页 → 样品申请待审核
│       ├── store_launcher.py          # running 校验 / 安全开店编排
│       ├── creator_api.py             # 达人 profile_types 详情 API 适配
│       ├── network_observer.py        # 批准/私信接口发现：被动脱敏网络摘要
│       ├── sample_write_api.py         # 已捕获的单条样品批准 API
│       ├── im_api.py                  # 页面内 IM SDK 文本发送窄适配器
│       ├── order_api.py               # 商家订单物流只读 API 适配器
│       ├── creator_detail.py          # 达人详情只读
│       ├── feishu_hero.py             # 飞书主推表只读（默认 wiki 链接）
│       ├── feishu_bitable.py          # 达人关系管理(新) 查重/写入（可选）
│       ├── approve_dom.py             # 列表展开 + 点「同意」（仅 execute）
│       ├── app_config.py              # config.toml / 环境变量合并
│       ├── hero_xlsx.py               # 已废弃（本地 xlsx 停用）
│       ├── filters.py                 # SOP 判定
│       ├── parse_metrics.py           # 金额/粉丝/百分比
│       └── export_util.py             # csv/xlsx/json
├── config.toml.example                # 飞书密钥模板（可提交）
├── config.toml                        # 本地密钥（gitignore，勿提交）
├── config/
│   └── hero_skus.example.txt          # 历史示例（正式流程不用）
├── exports/                           # 运行输出（可本地忽略敏感内容）
└── 样品申请筛查sop/
    ├── 样品申请筛查sop.md
    └── 图片和附件/                    # SOP 截图
```

### 飞书数据源

本仓用到两类飞书资源，**勿混用**：

| 用途 | 资源 | 默认位置 | 说明 |
|------|------|----------|------|
| **达人建联 / 达人关系（项目默认）** | 多维表格 Base | 表 **「达人关系管理(新)」** → 视图 **「达人管理总表」** | 见下表 ID；Agent / 新脚本未指定表时以此为准 |
| **主推款（SOP 条件 2）** | 知识库电子表格 | `tk产品图+货号` | 条件 2 专用；**不是**达人关系表 |

**默认 Base / 表（达人关系管理(新)）：**

| 项 | 值 |
|----|-----|
| Base 名称 | 内部达人建联记录表 |
| `app_token` | `CJXSbLIQWahB8esVOiscX7j1nLc` |
| 数据表名称 | **达人关系管理(新)** |
| `table_id` | **`tblWT2SRKJ3CEZ5e`** |
| **默认视图** | **达人管理总表**（`vewNtqmTk4`） |
| 打开链接 | `https://rsed6zggjt.feishu.cn/base/CJXSbLIQWahB8esVOiscX7j1nLc?table=tblWT2SRKJ3CEZ5e&view=vewNtqmTk4` |

同 Base 另有旧表「达人关系管理」（`tbl1Sc97Gkpc96xI`）等；**仅当用户/配置明确指定时**才读写那些表。

**主推表权威来源**（飞书 wiki 电子表格，频繁更新，**不再**用本地 xlsx）：

```text
https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc
```

姊妹项目（勿在本仓复制整套启停脚本，需要时引用）：

```text
../zn_daren/AGENTS.md          # 紫鸟 GUI / WEBDRIVER / ziniao-cli 约定
../zn_daren/scripts/lib/zclaw_dom.py
```

---

## 样品申请筛查（`screen_sample_requests.py`）

TikTok Shop **联盟中心 → 样品申请管理 → 待审核** 列表筛查脚本。列表和详情只读 API 已可用，日常推荐 `auto`；CLI 读路径默认仍为 `dom`，批准写路径默认使用 API，DOM 需显式指定。
规则对齐 `样品申请筛查sop/样品申请筛查sop.md`：默认输出通过名单；**默认禁止同意**。

| 路径 | 说明 |
|------|------|
| `scripts/screen_sample_requests.py` | CLI 入口（第 1–5 步） |
| `scripts/send_sample_intro.py` | 第 6 步：详情简介判语言 + 介绍私信 |
| `scripts/sync_shipped_tracking.py` | 第 7–9 步：已发货 → TikTok 物流 → 飞书 → 可选私信 |
| `scripts/lib/sample_dom.py` | 待审核扫表 / 翻页（假定页已定位） |
| `scripts/lib/shipped_dom.py` | 已发货扫表（读 `main_order_id`，DOM 备用） |
| `scripts/lib/order_dom.py` | 商家订单页只读抽 TikTok 物流单号（DOM 备用） |
| `scripts/lib/order_api.py` | 商家订单物流只读 GET（默认） |
| `scripts/lib/im_dom.py` | 达人消息打开会话 / 发送（仅 execute） |
| `scripts/lib/im_api.py` | 页面内 IM SDK 文本发送（第 6/9 步默认） |
| `scripts/lib/detect_lang.py` | 详情页简介 → 英语 / 西班牙语 |
| `scripts/lib/creator_api.py` | 达人详情 profile_types 2/3/4/5 API（筛查指标） |
| `scripts/lib/network_observer.py` | 批准接口发现用被动观察器；不主动发请求，不保存完整请求/响应 |
| `scripts/lib/creator_detail.py` | 达人详情只读（GPM、简介） |
| `scripts/lib/feishu_hero.py` | 飞书主推表只读（产品货号 / 是否主推） |
| `scripts/lib/feishu_bitable.py` | 达人关系管理(新)：查重 / 写入 / 回写物流 |
| `scripts/lib/approve_dom.py` | 全部展开 + 按 `apply_id` 点「同意」（仅 `--execute --yes`） |
| `scripts/lib/hero_xlsx.py` | 已废弃；调用会报错并提示改用飞书 |
| `scripts/lib/filters.py` | SOP 判定 |
| `exports/` | 默认导出目录（csv / json / xlsx） |

### 业务运行入口

业务员的完整复制命令、登录处理、输出说明和第 6–9 步操作见 [使用方法.md](./使用方法.md)。

Agent 只需在执行前确认：

1. 紫鸟 GUI 已启动且 `ziniao-cli doctor` 正常；
2. 脚本 1 与脚本 2 使用同一个显式 `store_id`；
3. 正式筛查存在 `[feishu].app_secret`，批准后写表另需 `[feishu.bitable]`；
4. 不把 `--max-rows`、`--skip-hero-check` 或缺少详情的结果当成正式名单。

### 核心 CLI 契约

| 参数 | 约束 |
|------|------|
| `--data-source dom|api|auto|shadow` | 控制列表/详情读路径；正式批准时仍必须为 `dom`，不控制批准写来源 |
| `--write-source dom|api` | 批准写路径；默认 `api`；`dom` 是显式页面备用路径，两者都需全部 execute 门闩 |
| `--from-seller-home --store-id <id>` | 允许从商家中心首页自动导航；失败不重开、不切店 |
| `--with-detail --require-detail` | 正式筛查必须同时使用；缺少核心详情指标不能通过 |
| `--execute --yes` | 唯一批准/真实发送门闩；缺一退出码 2 |
| `--execute-limit` | 写操作默认 1；不得通过新参数绕过上限 |
| `--write-feishu` | 批准确认成功后才写达人关系表；不能单独用于筛查脚本 |
| `--observe-approve-network` | 仅配合单条 execute 做被动观察，不重放、不登记未经确认的 endpoint |
| `--detail-limit` | 已移除，禁止重新加入或在文档中推荐 |

`config.toml` 的 `[feishu.bitable]` 使用独立的 `app_id` / `app_secret` / `app_token` / `table_id`，默认表为 `tblWT2SRKJ3CEZ5e`；主推款仍由 `[feishu]` 的 wiki 电子表格读取。

### 行为摘要（流水线）

**默认**全程只读，不点同意。  
**仅** `--execute --yes` 时进入批准支路。

```text
1. 读 config / 飞书主推表 → hero_keys（「是否主推=是」的货号 + 商品ID）
2. 校验当前页为样品申请；必要时点「待审核」tab（找不到则报错）
3. 按 `--data-source` 扫表 → raw_rows（`dom` / `api` / `auto` / `shadow`）
4. 列表初判 evaluate_row（此时尚无详情 GPM；require_detail=False）
5. 按开关决定谁进详情 → 按数据源读取 profile API 或 DOM → 抽 Video/Live GPM 等
6. 带详情重判 evaluate_row（正式须 require_detail=True）
7. 若未 --execute：导出 csv/xlsx/json，结束
8. 若 --execute --yes：
   a. 写批准前备份 exports/*_pre_execute.*
   b. 对 eligible 行（最多 execute-limit，默认 1）：
      · product_id → 主推货号 → 寄样产品选项（可模糊）；失败则整单跳过
      · 若 --write-feishu：红人ID+寄样产品 去重，命中则跳过
      · 用待审核列表 API 即时确认 apply_id / creator_id / product_id 仍一致且可批准
      · `--write-source dom`：全部展开 → 点「同意」→ 有弹窗则确认（永不点拒绝）
      · `--write-source api`（默认）：调用已捕获的 `/api/v1/affiliate/sample/group/action` 单条批准接口，不重试、不回退 DOM
      · 批准后用列表 API 确认进入「待发货/curr_status=20」；无法确认则标记 unknown、停止且不写飞书
      · 状态确认成功且 --write-feishu → 写达人关系管理(新)（人员=王良希（技术）、待发货、未寄样）
   c. 再导出（含 approve_status / feishu_* 列）
```

**批准约定：** 先同意成功再写飞书；去重命中或货号无法映射 → 不批不写；红人ID=`creator_name`；粉丝/履约原样字符串；平台「同意」不可脚本撤销。

#### 谁会进详情？（与主推的关系）

实现：`scripts/screen_sample_requests.py` 在列表初判之后构造 `detail_targets`。

| 开关组合 | 进详情的人 | 非主推会进详情吗？ |
|----------|------------|--------------------|
| 仅 `--with-detail`（**无** `--detail-all`） | 列表初判 **`eligible=True`** 的行 | **一般不会**（主推失败 → 初判不通过 → 不进目标） |
| `--with-detail` + **`--detail-all`** | 扫到的**每一行** | **会**（主推只影响最终是否通过，不挡详情） |

要点：

- **禁止** `--detail-limit`（参数已移除）：不得截断详情目标；试跑限量只用 `--max-rows`。  
- **主推（条件 2）与详情（条件 9–11）在判定上独立**：非主推最终仍是「不通过」；有 `--detail-all` 时仍可能拉到 GPM，便于导出完整指标。  
- **正式日常**推荐：`--with-detail --require-detail`（**不要**默认加 `--detail-all`），只给「列表层已有机会通过」的达人进详情，省时间。  
- **排查 GPM / 要全表详情列**：加 `--detail-all`（更慢）。  
- `--require-detail`：最终判定时若无视频/直播 GPM → **不能通过**（与是否主推无关）。

### 导出字段（摘要）

达人名、昵称、货号/product_id、履约率、成交件数、GMV、客单价、视频/直播 GPM、**视频达人** / **直播达人**（是/空）、粉丝数、是否主推、是否通过、原因；  
execute 时另有：动作、批准状态/错误、飞书状态/`record_id`/错误、寄样产品选项、解析货号。  
完整列名见 `scripts/lib/export_util.py`。

### 纪律（脚本级）

1. 先确认页在「待审核」，再跑命令  
2. 正式筛查必须：`config.toml`/`FEISHU_APP_SECRET` + `--with-detail` + `--require-detail`；测试可用 `--max-rows` 限扫表行数  
3. 禁止文档推荐「仅列表」、本地 xlsx 或 `--skip-hero-check` 作为正式路径；**禁止使用 `--detail-limit`**  
4. **默认**禁止同意；仅 `--execute --yes` 可批；测试默认**不要** `--write-feishu`；默认 `--execute-limit 1`  
5. 第一阶段 API 化覆盖待审核列表和筛查详情；当前页面 API 使用异步 `fetch` + request_id 轮询，兼容 2 号店同步 XHR 空响应；批准默认走 API，`--write-source dom` 可显式使用 DOM；简介仍走详情 DOM，第 6/9 步发送默认走 IM SDK API；`--execute` 必须使用 `--data-source dom`，shadow 始终以 DOM 为权威
6. 店掉线 / 停在详情页：手动回到待审核列表后重跑
7. 勿把 `app_secret` / `config.toml` 提交 git 或写入聊天记录

---

## SOP 第 6–9 步（介绍私信 / 物流回写）

与筛查脚本分开跑。发私信与批准同级：默认 dry-run，真发须 `--execute --yes`，默认 limit=1。

### 第 6 步：介绍私信

语言**只**从达人详情页简介识别（`#creator-detail-profile-container span.break-words`），仅英语 / 西班牙语。空简介默认英语。真实发送默认调用页面内 IM SDK `onSendText`，不点「发送」按钮；`--write-source dom` 才走旧点击路径。发送后读会话确认话术指纹，未确认则标 `send-unknown` 且不重试。

```bash
# 只读预演（不发送）
python3 scripts/send_sample_intro.py --from-export exports/sample_screen_<ts>.json --max-rows 1

# 真发 1 条（默认 IM SDK API）
python3 scripts/send_sample_intro.py --from-export exports/sample_screen_<ts>.json \
  --execute --yes --execute-limit 1

# 也可指定人（须有 creator_id 才能打开详情）
python3 scripts/send_sample_intro.py --creator-id <cid> --creator-name '<handle>'
```

`--from-export` 优先取 `approve_status=approved` 行，否则取筛查通过且有 `creator_id` 的行。  
`--write-feishu` 只回写「使用语言」，不新建行。

### 第 7–9 步：已发货 → 飞书 → 物流私信

北京时间 **16:00 前拒绝跑**（测试加 `--force`）。这是脚本启动时的运行时门闩：用 `Asia/Shanghai` 看当前小时是否 `< 16`，不是 cron，也不会到点自动触发。16:00 后直接跑即可；`--force` 只绕过时间检查，不写飞书、不发私信。用户店已开即可。默认用样品列表 API `tab=30` 读已发货，再用商家订单页 GET `/api/v1/fulfillment/na/logistic_detail/list` 读物流单号。可用 `--creator-name` / `--creator-id` 只处理指定达人。

- 已发货列表 `main_order_id` = 飞书「订单号」
- 商家订单物流 API / 页面 **「TikTok 物流」** = 飞书「快递单号」= 第 9 步发给达人的单号（**不是**订单 ID）
- 回写成功后：`是否已寄样=是`，`合作状态=待发布`
- 状态联动前提：本次已写入物流单号，或飞书已有同一物流单号；只允许「待发货」→「待发布」，不回退后续状态
- 已有不同快递单号默认不覆盖（`--overwrite` 才覆盖）
- 已有不同快递单号且未 `--overwrite`：不写新单号，也不推进合作状态
- 飞书无匹配行（红人ID + 寄样产品）→ 不新建

```bash
# 只读预演（抽运单、匹配飞书，不写不发）
python3 scripts/sync_shipped_tracking.py --force --max-rows 1

# 回写飞书（不发私信）
python3 scripts/sync_shipped_tracking.py --write-feishu --force --max-rows 1

# 回写 + 发物流私信 1 条（默认 IM SDK API）
python3 scripts/sync_shipped_tracking.py --write-feishu --send-tracking \
  --execute --yes --execute-limit 1
```

第 9 步话术接 **纯物流单号**：  
`Dear, here is the tracking number:{单号}` / `Estimada, aquí tienes el número seguimiento:{单号}`。

会话里已有介绍/物流话术指纹则跳过。禁止点「邀请」。

---

## 辅助命令（排查用）

```bash
ziniao-status && ziniao-cli doctor
# 店铺识别和页面读取统一由项目脚本经 ZClaw 完成；不要用 page extract --mode running。
# 读当前页（勿点同意）：仅通过项目已有 execute_script 诊断脚本查看 href / title / body 摘要。
```

排查默认**勿**点「同意」。仅用户明确要求且 CLI 已带 `--execute --yes` 时，才走批准路径。

---

## 筛查规则（与 SOP 对齐）

实现：`scripts/lib/filters.py`。阈值以 SOP 为准，改阈值须改代码并说明。

### 条件 1（列表 + 详情；正式须 `--with-detail --require-detail`）

| # | 规则 | 数据来源 |
|---|------|----------|
| 1 | 粉丝数 > 2000 | 列表 `creator.follower_num` |
| 2 | GMV > 1500（1k5） | 列表 `creator.gmv` |
| 3 | 成交件数 > 80 | 列表 `creator.item_sold` |
| 4 | 千次曝光成交 > 10 | 详情 `GPM` 优先；列表代理仅辅助 |
| 5 | 客单价 10–25 USD | 详情 `Revenue per buyer` 或 `GMV/件数` |
| 6 | 类目含白名单任一 | `creator.categories`（Beauty / Womenswear / … / Home Textiles） |
| 7 | 预计发布率/履约 > 80% | 列表履约或详情 Est. post rate |
| 8 | 女性粉丝 > 60% | `top_follower_gender` Female |
| 9–11 | 视频 **或** 直播达标 | 详情：Video GPM / Live GPM、均播、互动率（正式必拉） |

视频侧默认阈值（`filters.Criteria`）：Video GPM **> 10**、均播 **> 300**、互动率 **> 2%**。  
直播侧默认：Live GPM **> 12**、直播均播 **> 1000**。  

均播 / 互动字段若 **缺失（None）**，该子项不卡；有数才比阈值。  
SOP 通过条件 9–11 只需 **视频侧或直播侧至少一侧达标**（`video_ok or live_ok`）。

### 视频达人 / 直播达人（导出列，非互斥）

两列可 **同时为「是」**（同一个人既是视频达人又是直播达人）。值为 **「是」或空**，没有「否」。

实现分三层（`filters.evaluate_row` + `export_util.derive_creator_type_flags` + 详情粗标）：

| 层 | 作用 | 规则摘要 |
|----|------|----------|
| **SOP 达标** `video_ok` / `live_ok` | 决定条件 9–11 是否过 | 见上表阈值；双侧都过 → 筛查仍过 |
| **导出标记** `is_video_creator` / `is_live_creator` | 表上「视频达人」「直播达人」 | 达标 → 标「是」；**未达标但该侧 GPM > 0** 也可能标「是」（便于区分侧别，**≠ 已过 SOP**） |
| **详情粗标** `creator_type` | 详情页按 GPM 是否 >0 | 仅视频 / 仅直播 / `视频+直播`；**不看**均播与阈值 |

导出前若 `is_*` 仍空，会再按 `creator_type` 文案或 GPM > 0 兜底（`export_util.prepare_export_row`）。

```text
详情 GPM / 均播 / 互动
        │
        ├─► SOP：video_ok 或 live_ok → 条件 9–11 通过
        │
        └─► 导出列：达标必「是」；未达标但 GPM>0 也可能「是」
```

**勿**把导出列「是」直接当成「该侧已通过 SOP」；是否通过看 `eligible` / 原因列。

### 条件 2（主推款）

- 来源：飞书知识库电子表格（**不是**「达人管理总表」；达人建联默认见上文「飞书数据源」）  
  `https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc`（`tk产品图+货号` / `Sheet1`）  
- 列：**产品货号**、**是否主推**（`是`/`否`）；图片列忽略  
- 解析：`scripts/lib/feishu_hero.py`（wiki → spreadsheet → values；只读）  
- 匹配：主推行的 **产品货号** 与 **商品ID** 均进入 `hero_keys`；与列表 `product_id` / 标题 / sku 描述精确或子串匹配  
- 凭证：`FEISHU_APP_ID`（默认内置）+ **`[feishu].app_secret`（config.toml）或 `FEISHU_APP_SECRET`**  

列表 `product_id` 是平台数字 ID，**不等于**货号；飞书表「商品ID」列用于对齐平台 ID。无商品ID或填「下架」时，仅能靠标题里出现货号命中。

---

## 页面与 DOM 约定

### 列表（待审核）

- 典型 URL：`.../product/sample-request` 或 `.../affiliate/sample/sample-request`  
- 表数据：静默读 React fiber `memoizedProps.record`（对齐 zn_daren，**勿点**名称旁易弹剪贴板的控件）  
- 列表默认可折叠；「全部展开」后出现「同意/拒绝」  
- **默认**禁止点同意/拒绝；扫表时跳过无达人信息的操作子行  
- **仅** `--execute --yes`：`approve_dom` 可点「同意」（永不点拒绝）；二次确认弹窗可点确认  

### 达人详情（只读）

- 入口：点头像，或直链  
  `/connection/creator/detail?cid=<creator_id>&enter_from=sample_request&...`  
- 抽取：`scripts/lib/creator_detail.py` 正文正则（Video GPM、Live GPM 等）  
- 返回：回列表 URL 或 `history.back()`  
- 详情页若出现同意按钮：只允许关闭/返回，**不得**在详情页点同意（批准只走列表）  

### ZClaw

- 统一经 `ziniao-cli zclaw invoke`（`execute_script` / `visit_page` / `extract_data` 等）  
- Bridge：`127.0.0.1:9481`；与 WebDriver `16851` 不同  
- `storeId` 解析：显式 ID > running 精确店名 > running 唯一 > 测试默认 1 号店  

紫鸟 GUI / WEBDRIVER 互斥、启停细节见 `../zn_daren/AGENTS.md`。本仓筛查脚本默认在 **GUI + 已 open 的店铺页** 上跑。

---

## 导出

目录：`exports/`  
字段含：达人名、货号/product 信息、履约率、成交件数、视频/直播 GPM、视频达人 / 直播达人（是/空，**可同时为是**）、粉丝数、是否主推、是否通过、原因等。  
execute 时另含：批准/飞书状态列；批准前另有 `*_pre_execute.*` 备份。  
见 `scripts/lib/export_util.py`。视频/直播列语义见上文「视频达人 / 直播达人」。

---

## Agent 操作纪律

1. 改自动化前先确认：用户是否已停在「待审核」；`ziniao-cli doctor` / running 店铺。  
2. **默认**禁止「一键同意 / 批量通过」。用户要批准时：仅允许走已有 `--execute --yes`（+ 可选 `--write-feishu`），并提醒 limit、备份、平台不可脚本撤销；勿另开无门闩路径。  
3. 测试默认用 1 号店；勿默认操作 2 号店除非用户指定。  
4. 飞书达人相关默认 **「达人关系管理(新)」→「达人管理总表」**（`tblWT2SRKJ3CEZ5e`，见「飞书数据源」）；勿默认写旧表「达人关系管理」；主推款仍走 wiki `tk产品图+货号`。  
5. 探 DOM 时可用 `execute_script` 读结构；默认点击仅限：待审核 / 已发货 tab、翻页、头像/详情、返回、达人消息入口。execute 时可点列表「同意」、确认弹窗、私信「发送」。永不点「邀请」。  
6. 失败时优先检查：页面是否被留在详情页、Bridge 断连、tab 不是待审核、列表未「全部展开」。  
7. 勿把 `apiKey`、账号密码写进仓库或聊天日志。  
8. 改 SOP 阈值：改 `filters.Criteria` 并同步 README/本文件相关表。  
9. 与 zn_daren 共用模式时，优先**引用**而非复制大段 `zclaw_dom`；本仓 `lib/zclaw.py` 保持精简。

---

## 故障速查

| 现象 | 处理 |
|------|------|
| `not-on-sample-page` / `no-pending-tab` | 用户手动回到「样品申请 → 待审核」 |
| 扫表 0 行 | 列表未加载 / 在错误 tab / 详情页残留 |
| 详情失败 / 不在 detail | 检查 `creator_id`；回列表再试 |
| 主推全不匹配 | 货号 vs product_id；看标题是否含货号；核对飞书「是否主推」 |
| 飞书主推表读取失败 | `config.toml` / `FEISHU_APP_SECRET`；应用仍可阅读该 wiki/表；网络 |
| 仅 `--execute` 无 `--yes` | 退出码 2；补 `--yes` 或去掉 `--execute` |
| `approve-button-not-found` | 先「全部展开」；确认 `can_be_approved`；页是否仍在待审核 |
| 货号无法映射寄样产品 | 整单跳过；查主推表商品ID 与 bitable 单选选项 |
| 飞书写入失败但已批准 | 导出 `feishu_error`；人工补录；平台同意不可脚本撤销 |
| Bridge 连不上 | 紫鸟客户端启动；`ziniao-cli doctor` |
| 误开 WEBDRIVER 无窗口 | `../zn_daren`：`ziniao-gui` |
| 导出无 xlsx 样式 | 可选装 `openpyxl`；否则简易 xlsx |
| 16:00 前跑物流同步 | 加 `--force`（仅测试） |
| 无 TikTok 物流单号 | 订单页未出单 / 抽错成订单 ID；看导出 `tracking_raw` |
| 飞书无匹配行 | 红人ID 与列表 handle 是否一致；寄样产品是否对上 |
| 私信找不到会话 / 无输入框 | 详情 Message 可能 disabled；IM 搜索须 React `_valueTracker`；本周「只能给合作过的达人发信」会挡住新会话 |
| 语言判错 | 看导出 `bio` / `lang_reason`；仅英/西，空简介默认英 |

---

## 维护本文件

- 同一错误出现第二次 → 补一条**可验证**规则。  
- 纪律变更（尤其「禁止同意 / 可选 execute」「导航责任」「默认店铺」「默认达人关系管理(新)」）必须更新本文件与面向使用者的 `README.md` / `使用方法.md`。
- 删冗长：每行自问「删掉会不会更容易犯错」。  
- 维护前若改全局约定，向用户说明原因。
