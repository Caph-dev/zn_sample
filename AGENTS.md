# zn_sample · AGENTS.md

本文件给 **Agent / 技术维护**：纪律、数据 ID、实现契约。  
日常双击 / 出问题只写 [README.md](./README.md)。新电脑配机只写 [快速开始.md](./快速开始.md)。规则原文见 `样品申请筛查sop/1-样品申请筛查sop.md`；到货跟进见 `样品申请筛查sop/2-查看到货+达人跟进.md`（忽略飞书 wiki 里的 B006-A 与旧的「第五天 / 每隔两天」）。

紫鸟启停 / GUI vs WEBDRIVER / `ziniao-cli`：**先读** [`../zn_daren/AGENTS.md`](../zn_daren/AGENTS.md)。本仓默认 **GUI + 已 open 的店**。勿混用 `zn_daren` 的 `--execute` 取消逻辑。禁止 `ziniao-cli page extract --mode running`（可能 `runtime.reopen`）。

不要改系统或启动器 `PATH`。Windows 上 `ziniao-cli.cmd` 只解析成 `node` + `run.js` 的**绝对 POSIX 路径**（`C:/...`），子进程固定 UTF-8。日志用 `logging`，入口 `configure_logging`，库代码 `getLogger`。

---

## 非协商

1. **默认禁止写操作**  
   不点：同意 / 批准 / 拒绝 / 发货 / 私信「发送」/「邀请」。  
   默认只读列表、详情、本地判定、导出。  
   **仅** `--execute --yes` 可对筛查通过行点「同意」（永不拒绝）或发第 7 / 10 步私信。
   写飞书另加 `--write-feishu`。测试默认不写飞书。  
   `--execute-limit` 默认 1，不得新参数绕过。批准前写 `*_pre_execute.*`。平台同意与已发私信不可脚本撤销。
   唯一隔离例外：「自动批准」页的自定义规则链走自有任务链（见下文「自动审批子页面」），
   仍受 `--execute --yes`、同一执行限额、去重、备份与核对保护，且只接受服务器生成的自定义快照。
   达人跟进详情页的「发送这条跟进私信 / 发送感谢私信」是同一个门闩的网页入口：固定单条 `task_id`、每次最多 1 条、任务运行中不可取消，底层仍走 `send_followup_message.py --execute --yes` 与原子认领。
   同页「预演跟进私信 / 预演感谢私信」不是写操作（只打开会话核对身份，不发送），但同样占用店铺页面：
   与 0/1/2/3 及发送任务共用同一互斥（`assistant/services/page_lock.py`），有页面任务在跑时返回 409 `store-busy`，不得并发点击。
   预演与真发共用同一套重复/保留检查（会话指纹、阶段窗口内已有我方消息、感谢话术保留），预演只报告不写平台。

2. **两阶段导航**  
   `open_sample_store.py` 默认只开店；目标店已运行绝不关闭/重开。  
   **仅** 0 号脚本 / `open_sample_store.py --reopen` 可对**目标店**先 `close_store` 再 `open_store`（带 debugPort）。有其他店 running 仍拒绝切店。不走 `page extract --mode running`。  
   用户登录后可停在商家中心任意子页（空白页和登录页除外）。  
   `--from-seller-home` 从已登录商家中心/联盟中心/样品申请页进待审核；未传 `--store-id` 时必须 running 恰好一家，不回落到测试 1 号店。先返回脚本结果再跳转。  
   未带该开关：用户须已停在 **样品申请 → 待审核**。  
   1/2/3 导航失败只报错退出，不重开、不切店。

3. **测试默认 1 号店（筛查/物流）**  
   `跨境1号店（Lingerie Outlet）` / `27437742526069`。  
   未传 `--store-id` / `--store-name` 且 running 无法唯一解析时可用。生产/多店必须显式 ID。`--no-default-store` 禁用默认。  
   **1/2/3 勿默认操作 2 号店。**  
   **例外：0 号** 在工作台没有打开的店、且未传 `--store-id`/`--store-name` 时，默认 `open_store` 2 号店 `跨境2号店` / `27506607043054`（带 debugPort）。已有恰好一家 running 则重开那一家，不切到 2 号店。`--no-default-store` 禁用该默认。

4. **飞书两张表勿混**  

   | 用途 | 资源 | ID |
   |---|---|---|
   | 达人关系（默认写这里） | 「达人关系管理(**新**)」→「达人管理总表」 | `app_token=CJXSbLIQWahB8esVOiscX7j1nLc` `table_id=tblWT2SRKJ3CEZ5e` `view=vewNtqmTk4` |
   | 主推款（条件 2，只读） | wiki `tk产品图+货号` | https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc |

   不要默认写旧表 `tbl1Sc97Gkpc96xI`。  
   新建：人员=**王良希（技术）**，合作状态=待发货，是否已寄样=否。  
   写入 TikTok 物流单号后：已寄样=是，待发货→待发布；不回退「已发布」等。D+15 未履约写 **未发布**（不是待发布）。无匹配行（红人ID+寄样产品）不新建。

5. 只改任务相关代码。密钥 / `apiKey` / 密码不写入本文件、不提交 git、不进聊天。默认中文沟通。

---

## 能力边界

| 能力 | 实现 | 不可误解为 |
|---|---|---|
| 本地网页操作台 | 只读日常更新；网页只保留 0（运行准备）、只出名单（只读筛查）、物流写回与跟进动作（含跟进详情页单条发送），复用 `operator_launch` 或固定 handler；正式筛查-批准只走脚本 | 不接受任意命令/参数；网页写任务仍须明确输入 `y`，物流写回在 16:00 前另须 `FORCE`；最终仍只走既有 `--execute --yes` 门闩 |
| 进待审核 | 开店 + `--from-seller-home` | 不代办登录，不擅自切店 |
| 初筛+复筛+内容审查 | `--with-detail --require-detail`；第 4 步销售数据通过后执行第 5 步内容审查 | 不能把仅列表结果或没有内容通过证据的结果当正式通过名单 |
| 批准 | `--execute --yes`；默认已捕获的窄 API | 不能猜 endpoint、扩大接口、绕过门闩；`shadow` 禁止配合 `--execute`；API 路径尚未用第二条真实申请重复验收 |
| 写达人关系 | 写接口接受后 + `--write-feishu`；`--confirm-export` 可**补写** | 不是主推表；写结果未知时不先写 |
| 第 7 / 10 步私信 | 独立脚本；严格走样品申请页「聊天数」→「发送消息」→输入达人 ID→「聊天」的新路径，默认 IM SDK | 批准后不自动发；不打开详情消息弹层、不跳到 `/seller/im`、不点「聊天数」里的最近联系人 |
| 第 8–9 步物流 | 独立脚本；**北京时间 16:00 前拒绝** | 先飞书近 7×24 小时且合作状态=待发货（主键红人ID+寄样产品），再对已发货。`main_order_id` 不是发给达人的单号；`--force` 只过时间门。物流 GET 对齐订单页 query（`oec_seller_id`/`seller_id`/`aid`）；订单 URL 用 `seller.us`，不用 apex。紫鸟 `error.html` 当跳转失败 |

读路径：`auto` 日常推荐（API 失败回退 DOM）；`api` 失败即报错。页面 API 用异步 `fetch` + `request_id` 轮询（兼容 2 号店同步 XHR 空响应）。批准默认 `--write-source api`，DOM 须显式指定。简介仍走详情 DOM。

**详情接口系统级故障熔断**：错误串含 `code=100000` 或 `Please remove the plugin` 视为逐行复现的故障（TikTok 反爬判定浏览器环境/插件流量）。连续 3 行（`SYSTEMIC_DETAIL_FAILURE_LIMIT`）即停止逐行 API 与 DOM 回退，剩余行标 `detail-api-systemic-failure` → needs_review，只记一条汇总。禁止把该错误当成单行失败继续刷表；出现后先关插件/换干净 profile 或等平台恢复。实测证据见 `达人资料补齐接口故障排查-20260906.md` 顶部更正。

---

## 操作台页面（Astryx React 壳）

导航固定八项：总览 / 运行准备 / 自动批准 / 达人跟进 / 物流 / 任务 / 报表 / 诊断（含各自详情页）。正文由 React + Astryx 渲染，源码 `frontend/src/console/`，构建 `npm run build:console`（或 `npm run build:web` 同时构建自动批准），产物 `assistant/web/static/console/` 被 gitignore。

- 服务端只渲染 `assistant/web/templates/console.html` 并注入 bootstrap JSON：页面数据在 `assistant/web/console_pages.py`，契约同步 `frontend/src/console/types.ts`。不要新增 Jinja 页面模板或手写页面 CSS。
- **入口按页归属**（`operator_groups(page)`）：`/prepare` 打开店铺；`/auto-approval` 顶部只出名单（只读筛查）；`/followups` 物流同步 + 物流写回（16:00 门）+ 补齐资料 + 生成待办 + 预演感谢私信。**正式 SOP 的「筛查-批准-写飞书-私信」已从网页移除，只走脚本 1/2**；`/api/jobs/operator/pipeline` 端点保留兼容，不在任何页面暴露。
- `assistant/web/static/app.js` 仍负责任务面板、确认弹窗与表单提交：表单是 document 级事件委托（`data-job-form` / `data-job-cancel`），React 后挂载也能绑定；任务详情页挂载后派发 `assistant:monitor-job` 启动监控；准备状态由总览 / 运行准备页轮询，自动批准页只显示状态条 + 去准备页链接（不重复探活）。
- 任务面板与确认弹窗抽到 `_task_panel.html`，`console.html` 与 `auto_approval.html` 共用；两个壳都加载 `app.js` 与 `console-shell.css`。
- 静态资源按产物 mtime 加 `?v=`（`_static_version()`），避免浏览器缓存旧 JS/CSS。
- **主题 CSS 必须在组件产物之后加载**（`geist-theme.css` 放 `console/assets/index.css` / `auto-approval/assets/index.css` 之后）。产物里默认主题在 `@layer astryx-base`，主题文件在 `@layer astryx-theme`；layer 顺序按首次出现决定，主题先加载会被默认值压住（表现为 Meta 蓝 `#0064E0` + 系统字体，而不是 Geist 蓝 `#0070f3` + Geist 字体）。主题主色是蓝，勿改回黑。
- `assistant/web/static/console-shell.css` 只保留 app.js 动态生成 DOM 与任务面板 / 确认弹窗所需类，禁止裸元素选择器，避免污染 Astryx 组件。
- 页面级测试改断言 bootstrap JSON（`tests/assistant/console_payload.py`），不再匹配已不渲染的 HTML。

---

## 自动审批子页面（自定义审核方案）

`/auto-approval` 是独立 React 页（构建产物 `assistant/web/static/auto-approval/`，源码 `frontend/`，构建 `npm run build:auto`；产物被 gitignore）。后端：`assistant/api/auto_approval.py` + `assistant/services/auto_approval_service.py` + `scripts/auto_approval.py`（三种 mode）+ `scripts/lib/auto_approval_rules.py`；任务类型 `auto_approval_preview|execute|reconcile`（已入 ZINIAO_JOB_TYPES 与写任务保护集）。候选筛选层级（L0–L5）见 [docs/spec/自动审批候选筛选流程.md](./docs/spec/自动审批候选筛选流程.md)。

- **隔离，不是默认**：正式 SOP、`filters.Criteria`、0/1/2/3 入口行为不变。自定义规则只绑定本次任务快照；关闭检查 = not_checked，≠通过。正式入口与旧导出继续严格走 `_guard_content_review()`；本页批准只接受服务器生成的自定义快照（preview 信封 + rule hash + 逐项证据），禁止前端布尔量绕过内容门禁。
- **规则 schema 严格**（`validate_custom_rule`）：未知字段、非有限数字、非法范围、空条件组合、非主推商品都拒绝。至少启用一项检查；比较符第一版固定 `>`（客单价为区间）；`video_live` 组 either/both，both 须两侧都启用。
- **第一版后端边界**：执行限量为正整数（默认 1，无上限）；预览新鲜度 24 小时；内容组 days 仅 7、min_related 仅 4（与内容审核库一致，前端锁定）；数值上限见 `BASIC_LIMITS`/`VIDEO_LIVE_LIMITS`。
- **判定语义**：启用指标缺失或违反 → failed（不通过）；完整满足 → passed；仅详情采集失败（`detail_error`）导致缺失 → needs_review，且批次禁止执行。视频/直播任一侧通过即过；AND 组已知失败即失败。主推/当前款/`can_be_approved=false`/缺 ID 为**执行拦截**（blocked），不属审核项。
- **按需采集**：仅 video/live 组启用才拉详情；详情字段优先（履约 `est_post_rate`、官方 GPM、客单价 `aov_detail`），缺失回退列表口径（GPM 近似标记 source=list-proxy）。内容审核只跑自定义已通过行，与正式链路同一 `review_creator_rows`/`validate_content_review` 证据。
- **执行边界**：服务端校验 preview 完成/完整/新鲜 + 候选 ⊆ eligible + 限量 + 键入 `y` + 幂等键（重复点击/重试去重）；脚本内再验信封/店铺/hash、逐行复算结论、hero 重查、产品解析、查重、`check_pending_application_api` 预检后批准（`--write-source api` 固定）；批准未知 → 不写飞书、不自动重试。写飞书默认关，目标仍「达人关系管理(新)」。reconcile 复用正式 confirm 管线（不重批）。
- **任务与页面**：写任务运行中不提供取消；启动器 `PROTECTED_JOB_TYPES` 含 auto_approval_execute/reconcile。页面网络异常先查任务状态，不重复创建批准任务。改规则立即令旧预览失效。

---

## SOP → 脚本停止点

五个入口断开。SOP 新增第 5 步内容审查，0/1/2/3 启动器编号不变。可复制命令只写 README，这里只写停点和门闩。

| SOP | 脚本 | 默认停 | 加开关 | 不会做 |
|---|---|---|---|---|
| 开店（带调试口） | `open_sample_store.py --reopen` | 店铺窗口打开 | 无 | 不代登、不进联盟中心、不切其它店 |
| 开店（不重开） | `open_sample_store.py` | 首页前 | 无 | 已运行不关、不进联盟中心 |
| 1–5 筛查+名单 | `screen_sample_requests.py` | 导出 | 正式须 `--with-detail --require-detail`，销售数据和内容审查都通过 | 不批、不发信 |
| 6 同意/写表 | 同上 | 不写 | `--execute --yes`；再加 `--write-feishu` | 不发第 7 步 |
| 6 **补写** | 同上 `--confirm-export` | 核对待发货 / 补飞书 / 回填订单号 | 约 10 分钟窗口；不重批 | 不发私信 |
| 7 介绍 | `send_sample_intro.py` | 预演 | `--execute --yes` | 不批、不查物流 |
| 8 读物流 | `sync_shipped_tracking.py` | **16:00 前拒绝** | 16:00 后只读；测试 `--force` | `--force` 不写不发 |
| 9 回写飞书 | 同上 | 不写 | `--write-feishu` | 不发第 10 步 |
| 10 物流私信 | 同上 | 不发 | `--write-feishu --send-tracking --execute --yes` | 不回头筛/批 |

**补写：** 写接口 `success_count=1` 即算批准成功，不等待发货。列表约 10 分钟后刷新；用 `--confirm-export` 核对并补飞书。原先因「待审核找不到」标成 `skipped` 的已批行也纳入补写。

**订单号回填（同一 confirm 步骤）：** 确认转入待发货后，对飞书已建行的行回填「订单号」列，只写这一列（不联动「是否已寄样」/「合作状态」）。订单号取自待发货 tab 的 `main_order_id`，须非空、非 `0`、符合 15–20 位订单号形态（`is_order_id`）；已有不同订单号不覆盖（`update_record_order_no`）。拿不到有效订单号的行跳过，留待物流步骤兜底；「快递单号」仍只在已发货后由第 8–9 步写。回填与补写共享同一 `--execute-limit` 预算，主流程先行。导出新增 `order_no` / `feishu_order_status` / `feishu_order_error` 列。

**16:00：** `sync_shipped_tracking.py` 启动时用 `Asia/Shanghai` 看当前小时，`< 16` 退出。不是 cron，到点不会自动跑。

---

## CLI 契约

| 参数 | 约束 |
|---|---|
| `--data-source` | `dom\|api\|auto\|shadow`。批准推荐 `auto`。`shadow` 禁止 `--execute` |
| `--write-source` | 批准/私信写路径；默认 `api`；`dom` 须显式。都要 execute 门闩 |
| `--from-seller-home` | 才允许从已登录商家中心（任意子页）导航；未写 `--store-id` 时须 running 唯一店。失败不重开不切店 |
| `--from-export` | 跳过扫表/详情。筛查脚本：批准必须有第 5 步内容通过证据；旧导出无证据不可批准。历史 `--confirm-export` 补写行为不变。介绍脚本：优先 `approved`，也会带上「通过且有 creator_id」的未批行 → 指定人用 `--creator-id`/`--creator-name` |
| `--confirm-export` | 只补写/核对/回填订单号，不重批；回填与补写共享 `--execute-limit` |
| `--with-detail --require-detail` | 正式筛查必须成对；禁止 `--detail-limit`、`--skip-hero-check` |
| `--all-hero-products` | 恢复批准/筛查全部主推款；默认只过 `1732414717062320994` |
| `--execute --yes` | 唯一批准/真发门闩；缺一退出码 2 |
| `--execute-limit` | 默认 1 |
| `--write-feishu` | 筛查脚本不能单独用来「只筛查但写表」。物流无匹配行不新建 |
| `--force` | 只绕过 16:00 |
| `--observe-approve-network` | 仅单条 execute 被动观察；不重放、不登记未确认 endpoint |

网页不是新的业务写路径：后端只登记 `prepare|screen|pipeline|tracking|followup_send` 五个固定任务，使用启动操作台的 `sys.executable` 直接调用既有编排（`followup_send` 走固定 handler 调 `send_followup_message.py`），不执行 `.command`/`.bat`、不接受 shell 字符串、不允许网页覆盖 store/limit/source 等参数。网页只暴露 `prepare`（运行准备）、`screen`（自动批准页只出名单）、`tracking`（达人跟进页物流写回）和 `followup_send`（达人跟进详情页单条发送，固定 `task_id`、限 1、键入 `y` 确认）；`pipeline` 端点保留兼容但不在任何页面暴露，正式筛查-批准只走脚本 1/2。运行中的网页 operator 任务不提供取消按钮，避免把已经发生的平台批准、飞书写入或私信误解为可撤销。

网页“运行准备”的调试口状态只认唯一 running 店的短超时 `execute_script` 探活；不能因 running 有店或 `doctor` 正常就显示已就绪。检测只读且不得自动开店/重开；ZClaw 任务运行时返回 busy 缓存，不并发探活。探活只在总览 / 运行准备页轮询；自动批准页只显示状态条 + 去准备页链接。

批准：先同意成功再写飞书；去重或货号无法映射 → 不批不写。红人ID=`creator_name`。  
第 10 步发 **TikTok 物流单号**（有承运商则 `{承运商}, {单号}`），不是订单 ID。已有不同单号默认不覆盖（须 `--overwrite`）。

---

## 筛查阈值

改阈值改 `filters.Criteria` 并同步 SOP。正式必须 `--with-detail --require-detail`。

| # | 规则 | 来源 |
|---|---|---|
| 1 | 粉丝 > 2000 | 列表 |
| 2 | GMV > 1500 | 列表 |
| 3 | 成交件数 > 80 | 列表 |
| 4 | 千次曝光成交 > 10 | 详情 GPM |
| 5 | 客单价 10–25 USD | 详情或 GMV/件数 |
| 6 | 类目命中白名单 | 列表 |
| 7 | 履约 > 80% | 列表或详情 |
| 8 | 女性粉丝 > 60% | 列表 |
| 9–11 | 视频 **或** 直播一侧达标 | 详情必拉 |

视频：GPM>10、均播>300、互动>2%。直播：GPM>12、均播>1000。均播/互动为 `None` 不卡。  
导出「视频达人/直播达人」=「是」或空，可同时为是；**≠** 该侧已过 SOP。看 `eligible`。  
条件 2：`hero_keys` 只含「是否主推=是」的货号+商品ID；只精确匹配这两类字段。禁止标题/描述/TikTok `sku_id`、禁止子串。批准解析同样只认主推行；货号无法映射或不主推 → 不批不写。列表 `product_id` ≠ 货号。

**当前再收窄（只影响批准/筛查）：** 默认只过商品 ID `1732414717062320994`（指定 B005）。其它主推款筛掉、不批。恢复全部主推：`--all-hero-products`（`allowed_product_ids` 为空）。禁止 `--skip-hero-check` 做正式跑。

**第 5 步内容审查（第 4 步销售数据通过后）：** TikTok 最近滚动 7×24 小时内，至少 4 条相关带货视频，且这些视频中至少 1 条明确展示产品穿在身上，或同一画面内露脸并手持产品。相关类目沿用既有 7 类：Beauty & Personal Care、Womenswear & Underwear、Household Appliances、Fashion Accessories、Shoes、Sports & Outdoor、Home Textiles。无需强制 ASR、口播或每日发布配额。

未知、缺失或部分采集且证据不足 → `needs_review`，不得 `eligible`；完整计数少于 4 条 → `failed`。解析不了的购物锚点不计入那 4 条，也不单独否决；已确认至少 4 条相关且有展示证据即可通过。未知只在相关还不足 4 条时待复核。已确认至少 4 条相关带货视频且其中有展示证据时允许短路通过，但计数只能标为已知下界，不得冒充全量总数。仅第 4、5 步均通过才可批准；旧导出缺少内容通过证据不可批准，历史 `--confirm-export` 核对/补写保持不变。

内容能力使用本地 `scripts/lib/tiktok_creator_videos.py` 与 `scripts/lib/creator_video_review.py`，不依赖独立 `video-analysis-api` 项目。外部环境配置只接受显式白名单，不批量导入外部环境文件；保留现有 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_ID`，不得被内容配置覆盖。本次实现验证不执行批准、飞书写入或私信发送，不代表已完成实店全链路验收。

**跟进日历（仅免费样品【处理中】`tab=40`；跟进不分商品，全部主推款都跟进）：** D0 / D+3 / D+7 发话术；D+10 只出名单；D+15 飞书合作状态写 **未发布**（业务含义=未履约，不是「待发布」）。刚到货话术分商品：B005 附讲解图，非 B005 只发话术；3/7 天话术通用。达人发视频/直播 → 平台已完成 + 文档原文感谢话术 + 飞书 **已完成**。达人类型用筛查导出「视频达人/直播达人」；视频+直播同时标记时跟进话术按视频达人，不拆两条。类型未知才人工。不要把【已发货】当已送达。不扫买返。

**发送只认「当前最新应做阶段」（2026-09-10 加）：** 由到货日推算的当前节点之外一律不发——例如到货已 5 天时，5 天前的 D0 任务不得补发。判定用 `is_stale_followup_stage`（`assistant/domain/followup_stage.py`），脚本选择器与网页「发送」按钮共用；被拦下的过期任务转 `needs_review` + `stale_stage`（缺送达日转 `missing_delivery_time`），不发送。日历只在跑「生成今日跟进待办」时推进（不是 cron），所以每天/每次发送前应先跑一次生成，否则待办池会停在旧节点。

**重复发送保护（2026-09-10 加，同日加窗口判定）：** 真发前先打开会话读线程文本与逐条消息，四层挡：① 会话指纹预检 `message_text_fingerprint`（本行话术去掉称呼后的前 60 个归一化字符）命中 → `already-sent`，文本与图片都不发，本地记 `send_confirmation=already-sent`；② 感谢话术保留 `thread_thanks_hold_reason`（`content-thanks-found`/`uncertain-thanks-found`）→ `held` + needs_review（先于窗口判定，命中即转人工，不自动记已发）；③ 窗口判定（与措辞无关）`self_message_in_window_predicate`（`scripts/lib/im_dom.py`）：本阶段「当前应做」的自然日窗口（`stage_window_dates`，arrival=D+0..2、day_3=D+3..6、day_7=D+7..9，与日历同源）内只要有一条**我方发出**的消息，不管谁写的、写什么，一律判 `already-sent`；④ execute 前原子认领 `send_result=sending`，重复/并发运行跳过；⑤ 发送后再读线程确认，确认不了 → `send-unknown` + needs_review，禁止自动重试。网页「预演」与真发共用 ①②③（预演只报告，不写平台；`content_found` 无到货日历，只有 ①②）。**页面契约（2026-09-10 实测）**：消息行是 `.chatd-message`，我方消息带 `chatd-message--right`（气泡 `chatd-bubble-main--self`），对方是 `--left`/`--other`；时间标签在行内 `.chatd-message-time .chatd-time`，按**页面地区 + 页面时区**渲染（同一天实测先英文后中文：`Sep 3 5:55 PM`/`Tuesday, 4:55 AM` → `上午1:43`/`昨天 上午5:46`/`星期二, 4:55 上午`/`9月1日 7:41`/`2025年9月16日 6:28`；2 号店页面时区是美西 GMT-7），**无标签的行沿用上一条的时间**，只有时刻没有日期 = 页面本地「今天」，图片消息是 `.chatd-imageMessage` 且无文本。探针带回 `page_offset_minutes`（`new Date().getTimezoneOffset()`），`inspected_messages` 把它挂到每条消息上，`parse_chat_time_text(..., page_offset_minutes=…)` 先换算成北京时刻再做自然日比较；不换算时中文标签根本解析不出来，窗口判定会静默漏判（2026-09-10 实测）。聊天数面板与「新消息」抽屉都是异步渲染：必须短轮询就绪（`_wait_for_page_flag` + `CHAT_PANEL_READY_JS`/`NEW_MESSAGE_DRAWER_READY_JS`，各 20s）再点下一步，固定 sleep 1s 实测不够（会报 `no-chat-panel`）；`INSPECT_IM_JS` 在导航后无 composer 时也不能抛错（`input` 为 null 要短路）。**局限**：页面地区/时区会变（同一天实测先英文后中文、页面时区美西），新格式要补进 `parse_chat_time_text` 与 `tests/test_im_dom.py`；解析不出时刻或日期的标签一律跳过（不猜），只覆盖已加载的消息（最近一屏），更早的历史要滚动才有；指纹仍只认我们自己模板的措辞——指纹与窗口都没命中时不要 execute，改用详情页「标记已发跟进私信 / 标记已发感谢私信」只记本地完成。

**SQLite 写锁与任务心跳（2026-09-10 加）：** 连接固定 `PRAGMA busy_timeout=30000`（`assistant/database/engine.py`）；心跳失败只重试并记一条 warning，不打堆栈。`STALE_JOB_TIMEOUT_SECONDS=150` 必须明显大于 busy_timeout，否则长事务（生成待办 / 物流同步）期间正在跑的任务会被 `mark_stale_jobs_interrupted` 误判为中断。不要为 `database is locked` 去缩短超时，也不要指望心跳失败能自动区分「任务死了」和「写锁被占」。

**跟进写飞书（2026-09-10 加）：** 合作状态只走 `FollowupService._write_cooperation_status`（未履约 `未发布` / 内容确认 `已完成`），转换守卫只允许 `待发布 → 未发布/已完成`，`未发布/已完成/已发布` 是保护值、其它状态一律拒绝——**不要为了让守卫放行去改判定，也不要替业务补数据**。2 号店样例本地没有 `feishu_record_id`（实测 0/268）：写入前若为空，按 **红人ID（=达人名）+寄样产品** 查行（`resolve_relation_record_id`，`fetch_all` 翻页），**只有唯一命中才写并回填 case**；多行 → `ambiguous-record`、无行 → `no-record`，都转 needs_review `feishu_write_failed`，不新建、不猜。未履约写成功后写 `send_result=unfulfilled-written`（进 `COMPLETED_RESULTS`）结掉待办；写不成（守卫拒绝/多行/无行/接口错）同样转 needs_review，不自动重试。

**跟进语言：** 先读飞书「使用语言」（英语/西班牙语）；无值再 `detect_creator_lang(详情简介)`（`scripts/lib/detect_lang.py`）。有简介时走 LLM JSON 的 `lang`（不看 `confidence`；`.env`：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_ID`，默认 DeepSeek `deepseek-v4-flash`）；空简介、识别失败或非法 lang 默认英语。`sync_shipped_tracking.py` 已是这个优先级。

**提醒话术问候语（2026-09-10 修）：** D+3/D+7 统一成 `Hi {名}! ❤️`／`Hola {名}! ❤️`。原来 D+3 英文是 `Hi{名} ! ❤️`、西语 `Hola{名}! ❤️`（实测渲染成 `Himaideediaz ! ❤️`、`Holamaideediaz! ❤️`），SOP 与 `assistant/domain/message_templates.py` 已同步修正，回归测试 `tests/assistant/test_message_templates.py::test_reminder_templates_greet_with_clean_spacing`。**改话术必须 SOP 与模板同时改**。D0 与感谢话术里还有同类历史写法（`Hola{名} !`、`Hi {名}❤️`、`Hola{名}!`），本次未改。

`detail_targets`：仅 `--with-detail` 只拉列表初判通过行；加 `--detail-all` 才拉全表。试跑限量用 `--max-rows`。

---

## 页面 / 写操作

- 列表：静默读 fiber `record`，勿点名称旁易弹剪贴板的控件。批准只走列表「同意」，不在详情页点同意。
- 详情：直链 `cid=` 或点头像；抽完回列表。
- 私信：只从样品申请页右下角「聊天数」进入，点击「发送消息」，在「发送给」输入达人 ID 后点击结果行右侧「聊天」。成功=选中 `contactCard` 对得上人且有输入框。**不要打开详情消息弹层、不要跳到 `/seller/im`、不要点最近联系人**。发送默认 `onSendText`，不点发送钮（除非 `--write-source dom`）。
- 可点：待审核/已发货 tab、翻页、头像/详情、返回，以及样品申请页聊天数面板中的「发送消息」、达人 ID 搜索结果「聊天」。execute 还可点列表同意、确认弹窗。永不点邀请。
- 进出商家订单页：异步 `location.replace` + 短轮询，禁止阻塞 `visit_page` 回样品申请。从订单等 SPA 子页出发时先 replace 掉历史，避免弹回订单页。
- storeId：显式 > running 精确店名 > running 唯一 > 测试默认 1 号店。ZClaw Bridge `9481` ≠ WebDriver `16851`。
- 跟进/物流私信语言：飞书「使用语言」优先；否则详情简介 `detect_creator_lang`（LLM JSON）；空简介默认英语。已有会话语言不改判。密钥只放 `.env`，勿提交、勿进聊天。

脚本入口：`scripts/open_sample_store.py`、`screen_sample_requests.py`、`send_sample_intro.py`、`sync_shipped_tracking.py`。日常 0 号双击走 `--reopen`。`hero_xlsx.py` 已废。密钥在 gitignore 的 `config.toml`。

---

## Agent 纪律

1. 改自动化前确认：0 号已开店且 `execute_script` 通；页在待审核（或用户允许 `--from-seller-home`，起点可以是已登录商家中心任意子页）；`ziniao-cli doctor`；脚本 1/2 同一 `store_id`。0 号允许关目标店再开；1/2/3 仍不重开、不切店。
2. 用户要批准：只走已有 `--execute --yes`（+ 可选 `--write-feishu`）。提醒 limit、备份、不可撤销。勿另开无门闩路径。
3. 探 DOM 只用 `execute_script`；失败先查：留在详情页、Bridge、错 tab、未全部展开。
4. 与 zn_daren 共用时引用、不复制大段 `zclaw_dom`。
5. 同一错误第二次 → 补一条可验证规则。纪律变更同步 README。删冗长：删掉会不会更容易犯错？不会就删。
6. **页面探测参数/契约先实测再写死**（2026-08-26 连踩三坑的教训）：
   - 跨域导航后 TikTok 卖家/联盟 SPA 加载到 complete 约 55–60s，期间 `execute_script` 单次可阻塞 5–24s（稳态仅 0.5–2s）。探测单次超时 ≥15s（`HREF_PROBE_TIMEOUT_SECONDS`）；页面稳定/导航预算 ≥90s（20–45s 必超时）。改值前先跑 `ziniao-cli zclaw invoke execute_script` 计时实测延迟曲线。
   - 页面 DOM 契约（应用根节点等）选择器须实测真实结构再写：订单页根容器是 `#layout`，不是 `#root/#app/#__next`。
   - 改完代码必须重启操作台才生效（`launch_assistant.py` 已自动 kill 旧实例；旧进程持单实例锁会让新代码永远不加载）。

<!-- ASTRYX:START -->
Astryx v0.4.7 · 158 components
CLI: run every command as `npx astryx <cmd>` (shown below as `astryx ...`).

SETUP (once, in your app entry e.g. main.tsx) — without these, components render unstyled:
  import "@astryxdesign/core/reset.css";
  import "@astryxdesign/core/astryx.css";

WORKFLOW — discover, don't guess. Before writing UI:
1. `astryx build "<idea>"` — START HERE: returns a kit (closest [page] + [block]s + [component]s). No args = full playbook.
2. `astryx template <name> [--skeleton]` — scaffold the [page]/[block]s it named, or study their layout. Templates are reference code.
3. `astryx component <Name>` — props + examples for every component you use.

RULES:
- No <div> — components do all layout/spacing, page frame included.
- Frame first: read `astryx docs layout` before writing any page or screen — page frame, region widths, breakpoint behavior.
- Dense data = rows (Table, List/Item), never Card-wrapped list items; Card is for standalone widgets. Status = StatusDot/Token; Badge = counts only.
- Custom styling: component props first; else style/className with tokens — var(--color-*|--spacing-*|--radius-*). No raw hex/px. (No StyleX/Tailwind compiler here — don't use xstyle/utility classes.)
- Tokens for every value (`astryx docs tokens`). Brand/accent belongs in the theme (`astryx theme list` / `theme add <slug>`, or `astryx theme template` for a custom one) — never override --color-* in :root.
- SELF-CHECK before you finish: re-read the file and replace any raw <div>/<span> layout, imported .css/@apply, or hardcoded value (#hex, 16px) with the component or a token (var(--color-*|--spacing-*|…)). If unsure a component/prop exists, run `astryx component <Name>` / `astryx search "<thing>"`; don't hand-roll CSS.

MORE CLI:
  search "<query>"   find any component / hook / doc / template / block
  component --list   158 components by category
  template --list    page + block recipes
  docs <topic>       browser-support, cli-integrations, color, elevation, getting-started, icons, illustrations, internationalization, layout, migration, motion, principles, shape, spacing, styling-libraries, styling, theme, tokens, typography, working-with-ai
  swizzle <Name>     eject component source for deep customization
  upgrade --apply    run after any @astryxdesign/core bump
<!-- ASTRYX:END -->
