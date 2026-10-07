# 仓库任务导航

这张地图回答“改这项任务，先读什么、代码在哪、看哪些测试”，不重复业务规则，也不构成执行授权。入口采用文件路径而非易漂移的源码行号；新增或迁移入口时同步更新本页。

## 文档归属与历史边界

- **业务规则原文**：[筛查 SOP](../样品申请筛查sop/1-样品申请筛查sop.md)、[到货与跟进 SOP](../样品申请筛查sop/2-查看到货+达人跟进.md)。
- **Agent 安全门闩与实现契约**：[AGENTS.md](../AGENTS.md)。改代码前必读；专题规范补充细节，不覆盖门闩。
- **日常操作、可复制命令与排障**：[README.md](../README.md)；**新电脑配机**：[快速开始.md](../快速开始.md)。
- **专题规范**：[自定义候选筛选流程](./spec/自动审批候选筛选流程.md)、[TikHub 视频分页窗口契约](./spec/TikHub视频分页窗口契约.md)。
- **历史背景，不作现行操作依据**：`docs/archive/` 与历史事故记录。搜索命中后先看文件首屏的日期、替代指针与更正；不从旧方案恢复已禁止的路径。
- **本地计划**：可提供指定任务的范围和背景，不覆盖现行契约，也不是查找实现的前置；目录与编号约定见本页末节。

若文档与实现不一致，先核对现行契约及附近测试；涉及安全边界或业务口径时停止确认，不用历史计划填补或绕过门闩。

## 按任务找实现与验证

### 开店、导航与运行准备

- 先读：[AGENTS.md](../AGENTS.md)「两阶段导航」「页面 / 写操作」；紫鸟启停还须按其指针读相邻仓库契约。
- 实现：[开店脚本](../scripts/open_sample_store.py)、[店铺启停适配](../scripts/lib/store_launcher.py)、[样品页导航](../scripts/lib/sample_navigation.py)、[准备状态服务](../assistant/services/store_service.py)。
- 验证：[开店测试](../tests/test_store_launcher.py)、[导航测试](../tests/test_sample_navigation.py)、[店铺解析测试](../tests/test_resolve_store.py)。

### 正式 SOP 筛查、批准与核对补写

- 先读：[筛查 SOP](../样品申请筛查sop/1-样品申请筛查sop.md)、[AGENTS.md](../AGENTS.md)「SOP → 脚本停止点」「CLI 契约」。此链与网页自定义审核链隔离。
- 实现：[正式入口](../scripts/screen_sample_requests.py)、[销售阈值与主推过滤](../scripts/lib/filters.py)、[数据源编排](../scripts/lib/sample_data_source.py)、[批准窄 API](../scripts/lib/sample_write_api.py)、[飞书关系表](../scripts/lib/feishu_bitable.py)。
- 验证：[执行安全](../tests/test_execute_safety.py)、[正式内容门](../tests/test_screen_content_review.py)、[批准 API](../tests/test_sample_write_api.py)、[飞书写入](../tests/test_feishu_bitable.py)。

### 自定义审核预览、执行与历史批次核对

- 先读：[候选筛选流程](./spec/自动审批候选筛选流程.md)、[AGENTS.md](../AGENTS.md)「自动审批子页面」。筛选、详情复用、内容审查和执行复算按 L0–L5 查找。
- 实现：[HTTP 接口](../assistant/api/auto_approval.py)、[快照与任务服务](../assistant/services/auto_approval_service.py)、[四种模式脚本](../scripts/auto_approval.py)、[规则判定](../scripts/lib/auto_approval_rules.py)、[历史核对服务](../assistant/services/auto_approval_reconciliation.py)。
- 验证：[预览](../tests/test_auto_approval_preview.py)、[预筛与复用](../tests/test_auto_approval_prefilter.py)、[规则](../tests/test_auto_approval_rules.py)、[服务端门闩](../tests/assistant/test_auto_approval_service.py)、[核对补写](../tests/test_auto_approval_reconciliation.py)。

### 详情 API、系统故障熔断与筛查耗时

- 先读：[AGENTS.md](../AGENTS.md)「详情接口系统级故障熔断」、[候选筛选流程](./spec/自动审批候选筛选流程.md) 的阶段耗时日志契约；事故原始诊断不是处置依据。
- 实现：[页面请求传输](../scripts/lib/page_api.py)、[详情 API](../scripts/lib/creator_api.py)、[详情与熔断](../scripts/lib/creator_detail.py)、[耗时上下文](../scripts/lib/screening_perf.py)、[跟进资料补齐](../assistant/services/creator_enrich_service.py)。
- 验证：[详情故障](../tests/test_creator_detail.py)、[正式筛查熔断](../tests/test_screen_detail_failfast.py)、[耗时日志](../tests/test_screening_perf.py)、[跟进补齐](../tests/assistant/test_creator_enrich.py)。

### 视频内容审核、分页、证据与取消

- 先读：[筛查 SOP](../样品申请筛查sop/1-样品申请筛查sop.md) 的第 5 步、[README.md](../README.md)「第 5 步内容审查」、[分页窗口契约](./spec/TikHub视频分页窗口契约.md)。
- 实现：[视频采集](../scripts/lib/tiktok_creator_videos.py)、[视觉审核与证据验证](../scripts/lib/creator_video_review.py)、[配置契约](../scripts/lib/creator_video_contract.py)；上层取消传播还看 [预览入口](../scripts/auto_approval.py)。
- 验证：[视频采集](../tests/test_tiktok_creator_videos.py)、[视觉 / 证据 / 清理](../tests/test_creator_video_review.py)、[保守分页边界](../tests/test_video_window_boundary.py)、[内容时效](../tests/test_content_review_freshness.py)、[预览取消](../tests/test_auto_approval_preview.py)。

### 达人跟进日历、话术与单条发送

- 先读：[跟进 SOP](../样品申请筛查sop/2-查看到货+达人跟进.md)、[AGENTS.md](../AGENTS.md) 的当前阶段、重复发送和跟进写飞书保护。改话术须同时改 SOP 与模板。
- 实现：[日历与窗口](../assistant/domain/followup_stage.py)、[话术模板](../assistant/domain/message_templates.py)、[待办服务](../assistant/services/followup_service.py)、[内容感谢](../assistant/services/content_thanks_service.py)、[发送脚本](../scripts/send_followup_message.py)、[聊天导航与消息时间解析](../scripts/lib/im_dom.py)。
- 验证：[日历](../tests/assistant/test_followup_stage.py)、[话术](../tests/assistant/test_message_templates.py)、[生成待办](../tests/assistant/test_followup_generate.py)、[单条发送](../tests/test_send_followup_message.py)、[聊天 DOM](../tests/test_im_dom.py)。

### 计划清理（定向合作进行中）

- 先读：[README.md](../README.md)「计划清理」与 [AGENTS.md](../AGENTS.md)「独立定向邀请取消链」。这是 2/4 自然月的固定快照取消业务，不是样品批准、飞书或私信；真实执行须另行确认名单，不与旧项目脚本并跑。
- 实现：[独立脚本](../scripts/cleanup_target_plans.py)、[规则/快照/检查点](../scripts/lib/target_plan_cleanup.py)、[DOM 与完整性](../scripts/lib/target_invitation_dom.py)、[已知导航](../scripts/lib/target_invitation_navigation.py)、[持久化与领取](../assistant/services/target_cleanup_service.py)、[白名单表单 API](../assistant/api/target_cleanup.py)、[固定 handler](../assistant/jobs/handlers/target_cleanup.py)、[迁移](../assistant/database/migrations/versions/0007_target_cleanup.py)、[React 页面](../frontend/src/console/pages/TargetCleanupPage.tsx)。页面复用共用九项导航、确认弹窗与任务监控，不自行探活或 POST 第二次。
- 验证：[规则与执行](../tests/test_target_plan_cleanup.py)、[DOM](../tests/test_target_invitation_dom.py)、[导航](../tests/test_target_invitation_navigation.py)、[领域服务与恢复](../tests/assistant/test_target_cleanup_service.py)、[API](../tests/assistant/test_target_cleanup_api.py)、[任务保护](../tests/assistant/test_target_cleanup_jobs.py)、[bootstrap](../tests/assistant/test_target_cleanup_pages.py)、[合成 UI/网络恢复](../frontend/tests/targetCleanup.test.mjs)。发布资源与隔离 smoke 还看 `tests/release/test_bundle_resources.py` / `test_bundle_smoke.py`；离线证明不等于原生或实店验收。

### 物流同步、写回、介绍私信与订单号补写

- 先读：[README.md](../README.md) 的对应操作与 [AGENTS.md](../AGENTS.md) 的停止点；操作台只读物流、受 16:00 门保护的物流写回、订单号补写不是同一任务。
- 实现：[只读物流服务](../assistant/services/shipment_service.py)、[物流写回 / 私信脚本](../scripts/sync_shipped_tracking.py)、[介绍私信](../scripts/send_sample_intro.py)、[订单物流 API](../scripts/lib/order_api.py)、[订单号补写](../scripts/lib/order_backfill.py)、[网页补写服务](../assistant/services/order_backfill.py)。
- 验证：[只读同步](../tests/assistant/test_shipment_sync.py)、[物流解析](../tests/test_tracking_parse.py)、[介绍信](../tests/test_send_sample_intro.py)、[订单号补写](../tests/test_order_backfill.py)、[网页补写](../tests/assistant/test_order_backfill_service.py)。

### 操作台前端、数据契约与报表

- 先读：[AGENTS.md](../AGENTS.md)「操作台页面（Astryx React 壳）」；正式筛查批准不重新暴露到网页。
- 实现：[控制台 React](../frontend/src/console/ConsoleApp.tsx)、[自动批准 React](../frontend/src/App.tsx)、[bootstrap 数据](../assistant/web/console_pages.py)、[控制台类型](../frontend/src/console/types.ts)、[共用任务面板与表单](../assistant/web/static/app.js)、[报表服务](../assistant/services/export_service.py)。
- 验证：[bootstrap 测试辅助](../tests/assistant/console_payload.py)、[总览](../tests/assistant/test_dashboard.py)、[报表](../tests/assistant/test_exports.py)；构建命令见 [package.json](../package.json) 和 README。

### 任务互斥、取消、SQLite 与生命周期

- 先读：[AGENTS.md](../AGENTS.md)「网页取消」「SQLite 写锁与任务心跳」；`can_request_cancellation()` 是网页取消的唯一判据。
- 实现：[注册表](../assistant/jobs/registry.py)、[worker](../assistant/jobs/worker.py)、[取消哨兵与锁](../assistant/jobs/locks.py)、[页面互斥](../assistant/services/page_lock.py)、[数据库连接](../assistant/database/engine.py)、[迁移](../assistant/database/migrations/)、[源码生命周期](../assistant/lifecycle.py)。
- 验证：[任务](../tests/assistant/test_jobs.py)、[脚本取消](../tests/test_job_cancel.py)、[SQLite 连接](../tests/assistant/test_database_engine.py)、[迁移](../tests/assistant/test_migrations.py)。

### 发布包、安全启停与同机配置导入

- 先读：[README.md](../README.md)「内测目录包」「同机发布配置导入」；源码默认重启与发布版安全启停分开核对。
- 实现：[构建与运行时](../setup/release/)、[包内入口](../scripts/release_launcher.py)、[发布生命周期](../assistant/services/release_lifecycle.py)、[双模式路径](../assistant/paths.py)、[配置 / 历史导入](../assistant/services/local_state_import.py)。
- Windows 云构建：[手动 Actions 工作流](../.github/workflows/windows-bundle.yml)、[Windows ZIP 与校验摘要](../setup/release/package_windows_bundle.py)；操作与下载见 README「GitHub Actions」。工作流不接入业务密钥，不自动发布正式版。
- 验证：[发布测试目录](../tests/release/)，重点为 `test_bundle_smoke.py`、`test_release_lifecycle.py`、`test_state_import.py`、`test_package_windows_bundle.py`。离线测试不等于原生环境验收或真实导入授权。

## 本地计划编号（可选背景，不是维护前置）

以下目录按现有约定被 Git 忽略，只有本机存在时才阅读；本页不依赖它们。不要修改忽略规则或强制添加计划来补导航。

- `plan/`：旧产品规格；`plan/plan_workdesk_v1.md` 是历史背景，不是现行实现任务。
- `plans/`：操作台与交付阶段；`plans/009-browser-bundle-and-safe-lifecycle.md` 是目录包，`plans/010-installers-and-native-acceptance.md` 是安装器 / 原生验收。
- `advisor-plans/`：审查整改；`advisor-plans/009-screening-performance-observability.md` 是耗时观测，`advisor-plans/010-systemic-detail-circuit-breaker.md` 是详情熔断。
- 提到计划时写目录限定名或完整文件名，**不写裸 009/010**；本地进度看各自 README 与执行记录，不从编号猜任务。`plans/` 中已有两份日期命名计划被例外跟踪，仍不是新的导航权威。

变更实现入口时维护本页链接；具体规则仍维护在上面的归属文档。历史归档保留原文，只增加历史状态和替代指针。
