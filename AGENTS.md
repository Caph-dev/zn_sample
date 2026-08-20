# zn_sample · AGENTS.md

本文件给 **Agent / 技术维护**：纪律、数据 ID、实现契约。  
日常双击 / 出问题只写 [README.md](./README.md)。新电脑配机只写 [快速开始.md](./快速开始.md)。规则原文见 `样品申请筛查sop/样品申请筛查sop.md`。

紫鸟启停 / GUI vs WEBDRIVER / `ziniao-cli`：**先读** [`../zn_daren/AGENTS.md`](../zn_daren/AGENTS.md)。本仓默认 **GUI + 已 open 的店**。勿混用 `zn_daren` 的 `--execute` 取消逻辑。禁止 `ziniao-cli page extract --mode running`（可能 `runtime.reopen`）。

不要改系统或启动器 `PATH`。Windows 上 `ziniao-cli.cmd` 只解析成 `node` + `run.js` 的**绝对 POSIX 路径**（`C:/...`），子进程固定 UTF-8。日志用 `logging`，入口 `configure_logging`，库代码 `getLogger`。

---

## 非协商

1. **默认禁止写操作**  
   不点：同意 / 批准 / 拒绝 / 发货 / 私信「发送」/「邀请」。  
   默认只读列表、详情、本地判定、导出。  
   **仅** `--execute --yes` 可对筛查通过行点「同意」（永不拒绝）或发第 6 / 9 步私信。  
   写飞书另加 `--write-feishu`。测试默认不写飞书。  
   `--execute-limit` 默认 1，不得新参数绕过。批准前写 `*_pre_execute.*`。平台同意与已发私信不可脚本撤销。

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
   写入 TikTok 物流单号后：已寄样=是，待发货→待发布；不回退「已发布」等。无匹配行（红人ID+寄样产品）不新建。

5. 只改任务相关代码。密钥 / `apiKey` / 密码不写入本文件、不提交 git、不进聊天。默认中文沟通。

---

## 能力边界

| 能力 | 实现 | 不可误解为 |
|---|---|---|
| 进待审核 | 开店 + `--from-seller-home` | 不代办登录，不擅自切店 |
| 初筛+复筛 | `--with-detail --require-detail` | 不能把仅列表结果当正式名单 |
| 批准 | `--execute --yes`；默认已捕获的窄 API | 不能猜 endpoint、扩大接口、绕过门闩；`shadow` 禁止配合 `--execute`；API 路径尚未用第二条真实申请重复验收 |
| 写达人关系 | 写接口接受后 + `--write-feishu`；`--confirm-export` 可**补写** | 不是主推表；写结果未知时不先写 |
| 第 6 / 9 步私信 | 独立脚本；详情页消息按钮 `handleClick` 开弹层，默认 IM SDK | 批准后不自动发；不要求跳到 `/seller/im`；不要先点「聊天数」 |
| 第 7–8 步物流 | 独立脚本；**北京时间 16:00 前拒绝** | 先飞书近 7×24 小时且合作状态=待发货（主键红人ID+寄样产品），再对已发货。`main_order_id` 不是发给达人的单号；`--force` 只过时间门。物流 GET 对齐订单页 query（`oec_seller_id`/`seller_id`/`aid`）；订单 URL 用 `seller.us`，不用 apex。紫鸟 `error.html` 当跳转失败 |

读路径：`auto` 日常推荐（API 失败回退 DOM）；`api` 失败即报错。页面 API 用异步 `fetch` + `request_id` 轮询（兼容 2 号店同步 XHR 空响应）。批准默认 `--write-source api`，DOM 须显式指定。简介仍走详情 DOM。

---

## SOP → 脚本停止点

五个入口断开。可复制命令只写 README，这里只写停点和门闩。

| SOP | 脚本 | 默认停 | 加开关 | 不会做 |
|---|---|---|---|---|
| 开店（带调试口） | `open_sample_store.py --reopen` | 店铺窗口打开 | 无 | 不代登、不进联盟中心、不切其它店 |
| 开店（不重开） | `open_sample_store.py` | 首页前 | 无 | 已运行不关、不进联盟中心 |
| 1–4 筛查+名单 | `screen_sample_requests.py` | 导出 | 正式须 `--with-detail --require-detail` | 不批、不发信 |
| 5 同意/写表 | 同上 | 不写 | `--execute --yes`；再加 `--write-feishu` | 不发第 6 步 |
| 5 **补写** | 同上 `--confirm-export` | 核对待发货 / 补飞书 / 回填订单号 | 约 10 分钟窗口；不重批 | 不发私信 |
| 6 介绍 | `send_sample_intro.py` | 预演 | `--execute --yes` | 不批、不查物流 |
| 7 读物流 | `sync_shipped_tracking.py` | **16:00 前拒绝** | 16:00 后只读；测试 `--force` | `--force` 不写不发 |
| 8 回写飞书 | 同上 | 不写 | `--write-feishu` | 不发第 9 步 |
| 9 物流私信 | 同上 | 不发 | `--write-feishu --send-tracking --execute --yes` | 不回头筛/批 |

**补写：** 写接口 `success_count=1` 即算批准成功，不等待发货。列表约 10 分钟后刷新；用 `--confirm-export` 核对并补飞书。原先因「待审核找不到」标成 `skipped` 的已批行也纳入补写。

**订单号回填（同一 confirm 步骤）：** 确认转入待发货后，对飞书已建行的行回填「订单号」列，只写这一列（不联动「是否已寄样」/「合作状态」）。订单号取自待发货 tab 的 `main_order_id`，须非空、非 `0`、符合 15–20 位订单号形态（`is_order_id`）；已有不同订单号不覆盖（`update_record_order_no`）。拿不到有效订单号的行跳过，留待物流步骤兜底；「快递单号」仍只在已发货后由第 7–8 步写。回填与补写共享同一 `--execute-limit` 预算，主流程先行。导出新增 `order_no` / `feishu_order_status` / `feishu_order_error` 列。

**16:00：** `sync_shipped_tracking.py` 启动时用 `Asia/Shanghai` 看当前小时，`< 16` 退出。不是 cron，到点不会自动跑。

---

## CLI 契约

| 参数 | 约束 |
|---|---|
| `--data-source` | `dom\|api\|auto\|shadow`。批准推荐 `auto`。`shadow` 禁止 `--execute` |
| `--write-source` | 批准/私信写路径；默认 `api`；`dom` 须显式。都要 execute 门闩 |
| `--from-seller-home` | 才允许从已登录商家中心（任意子页）导航；未写 `--store-id` 时须 running 唯一店。失败不重开不切店 |
| `--from-export` | 跳过扫表/详情。筛查脚本：批准或补写。介绍脚本：优先 `approved`，也会带上「通过且有 creator_id」的未批行 → 指定人用 `--creator-id`/`--creator-name` |
| `--confirm-export` | 只补写/核对/回填订单号，不重批；回填与补写共享 `--execute-limit` |
| `--with-detail --require-detail` | 正式筛查必须成对；禁止 `--detail-limit`、`--skip-hero-check` |
| `--execute --yes` | 唯一批准/真发门闩；缺一退出码 2 |
| `--execute-limit` | 默认 1 |
| `--write-feishu` | 筛查脚本不能单独用来「只筛查但写表」。物流无匹配行不新建 |
| `--force` | 只绕过 16:00 |
| `--observe-approve-network` | 仅单条 execute 被动观察；不重放、不登记未确认 endpoint |

批准：先同意成功再写飞书；去重或货号无法映射 → 不批不写。红人ID=`creator_name`。  
第 9 步发 **TikTok 物流单号**（有承运商则 `{承运商}, {单号}`），不是订单 ID。已有不同单号默认不覆盖（须 `--overwrite`）。

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

`detail_targets`：仅 `--with-detail` 只拉列表初判通过行；加 `--detail-all` 才拉全表。试跑限量用 `--max-rows`。

---

## 页面 / 写操作

- 列表：静默读 fiber `record`，勿点名称旁易弹剪贴板的控件。批准只走列表「同意」，不在详情页点同意。
- 详情：直链 `cid=` 或点头像；抽完回列表。
- 私信：详情「邀请」旁 `.alliance-icon-Message` 的 `handleClick`（带 `creatorId`）。成功=弹层有输入框且选中 `contactCard` 对得上人。**不要先点「聊天数」**。不要因 URL 不是 `/seller/im` 判失败。发送默认 `onSendText`，不点发送钮（除非 `--write-source dom`）。
- 可点：待审核/已发货 tab、翻页、头像/详情、返回、详情消息按钮。execute 还可点列表同意、确认弹窗。永不点邀请。
- 进出商家订单页：异步 `location.replace` + 短轮询，禁止阻塞 `visit_page` 回样品申请。从订单等 SPA 子页出发时先 replace 掉历史，避免弹回订单页。
- storeId：显式 > running 精确店名 > running 唯一 > 测试默认 1 号店。ZClaw Bridge `9481` ≠ WebDriver `16851`。
- 语言只看详情简介；空简介默认英语，已有会话语言不改判。

脚本入口：`scripts/open_sample_store.py`、`screen_sample_requests.py`、`send_sample_intro.py`、`sync_shipped_tracking.py`。日常 0 号双击走 `--reopen`。`hero_xlsx.py` 已废。密钥在 gitignore 的 `config.toml`。

---

## Agent 纪律

1. 改自动化前确认：0 号已开店且 `execute_script` 通；页在待审核（或用户允许 `--from-seller-home`，起点可以是已登录商家中心任意子页）；`ziniao-cli doctor`；脚本 1/2 同一 `store_id`。0 号允许关目标店再开；1/2/3 仍不重开、不切店。
2. 用户要批准：只走已有 `--execute --yes`（+ 可选 `--write-feishu`）。提醒 limit、备份、不可撤销。勿另开无门闩路径。
3. 探 DOM 只用 `execute_script`；失败先查：留在详情页、Bridge、错 tab、未全部展开。
4. 与 zn_daren 共用时引用、不复制大段 `zclaw_dom`。
5. 同一错误第二次 → 补一条可验证规则。纪律变更同步 README。删冗长：删掉会不会更容易犯错？不会就删。
