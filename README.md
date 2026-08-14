# zn_sample · TikTok Shop 样品申请筛查

本项目用于处理 TikTok Shop **联盟中心 → 样品申请管理 → 待审核** 的达人样品申请：按业务 SOP 初筛、详情复筛，导出名单；经明确授权后，可批准申请、写入飞书达人关系表，并分阶段发送介绍私信和物流私信。

非技术人员请先阅读：[使用方法.md](./使用方法.md)。

技术维护、安全门闩和数据契约见：[AGENTS.md](./AGENTS.md)。

业务原始规则见：[样品申请筛查 SOP](./样品申请筛查sop/样品申请筛查sop.md)。

## 能力总览

| 能力 | 当前状态 | 说明 |
|---|---|---|
| 自动进入待审核 | 支持 | 脚本 1 打开店铺；用户登录并停在商家中心首页后，脚本 2 可自动进入联盟中心的待审核页 |
| 列表初筛 + 详情复筛 | 支持 | 正式命令使用 `--with-detail --require-detail`；主推款是硬性条件 |
| 自动批准申请 | 支持；默认 API，DOM 为显式备用路径 | 必须同时使用 `--execute --yes`；默认最多 1 条，批准前后均有只读状态检查 |
| 批准后写入飞书 | 支持 | 额外使用 `--write-feishu`；只有批准状态确认成功后才写入 |
| 介绍私信 | 支持；默认页面内 IM SDK | 独立脚本；按达人简介判断英语/西班牙语，真实发送须 `--execute --yes` |
| 物流回写和物流私信 | 支持；列表/物流默认 API，私信默认 IM SDK | 独立脚本；北京时间 16:00 前默认阻止，第 8、9 步不会自动触发 |
| TikTok 批准 API | 已实现，现为默认批准路径 | 根据一次真实 DOM 请求实现为窄接口；尚未用第二条真实申请重复执行验收，DOM 可用 `--write-source dom` 显式回退 |

## 业务流程

```text
脚本 1：安全打开目标店铺
  → 用户登录并停在商家中心首页
脚本 2：自动进入「联盟中心 → 样品申请管理 → 待审核」
  → 列表初筛
  → 通过初筛者读取达人详情并复筛
  → 强制检查申请样品是否为主推款
  → 导出 xlsx / csv / json
可选：明确授权批准
  → 批准成功并确认移出待审核
  → 可选写入「达人关系管理（新）」
可选：独立运行第 6 步介绍私信
后续：第 7 步查物流 → 第 8 步回写飞书 → 第 9 步发物流私信
```

默认运行只读，不会点击「同意」「拒绝」，不写飞书，也不发送私信。平台批准和已发送私信不能由脚本撤销。

## 快速开始

### 1. 首次配置

```bash
cd ~/Projects/zn_sample
cp -n config.toml.example config.toml
```

在 `config.toml` 中填写飞书主推表所需的 `[feishu].app_secret`。如果要在批准后写入达人关系表，还要配置 `[feishu.bitable]`。密钥不得提交 Git 或发送到聊天中。

### 2. 检查紫鸟并打开店铺

```bash
ziniao-status
ziniao-cli doctor
python3 scripts/open_sample_store.py --store-id <店铺编号>
```

脚本 1 不会关闭或重开已经运行的目标店铺；检测到其他店铺运行时会停止并提示人工处理。随后由用户完成 TikTok Shop 登录，停在商家中心首页。

### 3. 自动导航并进行只读筛查

```bash
python3 scripts/screen_sample_requests.py \
  --store-id <店铺编号> \
  --from-seller-home \
  --data-source auto \
  --with-detail \
  --require-detail
```

`auto` 表示只读 API 优先，接口异常时回退页面读取；批准等写操作仍必须使用 `--data-source dom`，批准来源另由 `--write-source dom|api` 控制，默认是 `api`。如果已经手动停在待审核页，可去掉 `--from-seller-home`。

结果默认写入 `exports/`：

- `.xlsx`：业务查看；
- `.csv`：表格导入；
- `.json`：后续介绍私信脚本输入；
- `*_pre_execute.*`：真实批准前备份。

常用只读选项：

```text
--eligible-only       只导出通过名单
--max-rows 6          测试时限制扫描行数
--detail-all          所有行都拉详情，较慢，主要用于排查
--out exports/name    指定导出前缀
```

正式流程不要省略 `--with-detail --require-detail`，不要使用已移除的 `--detail-limit`，不要使用 `--skip-hero-check` 绕过主推款条件。

## 飞书数据位置

### 主推款数据源

条件 2 使用飞书知识库电子表格 `tk产品图+货号`，以「是否主推=是」为准：

<https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc>

它与达人关系表不是同一个资源。

### 达人关系表

批准后如使用 `--write-feishu`，默认写入：

- Base：内部达人建联记录表；
- 数据表：**达人关系管理（新）**；
- 视图：达人管理总表；
- 链接：<https://rsed6zggjt.feishu.cn/base/CJXSbLIQWahB8esVOiscX7j1nLc?from=from_copylink>。

新记录固定初始化：

```text
人员       = 王良希（技术）
合作状态   = 待发货
是否已寄样 = 否
```

物流成功写入后，才允许把 `待发货` 推进为 `待发布`；不回退 `已发布` 等后续状态。

## 写操作边界

### 批准

当前默认使用已捕获的批准 API；页面 DOM 仍保留为显式备用路径。两条路径都必须使用：

```bash
--data-source dom --execute --yes --execute-limit 1
```

如需使用页面 DOM 备用路径，增加：

```text
--write-source dom
```

执行前会检查申请仍在待审核、`creator_id` / `product_id` 与原结果一致且 `can_be_approved=true`；DOM 路径会点击「同意」，API 路径调用已登记的单条批准接口。API 写入不自动重试，也不回退 DOM。执行后会确认申请进入「待发货」且 `curr_status=20`；无法确认时标记 `unknown`、停止本轮并禁止自动重试。

此前真实验收使用的是 `franciscarodrig227` 的 DOM 批准，随后确认进入待发货并完成飞书联动；当前默认已切换为 API，但 API 路径尚未用第二条真实申请重复批准验收。若 API 请求状态不确定，脚本不会自动重试或回退 DOM。

### 介绍私信

独立运行：

```bash
python3 scripts/send_sample_intro.py \
  --from-export exports/sample_screen_<时间戳>.json \
  --execute --yes --execute-limit 1
```

空简介默认英语，仅支持英语/西班牙语；默认调用页面内 IM SDK，不点「发送」按钮。平台不允许发送时不绕过限制，不点击「邀请」。

### 第 7–9 步

独立运行：

```bash
# 第 7 步只读预演；16:00 前测试才加 --force
python3 scripts/sync_shipped_tracking.py --force --max-rows 1

# 第 8 步回写飞书
python3 scripts/sync_shipped_tracking.py --write-feishu --max-rows 1

# 第 8 步成功后，最多发送 1 条第 9 步物流私信
python3 scripts/sync_shipped_tracking.py \
  --write-feishu --send-tracking --execute --yes --execute-limit 1
```

第 7 步默认用样品列表 API `tab=30` 和商家物流 GET 读单号；第 9 步默认走 IM SDK。第 9 步发送的是 **TikTok 物流单号**，不是 `main_order_id` / 订单 ID。第 7 步不会自动触发第 8、9 步；是否写回和发送由命令开关控制。

## 文档分工

| 文档 | 面向对象 | 内容重点 |
|---|---|---|
| [使用方法.md](./使用方法.md) | 业务员 | 复制命令、登录、日常操作、故障处理 |
| [README.md](./README.md) | 项目使用者 | 能力边界、快速开始、业务流程 |
| [AGENTS.md](./AGENTS.md) | Agent / 技术维护 | 安全纪律、技术契约、数据源和实现约束 |
| [API_AUTOMATION_PLAN.md](./API_AUTOMATION_PLAN.md) | 技术维护 | API 化阶段、验证结果和未完成事项 |
| [SOP_0814_FLOW_COMPARISON.md](./SOP_0814_FLOW_COMPARISON.md) | 业务 / 技术 | 0814 SOP 与当前实现逐步对照 |

紫鸟 GUI / WebDriver / CLI 的通用启停说明统一见姊妹仓库：[`../zn_daren/AGENTS.md`](../zn_daren/AGENTS.md)。

## 关键禁止事项

- 不要把账号密码、`app_secret`、`apiKey` 或 Cookie 写入仓库或聊天；
- 不要运行 `ziniao-cli page extract --mode running` 来识别当前店铺，它可能在无 debug port 时触发 `runtime.reopen`；
- 不要用 `--skip-hero-check` 进行正式筛查；
- 不要使用已移除的 `--detail-limit`；
- 不要在没有 `--execute --yes` 的情况下新增批准或发送路径；
- 不要点击「拒绝」或「邀请」。
