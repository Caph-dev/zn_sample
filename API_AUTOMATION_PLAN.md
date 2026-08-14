# TikTok Shop 样品申请 API 化实施计划

## 1. 目标与边界

本计划把当前基于 React fiber、DOM 文本解析和 DOM 点击的自动化，逐步迁移为：

1. 保留紫鸟 GUI、店铺登录态、浏览器指纹和家宽 IP；
2. TikTok 请求始终从紫鸟店铺页面上下文发出；
3. 第一阶段将列表和达人详情改为 API 优先、DOM 兜底；
4. 第二阶段将已有许可范围内的 TikTok 写操作改为受门闩保护的 API 调用；
5. 飞书 API、SOP 判定、导出格式和现有危险操作门闩保持不变。

明确不做：

- 不从本机 Python `requests` / `httpx` 直接调用 TikTok 接口；
- 不提取或持久化 TikTok Cookie；
- 不新增「拒绝」「邀请」「发货」等写能力；
- 不放宽 `--execute --yes`、`--execute-limit`、批准前备份等安全纪律；
- 不把捕获到的达人完整响应、会话信息或凭证提交到 Git。

## 2. 已验证事实

2026-08-14 在紫鸟 GUI 的 1 号店、样品申请页面完成了只读验证。

### 2.1 待审核列表

接口：

```text
POST /api/v1/affiliate/sample/group/list
```

已观察的待审核请求体：

```json
{
  "tab": 10,
  "cur_page": 1,
  "page_size": 50,
  "search_params": [
    {
      "search_key": 1,
      "search_type": 2,
      "value": ""
    }
  ],
  "order_params": [
    {
      "order_key": 7,
      "order_type": 2
    }
  ]
}
```

已验证：

- 原样重放返回 HTTP 200 和完整 `agg_info`；
- 修改 `cur_page` 后返回合法分页结果；
- 去掉 `fp=verify_...` 后仍返回完整结果；
- 请求没有 `_signature`、`X-Bogus`、自定义签名头或显式 CSRF 头；
- 当前页面中 `tab=10` 对应待审核，`tab=30` 对应已发货。枚举值仍须通过契约测试保护，不能散落硬编码。

### 2.2 达人详情

接口：

```text
POST /api/v1/oec/affiliate/creator/marketplace/profile
```

已观察的请求体：

```json
{
  "creator_oec_id": "<creator_id>",
  "profile_types": [2]
}
```

页面会分别请求 `profile_types` 2、3、4、5。已验证：

- 原样重放返回 HTTP 200、业务 `code=0`；
- 将 `profile_types` 从 `[3]` 改为 `[2]` 后正常返回另一组指标；
- 去掉 `fp=verify_...` 后仍成功；
- 响应包含粉丝性别/年龄、视频和直播 GPM 等现有筛查所需数据。

### 2.3 紫鸟限制

ZClaw 会对包含 Cookie 获取特征的脚本做静态拦截。正式实现必须：

- 绝不读取 `document.cookie`；
- 不在日志、导出或本地文件保存 Cookie；
- 对页面请求中无关的 `cookie_enabled` 参数优先做可删除性验证；若必须保留，只在运行时安全构造参数名，避免被静态规则误判；
- 所有请求仍经 `ziniao-cli zclaw invoke execute_script` 在目标店铺页面中执行。

## 3. 总体架构

```text
screen_sample_requests.py
        |
        v
SampleDataSource (统一行模型)
        |
        +-- api  -> page_api.py -> 紫鸟页面上下文 -> TikTok 同源接口
        |
        +-- dom  -> sample_dom.py / creator_detail.py
        |
        +-- auto -> API 优先；只读失败时回退 DOM
        |
        v
filters.py -> export_util.py -> 可选 execute 流水线
```

核心原则是让 API 适配器返回与现有 DOM 模块相同的标准字典。`filters.py`、主推款匹配、飞书逻辑和导出层不应感知数据来源。

建议新增模块：

| 文件 | 责任 |
|------|------|
| `scripts/lib/page_api.py` | 页面上下文 HTTP 传输、业务码检查、超时、重试、响应大小限制和日志脱敏 |
| `scripts/lib/sample_api.py` | 待审核列表请求、分页、响应解析、标准行映射 |
| `scripts/lib/creator_api.py` | `profile_types` 请求、响应合并、标准详情字段映射 |
| `scripts/lib/sample_data_source.py` | `api` / `dom` / `auto` / `shadow` 编排与回退原因记录 |
| `scripts/lib/sample_write_api.py` | 第二阶段批准接口；仅暴露明确的业务方法，不提供任意 URL 写请求 |
| `scripts/lib/im_api.py` | 第二阶段私信发送接口；保持消息指纹去重和 execute 门闩 |

### 3.1 页面上下文传输

优先顺序：

1. 先验证 ZClaw 是否能等待返回 Promise；若可以，使用页面上下文 `fetch`；
2. 若不能，使用“启动异步请求 + request_id 轮询结果”的模式；
3. 同步 XHR 只作为兼容兜底，因为它会阻塞页面主线程且无法可靠设置超时。

传输层必须具备：

- 仅允许当前 TikTok 联盟中心同源的相对路径；
- 只读阶段仅允许 `GET` 和已登记的查询型 `POST`；
- 响应同时校验 HTTP 状态和业务 `code`；
- 有限网络重试和指数退避；
- 限制响应大小，防止大响应阻塞 Bridge；
- 日志只记录 endpoint、耗时、状态、业务码、行数和 fallback 原因；
- 不记录 Cookie、完整头像签名 URL、达人简介全文或完整原始响应。

## 4. 第一阶段：列表 + 详情只读 API 化

### 4.1 里程碑 A：建立可测试的契约样本

1. 使用现有 hook 方法捕获列表及 `profile_types` 2、3、4、5 的响应；
2. 对响应做脱敏，只保留字段结构和人工构造的示例值；
3. 将脱敏 fixture 放入 `tests/fixtures/`；
4. 记录 endpoint、方法、最小 query 参数、请求体和业务错误结构；
5. 逐项移除非必要 query 参数，形成最小请求契约；`fp` 已验证可删除，但生产初期可以保留页面原值以降低行为差异。

验收条件：fixture 不含真实达人资料、店铺 ID、会话信息或签名 URL，且解析测试可离线运行。

### 4.2 里程碑 B：实现列表 API 客户端

实现 `sample_api.scrape_pending_list_api()`：

1. 仍先用现有页面校验确认用户在样品申请页；
2. 不再依赖 DOM 翻页按钮；
3. 从 `cur_page=1` 开始请求，依据 `has_more` 或稳定的行数/页数规则翻页；
4. 使用 `apply_id` 去重；
5. 遵守现有 `max_pages` 和 `max_rows`；
6. 将 API 响应映射为 `sample_dom.scrape_pending_list()` 当前输出的标准行。

必须覆盖的标准字段：

- `apply_id` / `apply_ids`；
- `product_id` / `product_title` / `sku_id` / `sku_desc`；
- `can_be_approved` / `review_status`；
- `creator_id` / `tt_uid` / `creator_name` / `nick_name`；
- `follower_num` / `gmv` / `item_sold` / `fulfillment_rate`；
- `content_video_views` / `pps_score`；
- `categories` / `top_follower_gender` / `top_follower_ages`；
- `_list_href`；
- 新增内部诊断字段 `_data_source=api`，导出时默认不暴露原始响应。

若 API 字段缺失，不得从字段位置猜测；应抛出 `ApiSchemaError`，由 `auto` 模式回退 DOM。

### 4.3 里程碑 C：实现详情 API 客户端

实现 `creator_api.fetch_creator_detail_api()`：

1. 按 `creator_id` 请求 `profile_types` 2、3、4、5；
2. 支持合并请求的能力探测；若服务端不支持，保持四个小请求；
3. 将响应合并成现有 `fetch_detail_for_row()` 的标准字段；
4. 不再打开详情页面，不再 `history.back()`；
5. 缺少关键指标时，`auto` 模式按达人回退到 DOM 详情；
6. 保留 `require_detail=True` 的现有语义。

必须映射和核对的字段：

- `video_gpm` / `video_gpm_n`；
- `live_gpm` / `live_gpm_n`；
- `overall_gpm` / `overall_gpm_n`；
- `avg_video_views` / `avg_video_views_n`；
- `video_engagement` / `video_engagement_n`；
- `avg_live_views` / `avg_live_views_n`；
- `live_engagement` / `live_engagement_n`；
- `est_post_rate` / `est_post_rate_n`；
- `revenue_per_buyer` / `aov_detail_n`；
- `bio`；
- `creator_type`。

实测 profile API 不返回 `bio`。简介不属于筛查条件，第 6 步语言识别和介绍私信继续显式使用详情 DOM；筛查 API 适配器将 `bio` 留空，不把缺失值伪装成接口数据。其他筛查核心指标缺失时，`auto` 必须按达人回退 DOM。

### 4.4 里程碑 D：数据源编排和 CLI

新增：

```text
--data-source dom|api|auto|shadow
```

迁移顺序：

1. 初始默认 `dom`，保证不改变现有日常行为；
2. `shadow`：API 和 DOM 都读，只用 DOM 做最终判定，输出脱敏差异报告；
3. 达到一致性门槛后，将推荐命令改为 `--data-source auto`；
4. 再经过稳定期后，可考虑把默认改为 `auto`；
5. `api` 模式 API 失败即报错，适合定位接口问题；
6. `auto` 模式 API 失败自动回退 DOM，并记录 `fallback_reason`。

只读回退是安全的，可以自动执行。以下情况触发回退：

- Bridge/网络错误；
- HTTP 非 2xx；
- 业务 `code != 0`；
- 响应 JSON 无法解析；
- 标准字段缺失或类型异常；
- 列表重复、分页不前进或总数明显不一致；
- 详情缺少正式筛查所需的核心 GPM 数据。

### 4.5 里程碑 E：一致性和性能验证

对同一批最多 10 个达人执行 shadow 对比：

1. 标识一致：`apply_id`、`creator_id`、`product_id` 100% 一致；
2. 列表数量一致；允许页面实时变化时按 `apply_id` 解释差异；
3. 金额、数量、百分比经同一解析器规范化后相等；
4. API 与 DOM 产生的 `eligible` 和原因必须一致；
5. 详情字段缺失率不高于 DOM；
6. 连续至少 3 次实际运行无不可解释差异；
7. 全量详情耗时相较 DOM 导航至少降低 60%，或给出可复现的基准结果。

建议测试层次：

- fixture 单元测试：请求体、响应解析、字段映射、分页；
- adapter 测试：API/DOM 都能产生同一标准行模型；
- shadow 集成测试：真实 1 号店、`--max-rows 6`，不执行写操作；
- 故障注入：HTTP 错误、业务码错误、缺字段、超时、重复页，验证 DOM 回退。

### 4.6 第一阶段完成标准

- 推荐只读命令可使用 `--data-source auto`；
- 正常情况列表不读 React fiber、详情不打开页面；
- API 异常时自动回退现有 DOM 路径；
- `filters.py`、主推匹配、导出字段和筛查结果不发生语义变化；
- 日志可看出每行/每个详情来自 API 还是 DOM；
- 不新增任何 TikTok 写请求。

## 5. 第二阶段：TikTok 写操作 API 化

第二阶段必须在第一阶段稳定后单独实施。写请求与只读请求的关键区别是：超时可能代表“服务端已成功但客户端没收到响应”，因此不能像只读请求一样盲目重试或自动回退 DOM。

### 5.1 范围

按优先级拆分：

1. 2A：样品申请「同意」API；
2. 2B：达人介绍私信 API；
3. 2C：物流单号私信 API。

继续禁止：

- 拒绝申请；
- 点击或调用邀请能力；
- 平台发货写操作；
- 绕过 `--execute --yes`；
- 无上限批量操作。

飞书写入已经是 API，不属于本阶段的 TikTok 接口迁移范围。

### 5.2 写接口发现

发现阶段只观察现有受门闩 DOM 流程产生的真实请求：

1. 用户明确授权一次 `--execute --yes --execute-limit 1`；
2. 批准前按现有流程写本地备份；
3. main-world hook 记录 method、endpoint、脱敏请求结构、响应业务码；
4. 仍由现有 DOM 点击完成这一次真实操作；
5. 捕获期间禁止自动重放写请求；
6. 记录是否存在预检、批准、二次确认等多个接口；
7. 识别 `apply_id`、店铺、商品、幂等键、CSRF/签名、时间戳和一次性 token 的绑定关系。

私信接口按同样方式分别捕获介绍话术和物流话术，但每次只允许真实发送 1 条，且必须先通过现有会话指纹去重。

### 5.3 批准 API 客户端

`sample_write_api.approve_application()` 必须是窄接口，只接受明确字段，例如：

```python
approve_application(store_id, apply_id, expected_creator_id, expected_product_id)
```

禁止暴露“任意 endpoint + 任意 body”的通用写方法给业务 CLI。

执行前门闩保持现状：

1. 同时存在 `--execute --yes`；
2. `execute_limit` 默认且最低按 1 处理；
3. 已写批准前备份；
4. 行通过最终 SOP 判定；
5. `apply_id`、达人、商品和主推货号映射完整；
6. `can_be_approved` 为真；
7. 启用飞书写入时已完成查重；
8. 使用第一阶段列表 API 再做一次即时状态预检，确认仍在待审核。

执行后必须通过只读 API 验证：

- `apply_id` 不再处于待审核；或
- `review_status` 已变为批准状态；或
- `can_be_approved` 已变为 false 且状态与批准一致。

只有响应成功且状态验证成功时，才设置 `approve_status=approved`，随后才允许写飞书。

### 5.4 写请求的失败与回退策略

| 失败位置 | 行为 |
|----------|------|
| 发请求前 Bridge/校验失败 | 未产生写请求，可安全停止；允许用户明确选择 DOM 路径重跑 |
| 服务端明确返回未执行的业务错误 | 标记失败，不自动点击 DOM；修复原因后人工重跑 |
| 超时、断连、响应无法解析 | 标记 `unknown`，先用只读 API 查状态；禁止立即重试或自动回退 DOM |
| 查状态确认已经批准 | 视为成功，继续后续飞书步骤 |
| 查状态确认仍待审核 | 只有在重试策略明确允许且仍在 execute limit 内时，才可重试一次 |
| 状态无法确认 | 停止本轮，导出人工核对项 |

写路径默认不做 API -> DOM 自动回退，避免同一申请被重复批准。若保留 DOM 兼容路径，使用显式参数：

```text
--write-source dom|api
```

迁移期默认 `dom`，API 通过验收后再改推荐值；任何来源都必须受相同 execute 门闩保护。

### 5.5 私信 API 客户端

`im_api.send_message()` 必须保留：

- `--execute --yes`；
- 默认 `execute-limit=1`；
- 英语/西班牙语模板白名单；
- 介绍话术和物流话术指纹去重；
- 禁止发送空消息或任意自由文本；
- 发送后通过会话只读接口验证消息指纹；
- 超时后先查会话，不盲目重发。

若平台仍限制“只能给合作过的达人发信”，API 客户端必须把服务端业务错误原样归类，不得尝试绕过。

### 5.6 审计记录

每次写操作至少记录：

- 本地运行 ID 和时间；
- storeId、apply_id、creator_id、product_id 的脱敏/必要值；
- 写来源 `dom` 或 `api`；
- 请求体结构摘要及稳定哈希，不保存 Cookie/token；
- HTTP 状态、业务码、耗时；
- 写后状态验证结果；
- 飞书后续动作及 record_id；
- `success` / `failed` / `unknown` 最终分类。

### 5.7 第二阶段完成标准

- 经用户授权的单条批准 API 流程通过；
- 连续多次限量运行无重复批准、无误批、无不明状态遗留；
- 网络超时场景验证“先查状态、后决定重试”；
- API 与 DOM 批准结果、导出状态和飞书顺序一致；
- 私信发送具备消息指纹幂等验证；
- DOM 写路径仍可作为显式、人工选择的紧急兼容方案；
- AGENTS.md 和 README.md 同步写明新的数据源、写来源和安全门闩。

## 6. 推荐实施顺序

1. 新增脱敏 fixture 和 `page_api.py`；
2. 实现列表 API 解析与离线测试；
3. 实现详情 `profile_types` 合并与离线测试；
4. 新增 `--data-source shadow`，产出一致性报告；
5. 修正字段差异，完成 3 次只读真实店验证；
6. 启用 `--data-source auto`，API 优先、DOM 兜底；
7. 稳定运行一段时间后，再开始批准写接口发现；
8. 上线 `--write-source api` 的单条限量试运行；
9. 最后迁移介绍私信和物流私信，并保留 DOM 显式回退。

## 7. 第一批实现任务

- [x] 建立 `page_api.py` 的同源请求 allowlist 和脱敏日志；
- [x] 建立脱敏列表和详情 fixture；
- [x] 明确列表响应到标准 row 的完整字段映射；
- [x] 明确 `profile_types` 2/3/4/5 各字段归属；
- [x] 完成列表分页和响应 schema 测试；
- [x] 完成详情合并和指标规范化测试；
- [x] 新增数据源编排和 `--data-source`；
- [x] 新增 shadow 差异报告；
- [x] 用 1 号店 `shadow --detail-all --max-rows 6` 做三轮只读对比：三轮列表均为 6/6 且零字段差异；详情可比较数据没有不可解释的数值差异。第 1 轮有一行 DOM 卡片显示 `--` 而 API 返回完整指标；第 2 轮有一行 DOM 遭遇 Bridge 瞬断；加入待审核页加载重试、详情只读网络重试和容差边界修复后，第 3 轮 6/6 达人的 8 个核心字段全部一致；
- [x] 真实样本覆盖视频达人、视频+直播达人、Live GPM 为 0、低于 1% 互动率；离线契约测试补齐直播达人、双侧 GPM 为 0、可选字段无权限、核心 GPM 无权限须回退；
- [x] 完成两个不同达人详情 shadow（修复低于 1% 的 DOM 百分比放大问题后，8 个核心字段一致）；
- [x] 验证两达人 `auto` 模式全程使用 API，并更新 README.md / AGENTS.md；
- [ ] 扩大真实达人样本并稳定运行后，再考虑把默认数据源从 `dom` 改为 `auto`。
