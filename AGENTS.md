# zn_sample · AGENTS.md

本文件给 **Agent / 技术维护**：纪律、数据 ID、实现契约。  
日常双击 / 出问题只写 [README.md](./README.md)。新电脑配机只写 [快速开始.md](./快速开始.md)。规则原文见 `样品申请筛查sop/1-样品申请筛查sop.md`；到货跟进见 `样品申请筛查sop/2-查看到货+达人跟进.md`（忽略飞书 wiki 里的 B006-A 与旧的「第五天 / 每隔两天」）。

按任务找现行规范、源码与测试见 [仓库任务导航](./docs/navigation.md)。历史归档不作现行操作依据，本地计划不覆盖现行契约；计划引用须带目录名，不用裸编号。

紫鸟启停 / GUI vs WEBDRIVER / `ziniao-cli`：**先读** [`../zn_daren/AGENTS.md`](../zn_daren/AGENTS.md)。本仓默认 **GUI + 已 open 的店**。样品链勿混用 `zn_daren` 的 `--execute` 取消逻辑；定向邀请取消只走本仓独立「计划清理」链，不运行兄弟仓代码。禁止 `ziniao-cli page extract --mode running`（可能 `runtime.reopen`）。

不要改系统或启动器 `PATH`。Windows 上 `ziniao-cli.cmd` 只解析成 `node` + `run.js` 的**绝对 POSIX 路径**（`C:/...`），子进程固定 UTF-8。日志用 `logging`，入口 `configure_logging`，库代码 `getLogger`。

依赖统一口径只写在 `setup/install_deps.py` 顶部常量里（Python 3.12、`@ziniao-open/cli` 1.0.7、vite 7 的 Node 范围），Windows 入口 `setup/安装依赖.bat`、macOS 入口 `setup/install_deps.sh`；改依赖 = 改锁文件 + 常量，再在每台机器重跑一次脚本。0/1/2/3 与「启动操作台」入口一律**优先用仓库 `.venv`**（`uv sync` / 安装脚本建立），没有才回退系统 Python——不要再改回「只用系统 Python」，新机器会缺 `httpx` 等依赖。

---

## 非协商

1. **默认禁止写操作**  
   不点：同意 / 批准 / 拒绝 / 发货 / 私信「发送」/「邀请」。  
   默认只读列表、详情、本地判定、导出。  
   **仅** `--execute --yes` 可对筛查通过行点「同意」（永不拒绝）或发第 7 / 10 步私信。
   写飞书另加 `--write-feishu`。测试默认不写飞书。  
   `--execute-limit` 默认 1，不得新参数绕过。批准前写 `*_pre_execute.*`。平台同意与已发私信不可脚本撤销。
   样品批准的隔离例外：「自动批准」页的自定义规则链走自有任务链（见下文「自动审批子页面」），
   仍受 `--execute --yes`、同一执行限额、去重、备份与核对保护，且只接受服务器生成的自定义快照。
   独立取消例外：「计划清理」只处理服务器生成的 2/4 自然月定向邀请快照，网页确认 `y` 后仍须 `--execute --yes`。确认的是该快照全部候选，不提供任意范围/限额；这不改变样品批准和私信的默认限额 1。取消不可脚本撤销，测试不执行真实取消。
   达人跟进详情页的「发送这条跟进私信 / 发送感谢私信」是同一个门闩的网页入口：固定单条 `task_id`、每次最多 1 条、任务运行中不可取消，底层仍走 `send_followup_message.py --execute --yes` 与原子认领。
   独立 D+15 例外：「达人跟进」批量写飞书未发布只接受同一本地店的当前候选 `task_ids`、正整数 `execute_limit`（默认 1）及严格 `y` 确认。固定 `followup_unfulfilled_write` Job 调 `mark_unfulfilled_followups.py --execute --yes --write-feishu`，用户明确上调上限，不接受 0=不限量。不碰紫鸟/私信、不要求 running 店，运行中不可取消/重启；不将参数权限推广至旧入口。
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

3. **默认店（筛查/物流 = 1 号；开店 = 2 号）**  
   未传 `--store-id` / `--store-name` 且 running 无法唯一解析时用默认店。生产/多店必须显式 ID。`--no-default-store` 禁用默认。  
   默认店由 `config.toml [stores]` 决定（`default_store_id` 给 1/2/3，`prepare_store_id` 给 0 号；环境变量 `ZN_SAMPLE_STORE_ID` / `ZN_SAMPLE_PREPARE_STORE_ID` 优先），**换客户/换公司只改这份配置，不改代码**；未配置时才用代码内置默认（1 号店 `27437742526069`、2 号店 `27506607043054`）。  
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
| 本地网页操作台 | 只读日常更新；网页保留 0（运行准备）、只出名单（只读筛查）、物流写回、跟进动作（含跟进详情页单条发送）、自动批准页的「订单号补写」与独立「计划清理」，复用 `operator_launch` 或固定 handler；正式筛查-批准只走脚本 | 不接受任意命令/参数；网页写任务仍须明确输入 `y`，物流写回在 16:00 前另须 `FORCE`；最终仍只走既有 `--execute --yes` 门闩 |
| 进待审核 | 开店 + `--from-seller-home` | 不代办登录，不擅自切店 |
| 初筛+复筛+内容审查 | `--with-detail --require-detail`；第 4 步销售数据通过后执行第 5 步内容审查 | 不能把仅列表结果或没有内容通过证据的结果当正式通过名单 |
| 批准 | `--execute --yes`；默认已捕获的窄 API | 不能猜 endpoint、扩大接口、绕过门闩；`shadow` 禁止配合 `--execute`；API 路径尚未用第二条真实申请重复验收 |
| 写达人关系 | 写接口接受后 + `--write-feishu`；`--confirm-export` 可**补写** | 不是主推表；写结果未知时不先写 |
| 第 7 / 10 步私信 | 独立脚本；严格走样品申请页「聊天数」→「发送消息」→输入达人 ID→「聊天」的新路径，默认 IM SDK | 批准后不自动发；不打开详情消息弹层、不跳到 `/seller/im`、不点「聊天数」里的最近联系人 |
| 第 8–9 步物流 | 独立脚本；**北京时间 16:00 前拒绝** | 先飞书近 7×24 小时且合作状态=待发货（主键红人ID+寄样产品），再对已发货。`main_order_id` 不是发给达人的单号；`--force` 只过时间门。物流 GET 对齐订单页 query（`oec_seller_id`/`seller_id`/`aid`）；订单 URL 用 `seller.us`，不用 apex。紫鸟 `error.html` 当跳转失败 |

读路径：`auto` 日常推荐（API 失败回退 DOM）；`api` 失败即报错。页面 API 用异步 `fetch` + `request_id` 轮询（兼容 2 号店同步 XHR 空响应）。批准默认 `--write-source api`，DOM 须显式指定。简介仍走详情 DOM。

**详情接口系统级故障熔断**：API `business_code=100000` 或错误串含 `code=100000` / `Please remove the plugin` 视为逐行复现的故障（TikTok 反爬判定浏览器环境/插件流量）。`auto` 从首个已知系统错误起就不回退 DOM；`auto/api` 连续 3 次真实采集命中（`SYSTEMIC_DETAIL_FAILURE_LIMIT`）才停止本轮后续 API/DOM 与等待，剩余行标 `detail-api-systemic-failure` → needs_review，只记一条汇总。真实成功、普通失败或普通异常清零连续性；跳过/缓存不增不清，取消直接传播，新任务重新计数。普通 API 失败仍可 auto DOM 回退，显式 dom/shadow 权威与既有失败处理不变。禁止把该错误当成单行失败继续刷表；出现后人工检查插件/浏览器环境或等平台恢复，不自动关插件或重开店。实测证据见 `达人资料补齐接口故障排查-20260906.md` 顶部更正。

---

## 操作台页面（Astryx React 壳）

导航固定九项：总览 / 运行准备 / 自动批准 / 达人跟进 / 计划清理 / 物流 / 任务 / 报表 / 诊断（含各自详情页）。共用导航同时覆盖独立自动批准页。正文由 React + Astryx 渲染，源码 `frontend/src/console/`，构建 `npm run build:console`（或 `npm run build:web` 同时构建自动批准），产物 `assistant/web/static/console/` 被 gitignore。

- 服务端只渲染 `assistant/web/templates/console.html` 并注入 bootstrap JSON：页面数据在 `assistant/web/console_pages.py`，契约同步 `frontend/src/console/types.ts`。不要新增 Jinja 页面模板或手写页面 CSS。
- **入口按页归属**（`operator_groups(page)`）：`/prepare` 打开店铺；`/auto-approval` 顶部只出名单（只读筛查）；`/followups` 物流同步 + 物流写回（16:00 门）+ 补齐资料 + 生成待办 + 预演感谢私信。**正式 SOP 的「筛查-批准-写飞书-私信」已从网页移除，只走脚本 1/2**；`/api/jobs/operator/pipeline` 端点保留兼容，不在任何页面暴露。
- `assistant/web/static/app.js` 仍负责任务面板、确认弹窗与表单提交：表单是 document 级事件委托（`data-job-form` / `data-job-cancel`），React 后挂载也能绑定；任务详情页挂载后派发 `assistant:monitor-job` 启动监控；准备状态由总览 / 运行准备页轮询，自动批准页只显示状态条 + 去准备页链接（不重复探活）。
- 「计划清理」只读恢复批次，不探活、不自动扫描。表单成功由 `app.js` 派发 `assistant:job-created`（`payload` + `form`）；页面只读取回执/更新 URL，不再自行 POST 或实现第二个确认弹窗。捕获阶段阻止的提交必须由 document 委托尊重 `defaultPrevented`。
- 任务面板与确认弹窗抽到 `_task_panel.html`，`console.html` 与 `auto_approval.html` 共用；两个壳都加载 `app.js` 与 `console-shell.css`。
- 静态资源按产物 mtime 加 `?v=`（`_static_version()`），避免浏览器缓存旧 JS/CSS。
- **主题 CSS 必须在组件产物之后加载**（`geist-theme.css` 放 `console/assets/index.css` / `auto-approval/assets/index.css` 之后）。产物里默认主题在 `@layer astryx-base`，主题文件在 `@layer astryx-theme`；layer 顺序按首次出现决定，主题先加载会被默认值压住（表现为 Meta 蓝 `#0064E0` + 系统字体，而不是 Geist 蓝 `#0070f3` + Geist 字体）。主题主色是蓝，勿改回黑。
- `assistant/web/static/console-shell.css` 只保留 app.js 动态生成 DOM 与任务面板 / 确认弹窗所需类，禁止裸元素选择器，避免污染 Astryx 组件。
- 页面级测试改断言 bootstrap JSON（`tests/assistant/console_payload.py`），不再匹配已不渲染的 HTML。

---

## 计划清理（独立定向邀请取消链）

`/plan-cleanup` 复用 console 壳。实现：`assistant/api/target_cleanup.py`、`assistant/services/target_cleanup_service.py`、固定 handler `assistant/jobs/handlers/target_cleanup.py`、入口 `scripts/cleanup_target_plans.py` 与 `lib/target_plan_cleanup.py` / `target_invitation_dom.py` / `target_invitation_navigation.py`。此链不批准样品、不写飞书、不发私信；源仓保留为旧工具，禁止与工作台并跑，现有锁不承诺跨项目互斥。

- **固定业务**：只接受整数 2 或 4；按上次修改日 `< 本机运行日往前 N 个自然月`，排除截止日，月底/闰年夹到有效日期。忽略已接受/已推广人数，页面及确认均展示非零人数候选数量。确认后处理固定快照全部候选，不新增自由勾选、任意日期、店铺覆盖或执行限额。
- **身份与导航**：GUI + ZClaw，必须恰好一家 running，并绑定紫鸟 storeId 与平台 shop_id/region；无默认店回落、不自动开/关/重开/切店。沿用已知定向合作路径和分页契约（末页向前、100/页、最多 50 页、间隔 1.2 秒）；每次取消后按「进行中 → 错误恢复 → 100/页 → 末页」恢复，结束留在定向合作，不擅自回样品页。
- **错误页到达与就绪（2026-10-06）**：合法目标 URL/唯一非空 shop_id/region 与可见进行中 tab 只证明可恢复页面壳，不是数据就绪。首次到达和写后共用「壳 → 进行中 → 最多 3 次 Retry → 严格列表就绪 → 100/页 → 末页 → 最终就绪」；最终仍须无列表错误、document complete、分页或真实空态和实际进行中。导航与恢复共享一次 90 秒 deadline，读探针上限 15 秒，sleep/局部页大小等待不续期；失败不外层重导航/另起恢复轮。进行中、Retry、页大小 trigger/option、末页每次副作用前须在同一 JS 校验已绑定 host/path/shop/region；登录、缺上下文或换店拒点。无 Retry、三次仍错或 loading 超时即失败，不能伪造完整预览或把未知取消洗成 submitted。本项只用离线 transport/合成 DOM 验收，不授权真实探店或重启。
- **恢复读取与末页到达**：仅只读探针的 TimeoutError/TimeoutExpired 或既有 Bridge 网络错误分类可在原 deadline 内重读；页面身份拒绝、无效状态或其它异常立即停止。Retry/页大小/末页等点击及取消/确认的回执丢失不重放。末页动作仅一次，随后用同店只读抽表与既有 total/页码/100 行覆盖判据等待实际末页；最大可见页码或点击回执不证明末页，真实空态保留。写后到达失败仍 uncertain，下一候选不写；不扫描全表证明原 ID 消失。
- **写前导航失败收尾**：仅当前已领取 child 在进入 execute_snapshot 前的明确导航异常路径，可事务核对 running Job/batch、绑定快照/hash/信封和逐项原证据，且无尝试/动作/返回、备份、结果或受保护历史后，发布不可覆盖的 not_processed 结果并关闭该 claim。不是由 pending/缺 journal 推断无写，也不接受前端无写布尔量；写阶段已开始后的异常/崩溃、缺失或无效 journal 仍沿用 uncertain 保护。所有权不符、重复领取或存在矛盾证据均拒绝此收尾，不清除 attempting/submitted/uncertain。
- **完整性与时效**：首端、日期边界或真实空态须有证据；非空页必须有可信非负整数 total，实际行数须与 total、页码和 100/页一致。缺失/不可解析总数、非空 total=0、页码越界或部分挂载均 incomplete（`page-total-unverified` / `page-rows-incomplete`），同一判据用于预览、定位、写前抽表及回读；100/页标签或下一页禁用不能替代覆盖证据，真空态仍须身份/进行中/就绪/empty-state 证据。上限、翻页失败/停滞、末页未确认、乱码/未知日期均 incomplete，不为有名单而放行。冻结运行日、UTC、时区/偏移与 cutoff；完成后 30 分钟有效，执行开始仍须同一本机日历日，长扫描不消耗这 30 分钟。GET 只读本地证据，不探店；页面 `can_execute` 不是省略执行时复核的许可。
- **预览版本**：现行实现 `target-cleanup-v2`；旧 v1 快照即使 hash 正确且未过期也拒绝新执行/启动领取，不重扫、不改签旧工件。已执行批次仍按原 JSON/数据库恢复结果，历史 submitted/uncertain/attempting 阻断不因升级清除；未知总数文案需另取真实只读证据，不猜 selector/字段。
- **异步 UI 与取消（2026-10-06 用户确认恢复）**：沿用 zn_daren 的按 ID 菜单取消和可选确认逻辑，不运行兄弟仓脚本，不新增另一套按钮/任务链。已移除固定 `cancellation-ui-contract-unverified` 阻断；执行仍须快照 + y + execute/yes。箭头/取消/可选确认均单次，等待用 Python monotonic 只读轮询（菜单 3 秒、可选确认 4 秒），不 busy-wait、不重放写调用。菜单用已实测 React record.id 关联；可选确认仅处理本次新挂载且对应 onOk/okText 的按钮，不点旧弹窗或保留按钮。无确认是正常路径，confirmed=false，不恒置 true。按用户接受的源工具提交口径，目标取消点击有回执、可选确认处理无错误且列表恢复成功即记 submitted；不新增逐条全表消失核对，gone=None，不宣称平台最终取消成功。100/页沿用单次 trigger/option + 有界只读等待与标签回读，真空态例外保留。历史防重/备份/未知写止批不变，运行任务不为加载新代码被重启。
- **服务器所有权**：两表 `TargetCleanupBatch` / `TargetCleanupItem` 与迁移 `0007_target_cleanup`；候选 JSON 严格 schema + 数据库 SHA-256 + 逐行依据，CSV 从不作为执行输入。POST 只接受固定 FormData 字段，拒绝未知/重复/文件字段与 query 参数；确认只认 `.strip().lower() == 'y'`，不认 yes。脚本必须绑定服务器 running Job 和一次性批次，执行双开关缺一先退出 2。
- **领取与保护**：批次、Job、请求指纹在 `admission_guard` + `BEGIN IMMEDIATE` 中提交；同键同请求返回原任务，同键异月份/批次拒绝。`target_cleanup_preview/execute` 均占店铺页面，忙碌检查先于探店；execute 登记写类型及源码重启保护。两者 running 均不可停止，pending 仍按 `can_request_cancellation()` 取消；等待人确认时不持有页面任务。
- **写前/未知保护**：备份及初始 JSON/CSV 检查点失败不点；每行先复核固定 ID 与原修改日，再持久化 attempting，才调用一次取消。定位恢复与写调用分开，取消/确认响应丢失、响应不匹配或恢复失败标 uncertain，立即停止剩余行；没有可选确认框本身不算失败。历史同店同 ID 的 submitted/uncertain/attempting 阻断新执行；崩溃尝试转人工，不因换预览、换键或重启而重试。只展示“取消操作已提交”，不是“平台已确认取消”。
- **工件恢复**：全部写用户目录 `exports/target_cleanup/`，文件名含 batch_id；候选/快照不变，结果逐行原子更新。中断后 DB 保留结论，派生结果 CSV 在提交恢复后重建；重建失败不提供旧结果链接，原 JSON 检查点保留。仅在未消费、无 Job/尝试/备份/结果证据且旧信封绑定可验证时恢复孤儿执行信封，其他情况保持人工核对，不能删工件绕过去重。
- **验收边界**：现行维护主线是 zn_sample，发布资源显式登记新模块；不运行兄弟仓 import/脚本或复制配置/历史。实现验收只做离线合成测试；真实只读预览、整份快照取消与 Windows 原生验收均须另行安排/授权，不因测试通过执行平台写入或重启正在工作的操作台。

---

## 自动审批子页面（自定义审核方案）

`/auto-approval` 是独立 React 页（构建产物 `assistant/web/static/auto-approval/`，源码 `frontend/`，构建 `npm run build:auto`；产物被 gitignore）。后端：`assistant/api/auto_approval.py` + `assistant/services/auto_approval_service.py` + `scripts/auto_approval.py`（四种 mode：`preview|execute|reconcile|order-backfill`）+ `scripts/lib/auto_approval_rules.py`；任务类型 `auto_approval_preview|execute|reconcile|order_backfill`（已入 ZINIAO_JOB_TYPES 与写任务保护集，启动器 PROTECTED_JOB_TYPES 同）。候选筛选层级（L0–L5）见 [docs/spec/自动审批候选筛选流程.md](./docs/spec/自动审批候选筛选流程.md)。

- **隔离，不是默认**：正式 SOP、`filters.Criteria`、0/1/2/3 入口行为不变。自定义规则只绑定本次任务快照；关闭检查 = not_checked，≠通过。正式入口与旧导出继续严格走 `_guard_content_review()`；本页批准只接受服务器生成的自定义快照（preview 信封 + rule hash + 逐项证据），禁止前端布尔量绕过内容门禁。
- **规则 schema 严格**（`validate_custom_rule`）：未知字段、非有限数字、非法范围、空条件组合、非主推商品都拒绝。至少启用一项检查；比较符第一版固定 `>`（客单价为区间）；`video_live` 组 either/both，both 须两侧都启用。
- **第一版后端边界**：执行限量为正整数（默认 1，无上限）；预览新鲜度 24 小时；内容组 days 仅 7、min_related 仅 4（与内容审核库一致，前端锁定）；数值上限见 `BASIC_LIMITS`/`VIDEO_LIVE_LIMITS`。
- **判定语义**：启用指标缺失或违反 → failed（不通过）；完整满足 → passed；仅详情采集失败（`detail_error`）导致缺失 → needs_review，且批次禁止执行。视频/直播任一侧通过即过；AND 组已知失败即失败。主推/当前款/`can_be_approved=false`/缺 ID 为**执行拦截**（blocked），不属审核项。
- **按需采集**：仅 video/live 组启用才拉详情；详情字段优先（履约 `est_post_rate`、官方 GPM、客单价 `aov_detail`），缺失回退列表口径（GPM 近似标记 source=list-proxy）。内容审核只跑自定义已通过行，与正式链路同一 `review_creator_rows`/`validate_content_review` 证据。
- **自定义 preview 安全预筛/复用**：`evaluate_custom_precheck` 与完整判定共用列表确定项、单条 SKU 和安全 blocks；详情启用时 GPM/AOV/履约列表低值或缺失不能提前淘汰。短路行启用详情项为 needs_review + value=None + 未采集说明，不设 detail_error/detail_checked，不删除申请；已知失败优先、SKU 缺失待复核、blocked 独立。`defer_detail` 只供 preview 显式调用且自行复核停止证据，默认 evaluator/批准复算不读行里的 skip/defer 字段。cache 仅本轮 `(store_id, creator_id)` 成功 API 指标白名单深拷贝；全快照同 ID 名字去首尾空白后须一致且非空（只防冲突，不证明 handle），身份靠 API 请求 creator_oec_id。DOM/失败/取消不缓存，成功缺指标可缓存；不覆盖申请/商品/SKU/是否可批准/checks，不跨任务持久化。缓存命中不请求/等待、不增清熔断连续数；熔断开启优先于缓存，真实失败仍令批次不完整。新鲜请求的等待/超时/重试及正式 SOP 不变。
- **执行边界**：服务端校验 preview 完成/完整/新鲜 + 候选 ⊆ eligible + 限量 + 键入 `y` + 幂等键（重复点击/重试去重）；脚本内再验信封/店铺/hash、逐行复算结论、hero 重查、产品解析、查重、`check_pending_application_api` 预检后批准（`--write-source api` 固定）；批准未知 → 不写飞书、不自动重试。写飞书默认关，目标仍「达人关系管理(新)」。reconcile 复用正式 confirm 管线（不重批）。
- **任务与页面**：写任务运行中不提供取消；启动器 `PROTECTED_JOB_TYPES` 含 auto_approval_execute/reconcile/order_backfill。页面网络异常先查任务状态，不重复创建批准任务。改规则立即令旧预览失效。
- **订单号补写（页面第 5 节，2026-09-18 起）**：`auto_approval_order_backfill` 与 `scripts/auto_approval.py --mode order-backfill`；规则固定「人员=王良希（技术）+ 系统 created_time 近 72 小时 + 订单号为空」（`lib/order_backfill.py`），**不选批次、不需要先跑只读核对**。只读扫描（`GET /api/auto-approval/order-backfill/candidates`，Web 进程直接读飞书，不占店铺页面）只用于展示；写任务**对每条候选到后台按达人 ID 逐个搜索**（`PLATFORM_SEARCH_TABS` = 待发货 20 → 已发货 30 → 处理中 40 → 已完成 100 → 待审核 10，拿到有效单号即停止继续搜后面的 tab），按「红人ID+寄样产品」选中申请（同一达人可能有多笔，必须两个键都对上），逐行 `update_record_order_no` 只写「订单号」一列并立即回读。**禁止只扫「待发货」整表**：已发货/处理中的行不在 tab=20，会整批漏掉（2026-09-18 实测三条）。页面仍须键入 `y`（`confirmation`），任务去重 + 店铺页面互斥；默认限量为 0（不限量，候选有界且重复运行幂等）。逐行结论 `written|unchanged|conflict|write-uncertain|no-platform-order|ambiguous`，报告带 `platform_tab`（来源 tab）与 `search_errors`（逐候选逐 tab 的搜索失败）；`write-uncertain` 禁止自动重试，后台未刷新时保持 `no-platform-order`、下次重扫自动带上。`--write-feishu 0` 为纯核对（`planned`，不写远端）。
- **常驻核对与补写（按历史批次，脚本/维护用；页面已移除）**：历史批次独立于当前预览。`auto_approval_reconcile` 在 `write_feishu=false` 时只回读平台/飞书，无需 `y`；补写须 `y` + 15 分钟内成功核对的缺失证据 + 原批次限量，执行前重查、执行后回读。核对不改原批准批次 `status/job_id`；每次任务独立报告，失败仍保留批次/报告指针及不确定写入检查点。只接受绑定 execution/store/hash 的服务器快照；历史核对可跳过预览新鲜度，但新批准始终校验 24 小时。查询失败不是缺失；重复记录、不同订单号、未知写入未找到记录禁止补写。平台确认仍只认待发货；后续阶段交物流/人工，不重批。

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

网页不是新的业务写路径：后端只登记 `prepare|screen|pipeline|tracking|followup_send` 五个固定任务，使用启动操作台的 `sys.executable` 直接调用既有编排（`followup_send` 走固定 handler 调 `send_followup_message.py`），不执行 `.command`/`.bat`、不接受 shell 字符串、不允许网页覆盖 store/limit/source 等参数。网页只暴露 `prepare`（运行准备）、`screen`（自动批准页只出名单）、`tracking`（达人跟进页物流写回）和 `followup_send`（达人跟进详情页单条发送，固定 `task_id`、限 1、键入 `y` 确认）；`pipeline` 端点保留兼容但不在任何页面暴露，正式筛查-批准只走脚本 1/2。

**网页取消（2026-09-18 加）：** 运行中的任务只有登记了安全检查点的类型可以取消，`can_request_cancellation()`（`assistant/jobs/registry.py`）是唯一判据，页面按钮直接读 `/api/jobs/{id}` 的 `can_cancel`，不要在前端再写一份名单。写任务里只有 `operator_tracking` 在列：服务端只写哨兵文件（`assistant/jobs/locks.py` 的 `cancel_flag_path`，经 `ZN_SAMPLE_CANCEL_FLAG` 传给子进程），子进程在 `sync_shipped_tracking.py` 的**每一行边界**检查并停下（退出码 3 → `JobCancelled`），已写入的飞书行和已发私信保留、任务显示「已取消」；绝不从一次写入中间杀进程。平台同意 / 单条私信 / 自动审批写任务运行中仍不可取消；取消只代表「不再处理剩余行」，不是撤销。

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

**视觉候选调度（2026-10-03 修）：** 相关不足 4 条不花视觉额度，额度为 0 也继续元数据计数（受既有 deadline/页数/条数上限约束）；达到 4 条后按采集顺序回看已有候选（包括前三条），visual 写回原 proof item，首次正面展示即停。调用前推进游标并扣共享预算，单条失败不重试、只记白名单原因并继续下一条，取消直接传播。默认全批最多 5 次尝试、600 秒内容预算，失败/视觉缓存命中也占一次；已有至少 4 条时额度/时间耗尽立即停并保留 partial。队列审完且仍有额度才继续采集；不改 proof/HMAC/缓存验证或分页排序假设。

内容能力使用本地 `scripts/lib/tiktok_creator_videos.py` 与 `scripts/lib/creator_video_review.py`，不依赖独立 `video-analysis-api` 项目。外部环境配置只接受显式白名单，不批量导入外部环境文件；保留现有 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_ID`，不得被内容配置覆盖。本次实现验证不执行批准、飞书写入或私信发送，不代表已完成实店全链路验收。

**跟进日历（仅免费样品【处理中】`tab=40`；跟进不分商品，全部主推款都跟进）：** D0 / D+3 / D+7 发话术；D+10 只出名单；D+15 飞书合作状态写 **未发布**（业务含义=未履约，不是「待发布」）。刚到货话术分商品：B005 附讲解图，非 B005 只发话术；3/7 天话术通用。达人发视频/直播 → 平台已完成 + 文档原文感谢话术 + 飞书 **已完成**。达人类型用筛查导出「视频达人/直播达人」；视频+直播同时标记时跟进话术按视频达人，不拆两条。类型未知才人工。不要把【已发货】当已送达。不扫买返。

**发送只认「当前最新应做阶段」（2026-09-10 加）：** 由到货日推算的当前节点之外一律不发——例如到货已 5 天时，5 天前的 D0 任务不得补发。判定用 `is_stale_followup_stage`（`assistant/domain/followup_stage.py`），脚本选择器与网页「发送」按钮共用；被拦下的过期任务转 `needs_review` + `stale_stage`（缺送达日转 `missing_delivery_time`），不发送。日历只在跑「生成今日跟进待办」时推进（不是 cron），所以每天/每次发送前应先跑一次生成，否则待办池会停在旧节点。

**重复发送保护（2026-09-10 加，同日加窗口判定）：** 真发前先打开会话读线程文本与逐条消息，四层挡：① 会话指纹预检 `message_text_fingerprint`（本行话术去掉称呼后的前 60 个归一化字符）命中 → `already-sent`，文本与图片都不发，本地记 `send_confirmation=already-sent`；② 感谢话术保留 `thread_thanks_hold_reason`（`content-thanks-found`/`uncertain-thanks-found`）→ `held` + needs_review（先于窗口判定，命中即转人工，不自动记已发）；③ 窗口判定（与措辞无关）`self_message_in_window_predicate`（`scripts/lib/im_dom.py`）：本阶段「当前应做」的自然日窗口（`stage_window_dates`，arrival=D+0..2、day_3=D+3..6、day_7=D+7..9，与日历同源）内只要有一条**我方发出**的消息，不管谁写的、写什么，一律判 `already-sent`；④ execute 前原子认领 `send_result=sending`，重复/并发运行跳过；⑤ 发送后再读线程确认，确认不了 → `send-unknown` + needs_review，禁止自动重试。网页「预演」与真发共用 ①②③（预演只报告，不写平台；`content_found` 无到货日历，只有 ①②）。**页面契约（2026-09-10 实测）**：消息行是 `.chatd-message`，我方消息带 `chatd-message--right`（气泡 `chatd-bubble-main--self`），对方是 `--left`/`--other`；时间标签在行内 `.chatd-message-time .chatd-time`，按**页面地区 + 页面时区**渲染（同一天实测先英文后中文：`Sep 3 5:55 PM`/`Tuesday, 4:55 AM` → `上午1:43`/`昨天 上午5:46`/`星期二, 4:55 上午`/`9月1日 7:41`/`2025年9月16日 6:28`；2 号店页面时区是美西 GMT-7），**无标签的行沿用上一条的时间**，只有时刻没有日期 = 页面本地「今天」，图片消息是 `.chatd-imageMessage` 且无文本。探针带回 `page_offset_minutes`（`new Date().getTimezoneOffset()`），`inspected_messages` 把它挂到每条消息上，`parse_chat_time_text(..., page_offset_minutes=…)` 先换算成北京时刻再做自然日比较；不换算时中文标签根本解析不出来，窗口判定会静默漏判（2026-09-10 实测）。聊天数面板与「新消息」抽屉都是异步渲染：必须短轮询就绪（`_wait_for_page_flag` + `CHAT_PANEL_READY_JS`/`NEW_MESSAGE_DRAWER_READY_JS`，各 20s）再点下一步，固定 sleep 1s 实测不够（会报 `no-chat-panel`）；`INSPECT_IM_JS` 在导航后无 composer 时也不能抛错（`input` 为 null 要短路）。**局限**：页面地区/时区会变（同一天实测先英文后中文、页面时区美西），新格式要补进 `parse_chat_time_text` 与 `tests/test_im_dom.py`；解析不出时刻或日期的标签一律跳过（不猜），只覆盖已加载的消息（最近一屏），更早的历史要滚动才有；指纹仍只认我们自己模板的措辞——指纹与窗口都没命中时不要 execute，改用详情页「标记已发跟进私信 / 标记已发感谢私信」只记本地完成。

**SQLite 写锁与任务心跳（2026-09-10 加）：** 连接固定 `PRAGMA busy_timeout=30000`（`assistant/database/engine.py`）；心跳失败只重试并记一条 warning，不打堆栈。`STALE_JOB_TIMEOUT_SECONDS=150` 必须明显大于 busy_timeout，否则长事务（生成待办 / 物流同步）期间正在跑的任务会被 `mark_stale_jobs_interrupted` 误判为中断。不要为 `database is locked` 去缩短超时，也不要指望心跳失败能自动区分「任务死了」和「写锁被占」。

**跟进写飞书（2026-09-10 加）：** 合作状态只走 `FollowupService._write_cooperation_status`（未履约 `未发布` / 内容确认 `已完成`），转换守卫只允许 `待发布 → 未发布/已完成`，`未发布/已完成/已发布` 是保护值、其它状态一律拒绝——**不要为了让守卫放行去改判定，也不要替业务补数据**。飞书当前还是「待发货」的 D+15 行（业务没把状态补到「待发布」）**保持 pending 等业务改**，不要去点它把本地任务推成人工、更不要放宽守卫（2026-09-10 业务口径）。2 号店样例本地没有 `feishu_record_id`（实测 0/268）：写入前若为空，按 **红人ID（=达人名）+寄样产品** 查行（`resolve_relation_record_id`，`fetch_all` 翻页），**只有唯一命中才写并回填 case**；多行 → `ambiguous-record`、无行 → `no-record`，都转 needs_review `feishu_write_failed`，不新建、不猜。未履约写成功后写 `send_result=unfulfilled-written`（进 `COMPLETED_RESULTS`）结掉待办；写不成（守卫拒绝/多行/无行/接口错）同样转 needs_review，不自动重试。

**跟进语言：** 先读飞书「使用语言」（英语/西班牙语）；无值再 `detect_creator_lang(详情简介)`（`scripts/lib/detect_lang.py`）。有简介时走 LLM JSON 的 `lang`（不看 `confidence`；`.env`：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_ID`，默认 DeepSeek `deepseek-v4-flash`）；空简介、识别失败或非法 lang 默认英语。`sync_shipped_tracking.py` 已是这个优先级。

**D+15 批量未履约（2026-10-08）：** 候选与单条真写共用 `FollowupService`，实际送达至少 15 个北京自然日、当前 unfulfilled、processing 非 stale、无已确认内容且未完成。只有类型缺失/歧义的 needs_review 可作为非私信候选，不放行其它人工原因；语言/类型不新增写状态门槛。原子条件更新 `unfulfilled-writing` 后 commit，远端调用不持 SQLite 写事务；成功/unchanged 记 `unfulfilled-written`。飞书精确「待发货」返回 waiting-business-update 并恢复 pending 等业务更新；其他明确拒绝转 feishu_write_failed。未知写/未分类异常记 `unfulfilled-write-unknown`、needs_review 并止批，崩溃 writing 不自动释放；生成/刷新待办不得清除 writing/unknown/feishu_write_failed。冻结排序名单按限額截取，拒绝也消耗预算，不补挑；每行前复核当前北京时间。写前备份及初始 JSON/CSV 失败不写，每行结果原子保存失败不继续。工件在用户 `exports/followup_unfulfilled/`，默认 CLI 仅本地预览；脚本显式店 ID，无默认回落/探店/迁移/worker。网页请求冻结范围入 Job，同类型同店 pending/running 复用原范围，失败保留逐行报告，不自动重试；发布资源登记脚本/handler。真实飞书验收另行授权，不与人工批量/旧脚本并跑。

同一 case 的旧 D+15 writing/unknown/feishu_write_failed 也阻断更正送达日后的新排期任务：候选、单条复核与原子认领共用历史保护，不能靠重新生成任务绕过。写后工件失败时保留最新 DB 逐行结果，清空过期 JSON/CSV 下载指针；handler 不以旧文件覆盖该失败摘要。

**提醒话术问候语（2026-09-10 修）：** D+3/D+7 统一成 `Hi {名}! ❤️`／`Hola {名}! ❤️`。原来 D+3 英文是 `Hi{名} ! ❤️`、西语 `Hola{名}! ❤️`（实测渲染成 `Himaideediaz ! ❤️`、`Holamaideediaz! ❤️`），SOP 与 `assistant/domain/message_templates.py` 已同步修正，回归测试 `tests/assistant/test_message_templates.py::test_reminder_templates_greet_with_clean_spacing`。**改话术必须 SOP 与模板同时改**。D0 与感谢话术里还有同类历史写法（`Hola{名} !`、`Hi {名}❤️`、`Hola{名}!`），本次未改。

**D+10 名单口径（2026-09-10 收窄）：** 「报表 → D+10 待出名单」导出（`ExportService._rows("day_10_list")`）只收 **当前节点仍是 D+10** 的行：`task.status=pending`、case 仍处理中且非 stale、未记账，且按 `latest_due_unpublished_stage` 判定 10 ≤ 到货天数 < 15（满 15 天当前节点已是 unfulfilled，不再重复催）。已被取代（suppressed）或没有送达日的行不进名单——这类行在页面上也点不了「已出名单」。实测 10 → 8 行（去掉两条已转 unfulfilled 的旧行）。

`detail_targets`：仅 `--with-detail` 只拉列表初判通过行；加 `--detail-all` 才拉全表。试跑限量用 `--max-rows`。

---

## 页面 / 写操作

- 列表：静默读 fiber `record`，勿点名称旁易弹剪贴板的控件。批准只走列表「同意」，不在详情页点同意。
- 详情：直链 `cid=` 或点头像；抽完回列表。
- 私信：只从样品申请页右下角「聊天数」进入，点击「发送消息」，在「发送给」输入达人 ID 后点击结果行右侧「聊天」。成功=选中 `contactCard` 对得上人且有输入框。**不要打开详情消息弹层、不要跳到 `/seller/im`、不要点最近联系人**。发送默认 `onSendText`，不点发送钮（除非 `--write-source dom`）。
- **搜索键先 handle 后数字（2026-09-18 改）**：`open_conversation_via_new_message` 的候选顺序是 `creator_name`（业务上的达人 ID，平台 handle，如 `prettybalanced_`）优先，`creator_id`（19 位内部数字 ID）只在前者搜不到时兜底——数字串会先出现在「发送给」输入框，用户已把它当 bug 报过。填完必须回读输入框，内容与搜索键不一致（残留上一位达人 / 拼串）立即拒开该会话，不再换第二个关键词。
- 可点：待审核/已发货 tab、翻页、头像/详情、返回，以及样品申请页聊天数面板中的「发送消息」、达人 ID 搜索结果「聊天」。execute 还可点列表同意、确认弹窗。永不点邀请。
- 进出商家订单页：异步 `location.replace` + 短轮询，禁止阻塞 `visit_page` 回样品申请。从订单等 SPA 子页出发时先 replace 掉历史，避免弹回订单页。
- storeId：显式 > running 精确店名 > running 唯一 > 测试默认 1 号店。ZClaw Bridge `9481` ≠ WebDriver `16851`。
- 跟进/物流私信语言：飞书「使用语言」优先；否则详情简介 `detect_creator_lang`（LLM JSON）；空简介默认英语。已有会话语言不改判。密钥只放 `.env`，勿提交、勿进聊天。

脚本入口：`scripts/open_sample_store.py`、`screen_sample_requests.py`、`send_sample_intro.py`、`sync_shipped_tracking.py`。日常 0 号双击走 `--reopen`。`hero_xlsx.py` 已废。密钥在 gitignore 的 `config.toml`。

依赖入口：`setup/install_deps.py`（Windows 双击 `setup/安装依赖.bat`；macOS `zsh setup/install_deps.sh`；`--no-build` / `--with-test`）。它只安装依赖与构建，不写平台、不写飞书、不填密钥；本地离线回归仍是 `uv run python -m pytest tests -q`。

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
