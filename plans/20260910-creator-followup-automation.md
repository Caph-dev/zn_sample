# 达人跟进自动化开发计划（Phase 1–4）

日期：2026-09-10
分支：`creator-followup-page`
状态：Phase 1 已完成（发送脚本、图片 SDK 通道、结果语义、原子认领与互斥、操作台单条发送入口、离线单测；全量 711 tests OK）。真实发送前新增「会话已有感谢话术 → 保留并转人工」（`held` / `content_thanks_already_sent`）与 `content_found` 任务本地标记（`mark-sent` 接受 `acknowledge_content`），避免与人工已发的感谢私信重复。未执行任何真实发送或写飞书；限量实发验收须另行授权。本文不构成执行真实发送私信、写飞书或平台审批的授权；任何真实发送仍须 `--execute --yes`（+ `--write-feishu`）门闩并默认限量。

权威口径：`样品申请筛查sop/2-查看到货+达人跟进.md`（项目约定 + 2026-08-26 文档更新）。
飞书 wiki 正文里的旧日历（到货第五天 / 每隔两天）与 B006-A 一律忽略，不进入代码、话术与验收。

## 0. 已确认决策（2026-09-10）

| # | 问题 | 决策 |
|---|------|------|
| 1 | D0 / D+3 / D+7 与感谢私信 | **脚本自动发送**，不再只做「人工发、系统记录」 |
| 2 | B005 配图 | 平台 IM 应允许发图片，**优先 SDK**；SDK 不支持时**先停止并汇报**，不擅自改 DOM 上传 |
| 3 | D+10 名单交付 | 当前版本只做**本地留痕 + CSV** |
| 4 | D+15 未履约 | **允许批量写飞书** |
| 5 | 内容巡检数据源 | **平台样品页「内容详情」的视频/直播计数**（2026-09-10 改版；TikHub + 视觉审核只用于 SOP 第 5 步筛查） |
| 6 | 触发方式 | **人工启动脚本**（与 0/1/2/3 一致），不做定时 |
| 7 | 网页角色 | 操作台**新增「发送」按钮**（受限写路径，每次 1 条）；其余能力保持只读/预演 |
| 8 | 感谢私信前置 | **业务员确认内容后**再发送 |
| 9 | 巡检频率 | **每天一次全量**（处理中到货达人） |
| 10 | D+15 批量门闩 | `--execute --yes` + 显式上调 `--execute-limit` + 写前备份 |

决策 6–10 于 2026-09-10 补充确认，详见 §9；决策 5 的巡检数据源同日改版为平台内容计数（见 §4.1）。

## 1. 目标与边界

### 1.1 目标

把 SOP 2「达人跟进」从「本地预览 + 人工执行」推进到「脚本自动执行」，形成可审计闭环：

1. 到货当天 D0、第 3 天、第 7 天：自动发送跟进私信（语言 × 视频/直播类型；B005 刚到货附图）。
2. 达人出视频/直播：内容巡检出候选 → 业务员确认 → 自动发送感谢私信 + 可选写飞书「已完成」。
3. 第 10 天仍未发布：导出名单 CSV，本地留痕。
4. 第 15 天仍未履约：批量把飞书合作状态改为「未发布」。
5. 送达日确认、页面对账与可观测性补齐。

### 1.2 非目标

- 不改正式 SOP 1（筛查/批准）与既有 0/1/2/3 入口行为。
- 不实现 wiki 旧日历（D+5 / 每隔两天 / 循环发送）与 B006-A。
- 除「发送跟进私信」这一个受限按钮外，不新增任何网页写路径；发送仍由后端固定任务调用既有脚本，网页不可覆盖 store / limit / source 等参数（决策 7）。
- 不自动拒绝、不自动补发历史阶段（只做当前最新该做的一步）。
- 不做买返样品跟进；跟进池仍只扫免费样品【处理中】（`tab=40`）。

### 1.3 非协商纪律（沿用仓库规则）

- 真实发送/写飞书必须 `--execute --yes`（+ `--write-feishu`），`--execute-limit` 默认 1，不得新增参数绕过。
- 执行前写 `*_pre_execute.*` 备份；`shadow` 禁止配合 `--execute`。
- 发送结果未知（`send-unknown`）不重试、不写飞书、不自动补发。
- 平台发送不可撤销；网页不给运行中的写任务提供取消按钮。
- 语言判定优先级：飞书「使用语言」> 详情简介 `detect_creator_lang`（LLM `lang`，不看 confidence）> 空/失败默认英语。
- 类型判定：手工设置 > 视频/直播标记；视频+直播同时标记按视频达人话术，不拆两条。

## 2. 已核实的现状（2026-09-10）

### 2.1 已经具备

- **日历与待办生成**：`assistant/domain/followup_stage.py` 的 `latest_due_unpublished_stage` / `plan_followup_mutation`；`FollowupService.generate()`（`assistant/services/followup_service.py`）按【处理中】+ 到货自然日生成唯一最新阶段，旧阶段 `suppressed_by_later_stage`，已确认内容则生成 `content_found`。
- **话术与附件**：`assistant/domain/message_templates.py`（`TEMPLATE_VERSION=1`，阶段 × video/live × en/es × B005），`attachment_key` + 详情页展示 B005 讲解图。
- **语言/类型策略**：`assistant/domain/policies.py`（含 `message_send_idempotency_key`，目前尚未实装到发送）。
- **任务与页面**：`assistant/api/followups.py`（预演/本地标记/内容确认/未履约）、`assistant/web/routes.py`（`/followups`、`/followups/{id}`）、React 页面与 bootstrap 数据（`assistant/web/console_pages.py`、`frontend/src/console/pages/`）。
- **物流同步**：`daily_refresh`（物流同步 + 待办生成）与 `shipment_sync` 已能更新到货状态与 `delivered_at`。
- **内容能力**：第 5 步内容审核库 `scripts/lib/tiktok_creator_videos.py` + `scripts/lib/creator_video_review.py`（TikHub + 视觉证据 + 系统级故障熔断）；`ContentThanksService` 已完成候选匹配、内容判定、话术与线程检查（`assistant/services/content_thanks_service.py`）。
- **飞书写入**：`update_record_cooperation_status`（已完成 / 未发布）与显式开关。

### 2.2 缺口（本计划要解决的）

| # | 缺口 | 证据 |
|---|------|------|
| G1 | 没有阶段私信真实发送路径 | `FollowupService.preview_followup_message` 固定 `execute=False`；`send_sample_intro.py` 是批准后介绍、`sync_shipped_tracking.py` 是物流单号，都不覆盖 D0/D+3/D+7 |
| G2 | IM SDK 只发纯文本，B005 图片发不了 | `scripts/lib/im_api.py::send_direct_message` 只接受 `body` 文本 |
| G3 | 内容发现靠人工确认，没有每日巡检 | 无跟进侧巡检任务；`content_thanks_preview` 只预演且 execute/write 硬禁用 |
| G4 | 发送结果与「本地人工标记」混淆 | `send_result` 只有 `marked-sent` / `listed`；没有平台确认/未知的区分 |
| G5 | D+10 无交付留痕 | 导出 CSV 已有，但无批次记录 |
| G6 | D+15 只能逐条 | 页面逐条勾选写飞书 |
| G7 | 送达日确认无入口 | `confirm_delivery_time` 哨兵任务停在 `needs_review`，物流详情只读 |
| G8 | 列表不可分页/搜索/批量 | `/followups` 全量渲染 |

测试基线（2026-09-10）：`.venv/bin/python -m unittest discover -s tests -q` → `Ran 642 tests ... OK`。

## 3. Phase 1（P0）— 跟进私信真实发送

### 3.1 交付物

1. 新脚本 `scripts/send_followup_message.py`（唯一真发路径）。
2. `scripts/lib/im_api.py` 扩展：图片消息能力（spike 结论：SDK 支持，走 Context Provider 的 `sendImageMessageWithFiles`，见 3.3）。
3. `followup_tasks` 发送结果语义扩展 + 标签更新（`assistant/domain/followup_labels.py`）。
4. 操作台发送入口：新任务类型 + 详情页「发送」按钮 + 确认弹窗（已完成，见 3.6）。
5. 详情页发送状态展示（只读）。
6. 单测 + 1 号店限量实测清单。
7. AGENTS.md 同步：登记新任务类型与保护集，并在决策 7 授权范围内更新网页写路径契约。

### 3.2 脚本契约（对齐既有 CLI 纪律）

| 参数 | 约束 |
|------|------|
| `--store-id` / `--store-name` | 显式 > running 唯一 > 测试默认 1 号店；多店/生产必须显式 |
| `--stage` | `arrival\|day_3\|day_7\|content_found`；不传按到期任务批次 |
| `--creator-id` / `--creator-name` | 指定单条达人；与 `--stage` 组合用于定向实测 |
| `--execute --yes` | 唯一真发门闩，缺一退出码 2 |
| `--execute-limit` | 默认 1；正整数；批量须显式调大 |
| `--write-feishu` | 仅 `content_found` 语义需要（合作状态→已完成）；默认关 |
| 页面前提 | 与 `send_sample_intro.py` 相同：已登录 + 停在样品申请页，或显式 `--from-seller-home` |

发送流程（逐条，串行）：

1. **选任务**：`status=pending`、未完成、`scheduled_for <= 今天`、平台【处理中】、有 `template_key`、非 `needs_review`。
2. **幂等预检**：本地 `sent_at` / `send_result=platform-sent` 直接跳过；平台侧用线程文本指纹二次确认。
3. **预演**：`send_direct_message(..., execute=False)` 打开会话并核对 `composer_identity_matches`；失败即停，不发送。
4. **备份**：`*_pre_execute.*`（任务快照 + 渲染后话术 + 目标达人 + 幂等键）。
5. **发送**：`send_direct_message(..., execute=True, write_source="api")`，`already_sent_predicate` 传入话术指纹（复用 `thread_has_named_intro` 思路，新增通用指纹函数）。
6. **落库**：
   - 确认已发 → `sent_at`、`send_result=platform-sent`、`send_confirmation=<指纹>`；
   - 结果未知 → `send_result=send-unknown`、`status=needs_review`、`review_reason=send_unknown_needs_review`（**移出自动发送池**，不重试）；
   - 失败（未发送）→ `last_error`，保持 `pending`，下次人工决定；
   - 文本已确认但配图失败 → 仍记 `platform-sent`（避免重发已发话术）并转 `needs_review`（`image_send_failed`），由人工补图。
7. **可选飞书**：仅当确认已发且 `--write-feishu`。

### 3.3 B005 图片 spike（2026-09-10 已完成：SDK 支持）

探查环境：2 号店 `27506607043054`（当时唯一 running），页面 `/affiliate/sample/sample-request`；全程只读：未点击、未发送、未导航。

**已确认（bundle 与 React 树实测证据）**

- `webpackChunkecom_seller` 模块 `4613` 暴露 `sendImageMessageWithFiles(conversationId, files)`：逐文件先建本地回显（`sendLocalImageMessage`，消息类型 `file_image`），再 `uploadImage(files)` 上传，拿到 `image_details` 后逐条发送 `{clientId, url, width, height}`；失败把消息状态置为 `Failed`。
- 上传实现（模块 `98452`）：`new FormData()` + `append("images[]", file)`，multipart POST，走 SDK `UploadImage({role, biz})`。
- 消息类型常量（模块 `4534`）：`FILE_IMAGE = "file_image"`。
- SDK 通过 React Context Provider 暴露 `sdkInstance` / `sendTextMessage` / `sendImageMessageWithFiles` / `sendLocalImageMessage` / `sdkStatus` 等；页面 fiber 树只读查找实测成功（遍历约 781 个节点，`sdkStatus=2`）。
- bundle 扫描未发现客户端大小/格式硬限制（`maxSize` / `fileSize` / `image/*` 等未命中相关校验）；限制预计在服务端上传接口。B005 附件为 1.1 MB PNG。

**Phase 1 实现路径（按此落地）**

1. 复用 `open_conversation_via_new_message` 打开会话并取 `conversationId`（与文本路径相同）。
2. 页面内只读定位 SDK Context Provider，调用 `value.sendImageMessageWithFiles(conversationId, [file])`；`sdkStatus` 未就绪或找不到 Provider 时拒绝发送，不改走 DOM 上传。
3. 图片文件在页面内构造：B005 PNG base64 分片注入 → `new File([bytes], "b05.png", {type: "image/png"})`；1.1 MB 必须分片，单片大小与次数在实现时实测（避免单次 `execute_script` 过大）。
4. 发送顺序：先 `sendTextMessage`（SOP 话术）再图片；发送后核对线程与本地回显状态。
5. 失败处理：文本已确认则记 `platform-sent`；配图失败转 `needs_review`（`image_send_failed`）由人工补图，不重发话术；文本结果未知走 `send-unknown` 规则。

**仍在 Phase 1 实发时验证**

- 服务端上传大小/格式上限与错误返回；是否需要页面上下文提供 `role`/`biz`。
- 图片发送的失败可观测性（是否仅有 `Failed` 飞行状态）；图片消息无文本时如何做发送后确认。
- 大图分片注入的耗时与 zclaw 稳定性。

Provider 只读查找参考（实现时使用，不点击不发送）：

```js
const root = document.querySelector('#root') || document.body;
const key = Object.keys(root).find(k =>
  k.startsWith('__reactContainer') || k.startsWith('__reactFiber'));
let fiber = key ? root[key] : null;
if (fiber && fiber.current) fiber = fiber.current;
const stack = [fiber];
while (stack.length) {
  const node = stack.pop();
  if (!node) continue;
  const value = node.memoizedProps && node.memoizedProps.value;
  if (value && typeof value.sendImageMessageWithFiles === 'function') {
    return value; // value.sendImageMessageWithFiles(conversationId, [file])
  }
  if (node.child) stack.push(node.child);
  if (node.sibling) stack.push(node.sibling);
}
```

### 3.4 数据与语义

- 复用 `followup_tasks` 现有字段（`sent_at` / `send_result` / `send_confirmation` / `last_error`），不新建表。
- `send_result` 取值：
  - `platform-sent`：平台发送后经线程确认；
  - `send-unknown`：已发出但未确认，禁止自动重试；
  - `sending`：发送中的原子认领（临态，发送结束后被最终结果覆盖；超时转 `send-unknown` + `needs_review`）；
  - `marked-sent`（保留）：网页人工标记，不等同平台发送；
  - `listed`（保留）：D+10 已出名单。
- 标签同步更新 `FOLLOWUP_ACTION_COMPLETED_LABELS` / 详情页文案，明确区分「平台已发送 / 本地人工标记 / 未确认 / 发送中」。
- 幂等键使用既有 `message_send_idempotency_key`（`policies.py`），不再另造；脚本输出中保留供核对。

### 3.5 互斥与安全

- 启动先检查 `jobs` 表中 pending/running 的页面任务（`ZINIAO_JOB_TYPES` ∪ `followup_generate`）；存在则拒绝（退出码 3），不并发占用店铺页面。
- 每条发送前用条件 UPDATE 原子认领（`send_result=sending`）；认领失败说明其他运行已占用，直接跳过，绝不重复发送。
- 认领超过 30 分钟（`--claim-stale-minutes` 可调）视为进程中断：转 `needs_review` + `send_interrupted`，由人工核对，不自动重发。
- 与 16:00 无关（跟进私信不受物流时间门限制）。
- 每条发送前检查取消信号；批量中途取消只保留已确认结果。

### 3.6 操作台发送入口（新增受限写路径，2026-09-10 已完成）

决策 7 允许操作台出现一个受限写入口，实现与约束如下：

- **固定任务**：任务类型 `operator_followup_send`，handler `assistant/jobs/handlers/followup_send.py` 用启动操作台的 `sys.executable` 调用 `scripts/send_followup_message.py --execute --yes --task-id <id> --ignore-job-id <job>`；不接受 shell 字符串，不执行 `.command` / `.bat`。已登记进 `ZINIAO_JOB_TYPES`、`WRITE_JOB_TYPES`、`OPERATOR_JOB_TYPES` 与启动器 `PROTECTED_JOB_TYPES`。
- **参数白名单**：端点 `POST /api/jobs/followups/send` 只接受 表单 `task_id`（服务端校验其存在）；`store` 由脚本解析 running 唯一店，`limit` 固定 1，网页不提供输入框，不得覆盖 store / limit / source。同一时刻只允许一个待执行任务；若已存在针对其他任务的待执行任务返回 409。
- **确认门**：沿用现有确认弹窗 + 键入 `y`；缺确认不创建任务。
- **按钮位置**：详情页 `send_ready` 时显示「发送这条跟进私信」（`send_message` 阶段）或「发送感谢私信」（`content_found` 阶段，须先完成内容确认）；`send_ready` 镜像脚本选人条件（到期、pending、非待确认、有模板、平台处理中），仅用于展示，最终仍由脚本复验。
- **结果展示**：任务事件 + 详情页发送状态（平台已发送 / 本地人工标记 / 未确认 / 发送中）。
- **验收**：一次点击最多发送 1 条；重复点击命中任务去重；脚本 `--task-id` 不可发送时退出码 4 使任务显式失败。

### 3.7 验收（DoD）

- 1 号店选 1 条真实 D0 任务：预演 → 发送 → 平台确认 → 本地 `platform-sent`；重复运行不重发。
- 离线单测覆盖：门闩（缺 `--yes` exit 2）、限量、幂等跳过、`send-unknown` 进 `needs_review`、备份文件生成、模板/语言/类型回归；以及原子认领（双运行只发一次）、陈旧认领转人工、页面任务互斥（退出码 3）；操作台端点（缺确认/缺 task_id/未知任务/重复点击去重/跨任务 409）、handler 固定 argv 与失败码。
- 操作台按钮：缺确认不建任务、运行中无取消、点击一次最多一条。
- 网页只新增发送入口；其余仍只有预演与本地标记；详情页能看到平台发送状态。

## 4. Phase 2（P0/P1）— 内容巡检 + 感谢私信

### 4.1 每日内容巡检（只读，平台口径）

> 2026-09-10 改版：原设计用 TikHub + 视觉审核判断"是否已出内容"。实店核对后确认**不需要**——
> 联盟中心样品申请页的「内容详情」本身就给出每行的 `视频 N / 直播 N`（含发布时间、播放/点赞、在 TikTok 查看视频），
> 商品行状态还会变「已完成」。跟进环节只需要"发了视频/直播 是/否"这个布尔量，不需要看视频内容，
> 也不需要消耗 TikHub 配额。TikHub + 视觉审核保留给 **SOP 第 5 步（筛查批准）**，与本阶段无关。

- 新任务类型 `followup_content_scan`：对【处理中】且已到货的达人分页读样品申请列表/详情的**内容数**（`视频 N` / `直播 N`）。
- 判定：`视频 ≥ 1` 或 `直播 ≥ 1` → 候选（`content_type` 按 video / live，二者都有按既有策略取 video）；全为 0 → 未出内容，不动。
- 只读：不打开达人会话、不发私信、不写平台；`ContentEvidence(content_status="suspect")` 落证据（平台来源 + 计数 + 可选视频链接），**不改任务状态**。
- 视频链接：抽屉「在 TikTok 查看视频」背后的链接若能只读取得就一并落库（用于感谢话术的 `{content_url}`），取不到就留空，不阻塞流程。
- 反爬/会话失效导致读不到：该行标 `needs_review`，不推断"未出内容"，只记一条汇总。
- 候选在跟进列表/详情页以筛选呈现；人工确认仍走现有 confirm-content 流程。

### 4.2 确认与感谢私信

- 业务员在详情页确认内容（保留现有 confirm-content 流程；可预填巡检证据链接与内容类型）。
- 确认后详情页出现「发送感谢私信」按钮（复用 3.6 的受限写入口），按内容类型发送 `video_found_*` / `live_found_*` 感谢话术；语言判定沿用同一策略；「业务员确认后才发」由决策 8 固定。
- 发送前的会话检查（2026-09-10 加固）：命中 SOP 感谢原文 → 保留并转人工；同事风格感谢需与 `video/live` 同段（±120 字）才算命中，避免旧消息拼接误挡（lince 案例）。
- `content_thanks_preview` 从「硬禁用」升级为「默认关闭 + 门闩」：
  - job 参数显式开启执行；仍 `--execute --yes` 语义等价；
  - `--write-feishu` 独立开关写「已完成」；
  - 不确定匹配、线程可疑、类型未知仍 hold，不发送。

### 4.3 验收

- 1 号店 1 条：巡检命中 → 人工确认 → 发送感谢私信 → （可选）飞书已完成。
- 巡检计数（0 / 仅视频 / 仅直播 / 两者都有 / 读取失败）与 hold、already-sent 路径有单测。
- 关闭开关时行为与现状一致（只预演）。

## 5. Phase 3（P1）— D+10 / D+15 / 列表可用性

### 5.1 D+10 本地留痕

- 导出 CSV 到 `user_data_dir()/exports/`（现有导出链路），文件名含批次时间与行数。
- 新增批次元数据：导出任务的 `result_summary` 记录文件路径、行数、生成时间（不新建表）。
- 列表页支持批量「标记已出名单」；批次与本地任务 `send_result=listed` 对账。

### 5.2 D+15 批量写飞书

- 脚本参数：`--stage unfulfilled --execute --yes --write-feishu`，批量须显式上调 `--execute-limit`。
- 逐行校验：已有内容证据拒绝、无 `feishu_record_id` 跳过、非 unfulfilled 阶段跳过。
- 写前备份全量行；逐行结果落 `last_error` / 日志；失败不自动重试；结束输出成功/跳过/失败清单。
- 完成后本地状态与飞书状态对账提示。

### 5.3 列表可用性

- 分页（服务端 `LIMIT/OFFSET`）、达人搜索（creator_name/creator_id）、批量选择（跳过、标记已出名单）。
- 保持 superseded 默认隐藏，状态筛选可显式包含。

## 6. Phase 4（P2）— 送达日确认与可观测性

### 6.1 送达日人工确认

- 详情页或物流详情页新增「确认实际送达日」表单 → API 写 `Shipment.delivered_at`。
- 必须人工选择日期，**禁止把 ETA / `predict_delivery_time_text` 当送达**。
- 写入后解决 `confirm_delivery_time` 哨兵任务（suppressed）并重排日历；页面提示「下次生成待办时按新日期计算」。
- 校验：日期不得晚于今天（北京时间）、不得早于发货事件。

### 6.2 可观测性

- 跟进时间线：任务生成 / 预演 / 发送 / 飞书写入 / 内容证据的只读视图（数据来自任务字段 + ContentEvidence）。
- 运行准备页：IM 链路只读探活（Bridge + 会话路径），不得并发探测，busy 时读缓存。

## 7. 验收与测试总表

| Phase | 关键验收 | 新增测试重点 |
|-------|----------|--------------|
| 1 | 1 号店 D0 实发 1 条（含 B005 配图）并确认；重跑不重发 | 门闩/限量/幂等/备份/send-unknown/标签/图片分片与失败可观测性 |
| 2 | 巡检 → 确认 → 感谢私信 1 条 | 内容计数分支（0/仅视频/仅直播/两者/读取失败）/hold/类型与语言/候选筛选 |
| 3 | 批量未履约写飞书（限量实测）；D+10 留痕文件 | 批量校验/拒绝路径/对账 |
| 4 | 人工送达日 → 哨兵解决 → 日历重排 | 日期校验/ETA 拒绝/状态迁移 |

统一要求：

- 每个 Phase 合并前跑 `.venv/bin/python -m unittest discover -s tests -q` 全绿（本阶段完成时 703）。
- 实店动作一律 `--execute-limit=1` 起步，验收记录写入导出或任务事件。
- 不改动 `plans/` 之外的既有计划；AGENTS.md 必须同步本次契约变化：新增任务类型 `followup_content_scan`、`operator_followup_send` 登记进 `ZINIAO_JOB_TYPES` 与 `PROTECTED_JOB_TYPES`，并把「网页不是新的业务写路径」更新为「唯一受限写入口：跟进私信发送」（决策 7）。

## 8. 风险与依赖

| 风险 | 影响 | 处置 |
|------|------|------|
| 图片上传服务端上限未验证 | B005 配图可能被拒或超时 | spike 已确认 SDK 支持（见 3.3）；Phase 1 限量实发验证 1.1 MB PNG 与失败可观测性 |
| TikHub 配额/费用 | 内容巡检不可持续 | 按第 5 步同一配置与预算；必要时限量（见 §9） |
| 反爬熔断误伤 | 巡检/详情接口系统级失败 | 沿用 `SYSTEMIC_DETAIL_FAILURE_LIMIT` 熔断与汇总日志 |
| 会话路径互斥 | 与批准/物流脚本冲突 | 单店写任务串行 + 启动前 running 校验 |
| `send-unknown` 误判 | 重复发送或漏发 | 线程指纹 + `needs_review` 人工处置；绝不自动重试 |
| 时区/自然日 | 阶段错位 | 全部 `Asia/Shanghai`；D0 只用 Delivered 事件北京自然日 |

## 9. 确认结果（2026-09-10）

| 原问题 | 结论 |
|--------|------|
| 触发方式 | 人工启动脚本，与 0/1/2/3 一致；不加定时设施 |
| 网页角色 | 操作台新增受限「发送」按钮（每次 1 条）；其余保持只读/预演 |
| 感谢私信前置 | 业务员确认内容后再发送（不接受巡检命中即自动发送） |
| 巡检频率 | 每天一次全量（处理中到货达人） |
| D+15 批量门闩 | `--execute --yes` + 显式上调 `--execute-limit` + 写前备份 |

剩余待实现时核对：TikHub 每日配额的具体数字（按第 5 步现有配置与预算执行；若超预算再与业务确认限量），以及操作台新按钮的文案与位置在实现 PR 中评审。

## 10. 实施顺序

`Phase 1.3 spike（图片 SDK）→ Phase 1 发送主干 + 操作台发送入口 → Phase 2 巡检+感谢 → Phase 3 批量与留痕 → Phase 4 送达日与可观测性`

- Phase 2 依赖 Phase 1 的发送管线与结果语义。
- Phase 3 的批量写飞书可独立于 Phase 2，先行合入亦可。
- Phase 4 与其余无强依赖，可并行。
