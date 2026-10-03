# TikHub 视频分页与七天窗口契约

## 结论与适用范围

- 调查日期：2026-10-03。
- 契约结论：**UNVERIFIED**；生产七天边界提前停止：**BLOCKED**。
- 已完成公开资料调查与保守分页保护测试；没有实现或启用时间边界优化。
- 适用接口：`GET /api/v1/tiktok/app/v3/fetch_user_post_videos`（TikTok-App-V3-API，文档标题为主页作品数据 V1）。公开导出的 OpenAPI 为 `3.0.1`，`info.version=1.0.0`；这不是分页一致性的版本保证。
- 当前请求：`sort_type=0`、`count=20`、首页 `max_cursor=0`，后页原样传回响应中的整数 `max_cursor`；首页使用 `unique_id`，验证作者后使用 `sec_user_id`。不传 `region`，文档描述默认美国地区。没有修改请求参数或 endpoint。

**无法安全启用**：公开文档只将排序选项描述为“最新”，没有提供足以证明后续所有视频均早于窗口起点的排序、置顶覆盖、游标上界和动态一致性保证。缺少任一项即继续保守分页；不能用样本或本文件自签 VERIFIED。

## 可审查的证据来源

### S1：供应商公开 OpenAPI

- 页面：https://docs.tikhub.io/186826108e0
- Markdown/OpenAPI 导出：https://docs.tikhub.io/186826108e0.md
- 读取日期：2026-10-03；先检索官方域名，再读取以上两个精确 URL。
- 页面 HTML 的正文抽取缺少参数说明；Markdown 导出包含接口路径、参数 description、schema 和 ResponseModel。下列引用来自实际读取的 Markdown 导出，不以搜索摘要作证明。
- 原文语义（保留参数名，移除渲染器的 Markdown 转义）：
  - `sort_type: 排序类型，0-最新，1-热门`；英文 `Sort type, 0-Latest, 1-Hot`。
  - `max_cursor: 最大游标，用于翻页，第一页为0，第二页为第一次响应中的max_cursor值。`
  - `count: 最大数量，建议保持默认值20。`
  - `sec_user_id` 为空才使用 `unique_id`；优先级为 `sec_user_id > unique_id`。
  - `region` 不传时默认使用美国地区数据。
- ResponseModel 只给出通用响应封装，`data` 为泛化字段；该导出没有定义作品列表的置顶标记、标记类型、跨页 create_time 上界或快照 token。
- 通用缓存说明为“缓存仅用于请求溯源，不影响接口数据的时效性，也不会再次通过接口返回”。这是单次响应溯源，不是跨页固定快照的承诺。
- 本地取证快照：`/tmp/zn-sample-013-tikhub-v3-20261003.md`（公开资料的抽取文本，不是原始 HTTP 响应；临时文件不提交，可能被系统清理）。SHA-256：`7dbf6586740c23972e99a0e7269a9556240d31a37a4c0fdae52d80440ce44353`。

调查命令为 `smart-search exa-search`（查询 `TikHub app v3 fetch_user_post_videos sort_type max_cursor pinned ordering`，限定 `docs.tikhub.io`，3 个结果），随后 `smart-search fetch` 读取页面与 `.md` 导出，导出文本保存到上述本地快照。没有访问需要 TikHub 凭据的元数据接口；公开网页的检索/提取不构成真实达人采样。

### S2：本地实现与既有测试

- `scripts/lib/tiktok_creator_videos.py::TikHubClient.collect`：作者核验、时间合法性、完整 metadata 去重及冲突拒绝；游标按不透明整数回传。
- 同文件顶部的 2026-09-06 OpenAPI 检查说明只证明参数名，不提供排序/置顶/快照保证。
- `tests/test_tiktok_creator_videos.py::test_username_contract_identity_pagination_and_pinned_old_video`：第一页旧视频之后和后页仍可有近期视频。因此不能见旧即停；测试不是供应商契约。
- `tests/fixtures/tiktok_shopping_anchors.json` 仅用于购物锚点形状，不证明分页顺序，也不将其历史身份数据复制到新 fixture。
- 本轮没有读取或提交真实达人媒体、签名 URL 或付费响应样本。

## 四个必须回答的问题

### 1. `sort_type=0` 是否保证非置顶作品跨页 create_time 非增序？

**未证明。** S1 的“最新”可以解释排序选项的意图，不能推出所有非置顶作品跨页单调、等时作品稳定分组或同时间作品不被漏掉。供应商没有在读取资料中给出等时间戳的 tie-break/分页规则。

当前按 `aweme_id` 去重，重复 metadata 完全一致才忽略，冲突即拒绝；这只能处理已经返回的重复，不能证明未请求页没有近期作品。

### 2. 置顶如何标记，何时出现，窗口内置顶是否全部覆盖？

**未证明。** S1 没有规定标记字段/类型、只在首页出现、后页重复/夹入规则，或在提前停止前已覆盖所有窗口内置顶作品的保证。不猜 `is_top` 等字段。

新 fixture 的 `synthetic_pinned_video_ids` 是解释反例的测试注释，不映射到 provider payload，不代表真实置顶字段；生产不会读取这个注释。

### 3. 游标是否给出后续页时间上界，动态发布/置顶是否影响分页？

**未证明。** S1 只要求将上次响应的 `max_cursor` 用于下一次请求。即使其值看起来像毫秒时间戳，也不能除以 1000 用作时间边界。没有跨页回跳/插入/重排保证，也没有分页期间新发布、删除、置顶变化的快照一致性说明。固定 `window_end` 只固定本地判定窗口，不固定供应商分页数据。

### 4. 是否存在后续视频不可能属于当前七天窗口的可证明边界？

**读取的权威来源没有给出。** `has_more=1`、一整页旧作品、连续几页降序或连续几页旧作品均不充分；不能把这些观察升级为窗口完整。

## 生产行为与异常处理（保持原契约）

- 仍可能扫描到原有 10 页/200 条上限，不提高额度。窗口外 metadata 仅不加入审核视频，仍经过作者、时间、重复冲突校验，不因“看起来已过七天”跳过。
- 仅原有自然结束 `stop_reason=complete` 返回 `complete=True`。页数/条数上限、游标停滞和正面回调短路均为 partial。
- 七天起点与固定终点均包含；比起点早 1 秒排除。终点之后 300 秒内延续原校验容差，但不计入窗口；超过终点 300 秒仍拒绝。
- 作者不符、sec_uid 冲突、metadata 冲突/非法时间仍失败关闭；空的继续页或重复游标返回 partial。采集失败/partial 不代表完整不足四条。
- 至少四条相关加正面展示仍可按已知下界通过并短路；相关不足且 partial/未知锚点待复核，原有完整不足四条失败，四条无正面待复核。
- 本轮未加入 `window_exhausted:*`、契约版本登记、信任开关或性能计数新原因，未改 proof keys、HMAC、REVIEW_VERSION、缓存或批准门。
- 计划 Step 3 中对独立 `_proof_verdict` 的完整性原因白名单也未提前实施：只有整体契约经审查 VERIFIED 才进入该实施阶段，不能把现有布尔值判断描述为已经收紧。

## 已交付的离线保护

`tests/fixtures/tiktok_video_ordering_cases.json` 与 `tests/test_video_window_boundary.py` 用统一假 handle/sec_uid、合成 ID、可读 UTC 基准及相对秒数重放真实 collect，包含 16 个案例：旧置顶、整页旧后有新、页内/跨页乱序及降序后回跳、边界、未来时间、重复/冲突、游标/空页、限额、作者冲突和十页自然结束。JSON 没有可执行代码或真实身份/媒体。

`tests/test_creator_video_review.py::test_old_page_cannot_hide_later_content_or_change_proof_semantics` 用真实 collect 与真实 `_proof_verdict` 覆盖后页三条完整/partial/未知锚点、四条有正面/无正面；视觉结果为本地 stub，正面结果通过真实签名和帧哈希验证。旧页不能隐藏后页内容，正面短路保持 partial。

离线请求数以实际 collector 计数为准：十页 fixture 请求 10 页后自然 complete，只有 2 个窗口内唯一 ID；旧页之后的内容集成样本请求 2 页。**没有“verified 2 页 vs baseline 10 页”对照，节省页数尚不可验收，不承诺生产加速倍率。**

## 解锁条件与独立审查

需供应商提供适用于上述 endpoint/参数/版本的可引用保证，明确：

1. 跨页时间排序与等时作品的完整覆盖规则，或另一种足以证明后续时间上界的契约。
2. 真实置顶标记及类型、出现/重复规则、窗口内置顶的完整覆盖点。
3. 游标后续时间上界和动态发布/置顶变化下的一致性；固定 end 不能代替这一保证。
4. 已定义异常/违约行为和版本适用条件；必要字段缺失/违约必须回到原保守扫描，不能假 complete。

独立审查记录：

- 用户指定技术维护者：**待指定**。
- 审查日期：**未发生**。
- 审查结论：**待审查；不允许启用**。
- 执行 Agent 的本次公开资料调查不等于独立技术维护者审查。

上述证据和独立审查齐全后，才可将契约改为 VERIFIED 并继续 Plan 013 Step 3–5（生产判断、独立 proof 完整性原因核对、两条路径结果等价与请求数测量）。若需要新增 proof schema/版本，应停止并单独提出变更方案。真实 TikHub 采样另需用户明确授权目标与小规模额度；样本只能佐证，不能替代契约或独立审查。
