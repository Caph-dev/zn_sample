# 桌面运营助手实施计划（V1）

## 1. 目标与技术决策

### 1.1 产品目标

将当前由多个 `.command`、`.bat` 和 CLI 参数驱动的流程，改造成业务员可直接使用的本地运营助手：

1. 业务员双击一个应用入口；
2. 应用启动本地 FastAPI 服务；
3. 自动打开系统默认浏览器；
4. 业务员在网页中查看店铺状态、今日待办、物流和达人跟进任务；
5. 默认只读；
6. 私信、批准、飞书写入等操作必须逐条确认；
7. 所有任务状态、执行记录和跟进进度保存在本地 SQLite；
8. 保留现有 CLI 入口作为应急和技术维护工具。

### 1.2 确定采用的技术栈

```text
后端：Python 3.11+ / FastAPI / Uvicorn / Pydantic
页面：Jinja2 / HTMX / 少量原生 JavaScript / 普通 CSS
数据库：SQLite / SQLAlchemy 2.x / Alembic
进度：Server-Sent Events（SSE）
任务：SQLite 持久任务表 + 单机后台 Worker
打包：PyInstaller one-folder
窗口：系统默认浏览器
调度：首版应用内手动运行；后续接 Windows Task Scheduler / macOS launchd
```

首版不采用：

- React/Vue 和 Node 前端构建链；
- Electron；
- pywebview；
- Redis、Celery；
- 紫鸟自研插件；
- 云端后端；
- 无人值守发送私信；
- 自动图片发送。

## 2. 基本架构

```text
┌─────────────────────────────────────────┐
│ 系统默认浏览器                           │
│                                         │
│ 今日待办 / 物流 / 达人 / 消息预览 / 日志 │
└───────────────────┬─────────────────────┘
                    │ localhost HTTP + SSE
                    ▼
┌─────────────────────────────────────────┐
│ FastAPI 本地服务                         │
│                                         │
│ Web 页面     REST API     安全确认门闩    │
└──────────┬──────────────┬───────────────┘
           │              │
           ▼              ▼
┌────────────────┐  ┌─────────────────────┐
│ SQLite         │  │ 单机任务执行器       │
│                │  │                     │
│ 店铺/申请      │  │ 同一店铺串行执行     │
│ 物流快照       │  │ 持久任务状态         │
│ 跟进任务       │  │ 进度事件             │
│ 内容证据       │  │ 中断恢复             │
│ 写操作审计     │  └──────────┬──────────┘
└────────────────┘             │
                               ▼
                    ┌──────────────────────┐
                    │ 业务服务层           │
                    │                      │
                    │ 样品筛查             │
                    │ 紫鸟店铺与页面        │
                    │ 物流同步             │
                    │ 达人详情与语言        │
                    │ 话术生成             │
                    │ TikTok 私信          │
                    │ 飞书读写             │
                    └──────────┬───────────┘
                               │
                ┌──────────────┼───────────────┐
                ▼              ▼               ▼
          紫鸟 Bridge     TikTok Shop       飞书 API
```

### 2.1 核心原则

#### UI 不直接调用现有 CLI 子进程作为最终架构

最终应形成：

```text
现有 CLI ─────┐
              ├── 调用同一套业务 Service
FastAPI API ──┘
```

而不是长期维持：

```text
FastAPI → subprocess → Python 脚本 → 解析终端输出
```

初期可以短暂使用子进程适配器减少改造范围，但要明确标记为过渡层。

#### 所有平台操作仍从紫鸟页面环境执行

FastAPI 不改变当前边界：

- 不直接读取 TikTok Cookie；
- 不把 Cookie 保存到数据库；
- 不使用普通 HTTP 客户端直接调用 TikTok；
- 继续通过紫鸟 GUI、ZClaw Bridge 和页面上下文执行；
- 不引入 WebDriver；
- 不使用 `page extract --mode running`；
- 不擅自重开、关闭或切换店铺。

## 3. 第一版功能范围

### 3.1 必须交付

#### 应用和环境

- 跨平台本地启动器；
- 单实例运行；
- 自动选择可用本地端口；
- 自动打开系统默认浏览器；
- 只监听 `127.0.0.1`；
- 显示当前应用版本；
- 检测 Python、紫鸟 GUI、`ziniao-cli`、Bridge 和配置状态；
- 显示唯一 running 店铺；
- 不自动切店。

#### 运营首页

首页显示：

- 当前店铺；
- 紫鸟连接状态；
- 上次物流同步时间；
- 上次待办生成时间；
- 等待送达数量；
- 今日到货数量；
- D+3 待跟进数量；
- D+5 待跟进数量；
- D+7 待跟进数量；
- 循环跟进数量；
- 超过 10 天未发布数量；
- 需要人工确认数量；
- 最近失败任务。

#### 物流同步

- 读取目标样品或飞书候选记录；
- 读取 TikTok Shop 已发货记录；
- 读取订单物流详情；
- 保存物流单号和承运商；
- 保存物流状态；
- 保存预计送达时间；
- 保存实际送达时间；
- 保存最近物流轨迹；
- 保存本次读取来源与错误；
- 保留历史物流快照；
- 识别状态变化；
- 默认不发送私信、不写飞书。

#### 跟进待办

根据送达时间生成：

- D0：到货当天；
- D+3；
- D+5；
- D+7；
- D+9 及以后每两天；
- 超过 D+10 未发布名单。

每条待办显示：

- 店铺、达人和产品；
- 订单号和物流号；
- 送达时间；
- 跟进阶段；
- 达人语言和达人类型；
- 推荐话术和推荐图片；
- 当前飞书状态；
- 是否存在内容证据；
- 是否有冲突或缺失数据。

#### 话术预览

支持 SOP 2 中的模板矩阵：

- 英语/西班牙语；
- 视频达人/直播达人；
- B005/B006-A/其他产品；
- 到货当天；
- D+3/D+5；
- D+7/D+9/D+10；
- 发现视频；
- 发现直播。

首版支持：

- 生成、预览和复制；
- 标记人工处理；
- 记录使用的模板版本。

首版可以暂不支持：

- 自动图片发送；
- 自动插入 TikTok 内容证据；
- 无人值守发送。

#### 导出

支持导出：

- 今日待办 CSV；
- 超过 10 天未发布名单 CSV；
- 物流异常名单 CSV；
- 需要人工确认名单 CSV；
- 单次任务结果 JSON；
- 写操作审计 CSV。

#### 运行记录

每次操作记录：

- 操作人；
- 店铺；
- 任务类型；
- 创建时间；
- 开始和结束时间；
- 当前阶段；
- 成功、失败、跳过和未知结果数量；
- 错误摘要；
- 日志路径。

### 3.2 第一版暂不交付

- 紫鸟薄插件；
- TikTok.com 全自动视频扫描；
- 达人精灵自动读取；
- 自动判断视频所带产品；
- 自动上传或发送图片；
- 无人值守发送私信；
- 无人值守修改飞书合作状态；
- 自动处理多语言；
- 云端多用户协作；
- 多台电脑共享状态；
- 浏览器内嵌窗口；
- 自动升级程序。

## 4. 需求澄清的默认约定

### 4.1 时间规则

- 业务计算时区：`Asia/Shanghai`；
- D0 为承运商实际 `delivered_at` 所在的北京时间自然日；
- 如果平台没有实际送达时间，只看到“已送达”，标记为“送达时间待确认”；
- 不使用脚本首次观察时间直接冒充实际送达时间；
- D+3、D+5、D+7 按自然日计算；
- D+9 起每两天生成一次任务；
- “超过 10 天”从 D+11 开始；
- 如果错过多个阶段，只显示当前最新阶段，不补发多条旧消息；
- 周末和节假日暂不特殊处理。

### 4.2 内容发布规则

第一版没有可靠内容自动扫描，因此：

- 业务员可以手动添加视频/直播链接；
- 内容证据必须关联达人和产品；
- 产品匹配不明确时不能自动完成；
- 添加视频链接不等于立即写飞书；
- 必须经过人工确认后，才能进入“可更新飞书状态”队列。

### 4.3 达人类型

优先级：

1. 人工确认的跟进类型；
2. 筛查导出中唯一通过的一侧；
3. 只有一个标记时使用该标记；
4. 视频和直播均标记时进入人工确认；
5. 都没有时进入人工确认。

不允许把“视频+直播”默认拆成两条消息发送。

### 4.4 语言

- 仅支持英语和西班牙语；
- 高置信度时推荐模板；
- 空简介或低置信度时默认推荐英语，但标记“需要确认”；
- 不支持的语言只生成待办，不自动发送；
- 业务员可以覆盖语言，覆盖结果写入本地记录。

### 4.5 B005/B006-A

- 只使用精确业务货号匹配；
- 不使用标题、描述或子串匹配；
- B005 和 B006-A 分别关联对应图片；
- 无法精确映射时不推荐图片；
- 图片首版仅显示和允许业务员手动下载/查看，不自动发送。

## 5. 项目目录规划

```text
zn_sample/
├── assistant/
│   ├── __init__.py
│   ├── app.py
│   ├── bootstrap.py
│   ├── settings.py
│   ├── lifecycle.py
│   ├── api/
│   │   ├── health.py
│   │   ├── dashboard.py
│   │   ├── stores.py
│   │   ├── jobs.py
│   │   ├── shipments.py
│   │   ├── followups.py
│   │   ├── evidence.py
│   │   ├── writes.py
│   │   └── exports.py
│   ├── web/
│   │   ├── routes.py
│   │   ├── templates/
│   │   └── static/
│   ├── database/
│   │   ├── engine.py
│   │   ├── models.py
│   │   ├── repositories/
│   │   └── migrations/
│   ├── jobs/
│   │   ├── worker.py
│   │   ├── registry.py
│   │   ├── progress.py
│   │   ├── locks.py
│   │   └── handlers/
│   ├── services/
│   │   ├── store_service.py
│   │   ├── shipment_service.py
│   │   ├── followup_service.py
│   │   ├── message_service.py
│   │   ├── evidence_service.py
│   │   ├── feishu_service.py
│   │   └── export_service.py
│   ├── domain/
│   │   ├── models.py
│   │   ├── shipment_status.py
│   │   ├── followup_stage.py
│   │   ├── message_templates.py
│   │   └── policies.py
│   └── security/
│       ├── local_session.py
│       ├── csrf.py
│       ├── write_confirmation.py
│       └── secret_redaction.py
├── scripts/
│   ├── launch_assistant.py
│   └── lib/                 # 当前底层适配器继续保留
├── tests/
│   ├── assistant/
│   └── ...                  # 当前测试继续保留
├── packaging/
│   ├── pyinstaller/
│   ├── windows/
│   └── macos/
├── migrations/
├── README.md
├── AGENTS.md
└── pyproject.toml
```

现有 `scripts/lib/` 继续作为平台适配层。新建的 `assistant/services/` 不复制底层代码，而是组合并调用它们。

## 6. 数据库设计

### 6.1 `stores`

保存店铺身份，不保存 TikTok Cookie。

```text
id
ziniao_store_id
store_name
shop_id
shop_region
enabled
last_seen_at
created_at
updated_at
```

约束：`ziniao_store_id` 唯一，不因店铺显示名变化创建重复店铺。

### 6.2 `sample_cases`

代表“某达人 + 某寄样产品”的持续合作案例。

```text
id
store_id
creator_id
creator_name
creator_nickname
apply_id
product_id
sku_id
resolved_sku
sample_product_option
main_order_id
feishu_record_id
creator_type
language
current_state
first_seen_at
last_seen_at
created_at
updated_at
```

推荐业务唯一键：

```text
store_id + creator_id + product_id + apply_id
```

不能仅使用达人名，因为达人 handle 可能变化。

### 6.3 `shipments`

保存当前物流聚合状态。

```text
id
sample_case_id
tracking_number
tracking_display
carrier
status_code
status_label
status_category
estimated_delivery_at
delivered_at
last_event_at
last_event_text
package_count
source
last_checked_at
last_error
created_at
updated_at
```

标准化 `status_category`：

```text
unknown
label_created
pending_pickup
in_transit
out_for_delivery
delivered
exception
returned
lost
```

平台原始状态仍保存在 `status_code/status_label` 中，避免标准化导致信息丢失。

### 6.4 `shipment_snapshots`

保存每次只读同步结果：

```text
id
shipment_id
checked_at
status_code
status_label
status_category
estimated_delivery_at
delivered_at
last_event_at
last_event_text
raw_payload_hash
source
error
```

不默认保存可能含隐私的完整平台响应，只保存经过筛选的数据和哈希。

### 6.5 `content_evidence`

```text
id
sample_case_id
content_type
content_url
content_id
published_at
matched_product_id
matched_sku
match_status
match_source
manual_confirmed
confirmed_by
confirmed_at
notes
created_at
updated_at
```

`content_type` 为 `video` 或 `live`；`match_status` 为 `unknown`、`matched`、`unmatched` 或 `ambiguous`。第一版主要由人工添加和确认。

### 6.6 `followup_tasks`

```text
id
sample_case_id
stage
scheduled_for
status
language
creator_type
template_key
template_version
message_preview
attachment_key
requires_manual_confirmation
suppressed_reason
sent_at
send_result
send_confirmation
last_error
created_at
updated_at
```

`stage`：

```text
arrival
day_3
day_5
day_7
recurring
video_found
live_found
overdue_10_days
```

`status`：

```text
pending
ready
needs_review
approved
running
sent
unknown
failed
skipped
suppressed
cancelled
```

### 6.7 `jobs`

```text
id
job_type
store_id
status
progress_current
progress_total
progress_message
requested_by
created_at
started_at
heartbeat_at
finished_at
error_code
error_summary
result_summary
log_path
```

应用启动时，将 `running` 且心跳过期的任务改为 `interrupted`。不自动重新执行写操作；只读任务可由用户确认后重新运行。

### 6.8 `job_events`

保存细粒度进度：

```text
id
job_id
sequence
level
event_type
message
payload_summary
created_at
```

SSE 从该表增量读取，页面刷新后也能恢复进度。

### 6.9 `write_audits`

```text
id
job_id
sample_case_id
target_system
operation_type
idempotency_key
before_state
requested_change
after_state
requested_by
confirmed_at
executed_at
result
confirmation
error
created_at
```

写操作必须先写审计记录，再调用平台。

### 6.10 `app_settings`

只保存非密钥配置，例如时区、每日只读任务时间、默认导出目录、数据库 schema 版本和页面显示偏好。飞书 `app_secret` 不放入 SQLite。

## 7. 跟进状态机

### 7.1 物流状态机

```text
unknown
   │
   ▼
label_created / pending_pickup
   │
   ▼
in_transit
   │
   ▼
out_for_delivery
   │
   ▼
delivered
```

旁路状态：`exception`、`returned`、`lost`。

进入 `delivered` 后：

1. 有可靠 `delivered_at`：生成 D0 和后续日程；
2. 无可靠 `delivered_at`：创建“确认送达时间”人工任务；
3. 不允许用预计送达时间生成 D0。

### 7.2 跟进状态机

```text
已送达
  │
  ├─ D0 到货任务
  ├─ D+3 未发现内容
  ├─ D+5 未发现内容
  ├─ D+7 未发现内容
  ├─ D+9 / D+11 / ... 循环任务
  └─ D+11 超过 10 天名单
```

发现并确认相关视频或直播后：

- 抑制所有未发送的“未发布”跟进；
- 生成“视频已发布”或“直播已发生”任务；
- 不自动写飞书；
- 进入飞书更新人工确认队列。

### 7.3 幂等键

跟进任务：

```text
sample_case_id + stage + scheduled_for + template_version
```

私信发送：

```text
store_id + creator_id + product_id + stage + scheduled_for
```

飞书状态更新：

```text
feishu_record_id + target_status + content_evidence_id
```

同一个幂等键：

- 成功后不能再次执行；
- `unknown` 不能自动重试；
- `failed` 是否可重试由错误类型决定；
- 业务员重试前必须看到上一次结果。

## 8. 后台任务执行器

### 8.1 不使用 FastAPI `BackgroundTasks`

长任务需要持久状态、中断恢复、进度、店铺锁和错误分类，因此不使用 FastAPI `BackgroundTasks` 作为核心执行机制。

### 8.2 第一版 Worker

- FastAPI 进程内单独工作线程；
- SQLite `jobs` 表作为队列；
- 同一时刻默认只执行一个紫鸟任务；
- 每个店铺增加逻辑锁；
- 定期写 `heartbeat_at`；
- 每一步写 `job_events`；
- 应用关闭时停止领取新任务；
- 当前写操作不强行中断。

### 8.3 任务类型

```text
environment_check
store_refresh
shipment_sync
followup_generate
followup_export
message_preview
message_send
feishu_update
report_export
```

### 8.4 取消规则

允许取消：

- 尚未开始的任务；
- 正在分页读取的只读任务；
- 尚未进入平台写入阶段的任务。

不允许强行取消：

- 已向 TikTok 发出批准请求；
- 已向 IM SDK 发出发送请求；
- 已向飞书发出更新请求。

此时必须等待确认或标记为 `unknown`。

## 9. FastAPI 页面和接口规划

### 9.1 页面

```text
/                       首页
/stores                 店铺与环境
/jobs                   任务列表
/jobs/{job_id}          任务详情与实时进度
/shipments              物流列表
/shipments/{id}         物流详情与历史
/followups              跟进待办
/followups/{id}         待办详情与话术预览
/evidence               内容证据
/reports                导出与历史报告
/settings               非敏感设置
/diagnostics            环境诊断
```

### 9.2 只读 API

```text
GET  /api/health
GET  /api/dashboard
GET  /api/stores
GET  /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/events
GET  /api/shipments
GET  /api/followups
GET  /api/evidence
GET  /api/reports
```

### 9.3 创建只读任务

```text
POST /api/jobs/environment-check
POST /api/jobs/shipment-sync
POST /api/jobs/followup-generate
POST /api/jobs/report-export
```

这些接口创建 job，立即返回 `job_id`。

### 9.4 SSE

```text
GET /api/jobs/{job_id}/stream
```

事件类型：

```text
job.started
job.progress
job.warning
job.item.completed
job.item.failed
job.completed
job.failed
```

页面断线重连时通过事件序号续传。

### 9.5 写操作 API

```text
POST /api/followups/{id}/prepare-send
POST /api/followups/{id}/confirm-send
POST /api/evidence/{id}/prepare-feishu-update
POST /api/evidence/{id}/confirm-feishu-update
```

采用两阶段确认：

1. `prepare`：后端重新检查店铺、达人、产品、状态、模板、写入目标和执行上限，返回短期确认令牌和完整预览；
2. `confirm`：携带短期确认令牌、CSRF Token、操作人和预览内容哈希。

令牌必须一次性使用、短时间过期，并绑定具体操作、达人和产品。

## 10. 操作台页面设计

### 10.1 首页

优先展示：

1. 环境状态：紫鸟 GUI、Bridge、运行店铺、当前页面、飞书配置和最近同步；
2. 今日待办：人工确认、物流异常、今日到货、到期跟进、超过 10 天和普通等待送达；
3. 最近任务：任务名、进度、结果、开始时间和详情入口。

### 10.2 跟进列表

筛选条件：

- 店铺、阶段、状态和语言；
- 视频/直播类型；
- 产品以及是否 B005/B006-A；
- 是否需要人工确认；
- 是否存在内容证据。

默认不提供“一键全部发送”。可以支持多选导出，但批量平台写操作放到后续版本。

### 10.3 跟进详情

左侧展示事实：达人、产品、物流、送达时间、内容证据、飞书记录和最近消息状态。

右侧展示建议操作：推荐语言、达人类型、模板、完整消息、图片预览、风险提示、复制、标记人工发送和确认自动发送。

### 10.4 错误显示

业务员看到简洁错误；技术详情折叠显示错误代码、job ID、日志路径、数据源和诊断摘要。

网页不得显示飞书 `app_secret`、access token、Cookie 或完整平台敏感响应。

## 11. 本地安全设计

### 11.1 网络边界

Uvicorn 固定监听 `127.0.0.1`，禁止默认监听 `0.0.0.0`。

### 11.2 单实例

启动器流程：

1. 获取用户级实例锁；
2. 检查运行状态文件；
3. 验证已有服务是否属于本应用；
4. 已有服务则打开已有页面；
5. 没有服务则选择空闲端口并启动。

不能仅根据“端口有人监听”就认为是本应用。

### 11.3 本地会话

启动时生成随机 bootstrap token：

```text
http://127.0.0.1:<port>/bootstrap?token=<random>
```

服务验证后建立 `HttpOnly`、`SameSite=Strict` 会话 Cookie，bootstrap token 立即失效，并重定向到不含 token 的首页。

### 11.4 CSRF 与 Origin

所有写接口：

- 验证本地会话和 CSRF Token；
- 检查 `Origin`；
- 拒绝非本地 Host；
- 默认不开放 CORS；
- 仅接受 JSON 或明确的 HTMX 表单。

### 11.5 密钥

第一阶段继续兼容 gitignore 的 `config.toml`，但密钥不得传到前端、保存到 SQLite、写入日志、进入导出或打包资源。后续可迁移到 macOS Keychain 和 Windows Credential Manager。

### 11.6 写操作保护

前端确认不是唯一安全边界，后端必须重新验证操作类型、店铺、达人、产品、平台和飞书状态、幂等键、执行上限、预览哈希和确认令牌。

## 12. 跨平台数据目录

不得把数据库、配置和日志写入安装目录。

### macOS

```text
~/Library/Application Support/ZnSampleAssistant/
├── assistant.sqlite3
├── config/
├── exports/
├── logs/
├── backups/
└── runtime/
```

### Windows

```text
%LOCALAPPDATA%\ZnSampleAssistant\
├── assistant.sqlite3
├── config\
├── exports\
├── logs\
├── backups\
└── runtime\
```

需要提供统一路径函数，禁止业务代码手工拼接平台路径。

### 12.1 备份

- 应用升级数据库前自动备份；
- 每日首次启动可创建一次滚动备份；
- 默认保留最近若干份；
- SQLite 备份使用数据库备份 API，不直接复制正在写入的 WAL 文件；
- 导出和数据库备份分开。

## 13. 现有代码服务化计划

### 13.1 第一批：纯函数和领域模型

优先抽取：

- 物流状态标准化；
- 跟进日期计算；
- 话术模板；
- 产品与图片映射；
- 达人类型选择策略；
- 语言确认策略；
- 幂等键生成；
- 写操作状态规则。

这些代码不依赖 FastAPI、数据库或紫鸟，便于单元测试。

### 13.2 第二批：平台服务封装

为现有模块增加稳定的 Service 接口：

```python
class StoreService:
    def inspect_environment(...)
    def resolve_running_store(...)

class ShipmentService:
    def list_shipped_samples(...)
    def fetch_logistics_detail(...)
    def synchronize_shipments(...)

class MessageService:
    def preview_followup(...)
    def send_followup(...)

class FeishuService:
    def find_relation(...)
    def update_completion_status(...)
```

Service 返回结构化结果，不打印用户界面文本。

### 13.3 第三批：CLI 调用 Service

逐步让现有脚本调用 Service，确保 CLI 行为不退化，且 FastAPI 和 CLI 使用同一业务规则。

### 13.4 第四批：FastAPI 接入

FastAPI route 只负责参数解析、权限与确认、创建 job，以及返回页面或 JSON。route 中不直接写复杂业务逻辑。

## 14. 物流契约验证阶段

这是正式开发 SOP 2 前的阻塞项，应作为 Phase 0。

### 14.1 样本要求

至少准备：

- 1 个待揽收订单；
- 1 个运输中订单；
- 1 个已送达订单；
- 如果可能，1 个多包裹或物流异常订单。

### 14.2 只读验证内容

确认订单物流响应中的状态字段、状态文案、ETA、实际送达时间、最近轨迹、轨迹列表、包裹结构、承运商和时区/时间戳单位。

### 14.3 输出

形成脱敏 fixture：

```text
tests/fixtures/logistics_pending_pickup.json
tests/fixtures/logistics_in_transit.json
tests/fixtures/logistics_delivered.json
tests/fixtures/logistics_multiple_packages.json
```

不得将 Cookie、access token、真实地址、电话和真实达人敏感信息写入 fixture。

### 14.4 验收

- 全程只读；
- 不猜字段；
- 不把“已发货”当成“已送达”；
- 不把 ETA 当成实际送达时间；
- 能识别缺失送达时间；
- 多包裹策略明确。

## 15. 测试计划

### 15.1 单元测试

重点覆盖：

- 物流状态标准化；
- 时间戳与时区解析；
- D0/D+3/D+5/D+7/D+9 计算；
- 错过多个阶段时只生成最新任务；
- 已发现内容时抑制未发布任务；
- B005/B006-A 精确匹配；
- 视频+直播进入人工确认；
- 空简介语言进入人工确认；
- 模板版本和变量替换；
- 幂等键；
- 状态不回退；
- 未知发送结果不可自动重试。

### 15.2 数据库测试

覆盖唯一约束、SQLite WAL、job 原子领取、worker 并发保护、interrupted 恢复、跟进任务幂等、物流快照历史、数据库迁移和升级前备份。

### 15.3 FastAPI 测试

覆盖本地会话、CSRF、Origin、bootstrap token 单次有效、确认令牌过期、预览哈希变化、重复确认、SSE 断线续传和页面刷新后的任务恢复。

### 15.4 集成测试

使用 mock 适配器覆盖紫鸟可用/不可用、无 running 店、多店、页面错误、API 回退 DOM、飞书不可用、物流状态变化，以及消息/飞书写操作的成功、失败、冲突和未知状态。

### 15.5 跨平台测试

Windows 和 macOS 分别验证双击启动、默认浏览器、单实例、中文路径、用户数据目录、日志、导出、`ziniao-cli` 定位、进程退出、安装升级、防火墙/杀毒软件和 macOS Gatekeeper。

### 15.6 避免低价值测试

不为简单模板 HTML 或纯字段映射堆叠测试。重点测试状态机、时间、幂等、写操作安全、数据迁移和平台边界。

## 16. 打包与发布计划

### 16.1 依赖管理

新增 `pyproject.toml`，区分 runtime、development 和 packaging 依赖。依赖版本通过包管理器安装和锁定，不手写猜测版本。

### 16.2 PyInstaller

采用 one-folder。打包内容包括 FastAPI、Uvicorn、Jinja2 模板、HTMX 本地静态文件、Alembic migration 和应用版本元数据。不要从公共 CDN 加载 HTMX。

### 16.3 Windows

交付 one-folder 应用和安装器，提供开始菜单快捷方式、可选桌面快捷方式和卸载程序。卸载时默认保留用户数据。

### 16.4 macOS

交付 `.app`，完成代码签名和 notarization；应用数据写到 Application Support；不要求用户从终端启动。

### 16.5 版本升级

第一版采用人工下载安装包升级：停止旧服务、备份 SQLite、安装新版本、执行 migration、验证数据库并打开首页。migration 失败时保留旧数据库和备份，不继续启动写操作。

## 17. 分阶段实施计划

### Phase 0：物流与业务契约验证

工作：

- 捕获脱敏物流样本；
- 确认 ETA、实际送达和轨迹字段；
- 确认多包裹规则；
- 确认飞书“已完成”实际选项；
- 确认 B005/B006-A 精确映射；
- 固化 D0/D+3 等默认时间规则。

交付：契约说明、脱敏 fixtures、物流解析单元测试和未决问题清单。

验收：全程只读，无平台和飞书写操作，不猜接口字段。

### Phase 1：应用骨架

工作：

- 建立 `pyproject.toml`；
- FastAPI、Jinja2 和 HTMX；
- 本地会话和单实例；
- 自动打开浏览器；
- SQLite 和 Alembic；
- 用户数据目录；
- 健康检查和诊断页。

交付：业务员双击后可看到应用版本、数据库状态、紫鸟状态、running 店铺、飞书配置状态和日志位置。

验收：Windows/macOS 均可启动，不依赖终端，仅监听本地地址，页面中没有密钥，退出后进程正常结束。

### Phase 2：任务执行器与进度

工作：`jobs`、`job_events`、单 worker、店铺锁、心跳、interrupted 恢复、SSE、任务列表和详情、只读环境检查任务。

验收：页面刷新不丢任务；SSE 断线可恢复；重复点击不会创建相同运行任务；异常退出后任务标记为 interrupted；不自动重试写操作。

### Phase 3：只读物流同步

工作：`sample_cases`、`shipments`、`shipment_snapshots`、已发货样品读取、订单物流读取、状态标准化、物流页面和 CSV 导出。

验收：能区分已发货和已送达；能显示 ETA 和实际送达时间；缺少实际送达时间时进入人工确认；状态变化产生快照；默认不写飞书、不发私信。

### Phase 4：待办与话术预览

工作：`followup_tasks`、D0/D+3/D+5/D+7/循环状态机、超过 10 天名单、语言和达人类型策略、B005/B006-A 图片映射、20 类模板、复制话术、标记人工处理和今日待办首页。

验收：同一阶段不重复创建；发现内容后抑制未发送提醒；视频+直播不自动选模板；低置信语言需要确认；可以导出超过 10 天名单；仍不自动发送。

### Phase 5：人工内容证据

工作：`content_evidence`、手动添加视频/直播 URL、达人和产品关联、人工确认商品匹配、证据列表、感谢话术和“可更新飞书”队列。

验收：内容证据不能只按达人名匹配；模糊商品不能自动完成；未确认内容不抑制跟进；确认后抑制后续未发布提醒；不自动修改飞书。

### Phase 6：受保护的文本私信

工作：复用现有 IM SDK、`prepare-send` / `confirm-send`、一次性确认令牌、`write_audits`、发送确认、失败/未知状态和完整预览。

安全要求：默认每次 1 条；不默认全选；不点邀请和拒绝；结果未知不重发；写前审计；后端重新核对达人和产品；图片仍人工处理。

验收：以一条测试达人、明确授权、明确店铺和明确模板完成真实单条验收。

### Phase 7：受保护的飞书更新

工作：内容证据关联飞书记录、目标状态检查、两阶段确认、写后回读、状态不回退、无匹配不新建和冲突人工处理。

验收：达人 + 产品 + record ID 三重核对；当前状态不允许时拒绝；后续状态不回退；无匹配不创建；结果未知不自动重试。

### Phase 8：跨平台安装包

工作：PyInstaller、Windows 安装包、macOS `.app`、签名和公证、首次启动、升级与 migration、用户文档。

验收：由非技术业务员在干净电脑完成安装、启动、打开浏览器、查看环境、同步物流、查看待办、导出名单和退出，全程不要求打开终端。

### Phase 9：系统计划任务（可选后续）

不属于首版上线阻塞项。只自动执行环境检查、物流只读同步、待办生成和超过 10 天名单更新。

禁止计划任务自动执行批准、私信、图片发送、飞书写入和店铺切换。

## 18. 建议的里程碑

### M1：可启动

业务员能双击打开操作台，查看紫鸟和配置状态。包含 Phase 1–2。

### M2：可查看

业务员能同步物流、查看送达状态和物流历史。包含 Phase 3。

### M3：可安排

业务员能看到今日跟进待办、生成话术、导出名单。包含 Phase 4。

这是第一版最有价值、风险最低的可上线节点。

### M4：可记录内容

业务员能录入视频/直播证据，并自动停止不适用的跟进。包含 Phase 5。

### M5：可安全执行

业务员能从操作台逐条发送文本、更新飞书，并有完整审计。包含 Phase 6–7。

### M6：可分发

非技术业务员能在 Windows/macOS 安装和升级。包含 Phase 8。

## 19. 上线门槛

第一版进入业务使用前必须满足：

- Windows 和 macOS 均通过安装测试；
- 只监听 `127.0.0.1`；
- 密钥不出现在网页、数据库和日志；
- 数据库自动备份；
- 数据库 migration 可回退到备份；
- 只读物流同步连续运行多个样本无写操作；
- 跟进任务幂等测试通过；
- 页面刷新和应用重启不重复任务；
- 所有写操作仍有后端门闩；
- 私信和飞书写入分别完成真实单条验收；
- `unknown` 状态不会自动重试；
- CLI 应急入口仍可用；
- README 和 AGENTS 与新操作台规则同步。

## 20. 最终建议的实施边界

建议第一轮实际开发只做到 **M3**：

```text
应用启动
+ 环境诊断
+ SQLite
+ 持久任务执行器
+ 只读物流同步
+ 今日待办
+ 话术预览
+ CSV 导出
```

这个范围已经能够显著改善业务员体验，同时不会过早碰触：

- 自动私信；
- 图片上传；
- 内容自动判断；
- 飞书“已完成”写入；
- 系统无人值守写操作。

等 M3 在真实业务中稳定运行并积累物流、时间规则和待办反馈后，再进入 M4/M5。这样可以避免在业务定义尚不稳定时，把不可撤销操作做得过于自动化。
