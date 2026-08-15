# zn_sample · TikTok Shop 样品申请筛查

按 SOP 筛「联盟中心 → 样品申请管理 → 待审核」，导出名单。经明确授权后可批准、写飞书、发介绍/物流私信。

| 文档 | 分工 |
|---|---|
| [使用方法.md](./使用方法.md) | 业务员：登录、逐步复制命令、故障处理 |
| **本文件** | 能力边界、SOP 对应关系、常用命令 |
| [AGENTS.md](./AGENTS.md) | Agent / 维护：纪律、数据 ID、实现契约 |
| [样品申请筛查 SOP](./样品申请筛查sop/样品申请筛查sop.md) | 业务规则原文 |

默认只读：不点同意/拒绝，不写飞书，不发私信。平台批准和已发私信不能由脚本撤销。

## SOP 步骤 → 脚本 → 停在哪

四个脚本断开，**不会**自动进入下一步。

| SOP | 脚本 | 默认停在 | 加开关后 | 不会做 |
|---|---|---|---|---|
| 开店 | `open_sample_store.py` | 商家中心首页前。店已开不重开 | 无。登录/验证码人工 | 不进联盟中心 |
| 1–4 进待审核、筛人、出名单 | `screen_sample_requests.py` | 导出 xlsx / csv / json | 正式必须 `--with-detail --require-detail` | 不加开关不批、不写表、不发私信 |
| 5 同意 / 新建达人关系 | 同上 | 不批不写 | `--execute --yes` 同意；再加 `--write-feishu` 写表 | 不发第 6 步 |
| 5 **补写** | 同上 + `--confirm-export` | 只核对「待发货」、可补飞书 | 平台约 10 分钟才刷到待发货；不重批 | 不发私信 |
| 6 介绍私信 | `send_sample_intro.py` | 只读预演 | `--execute --yes` 真发 | 不批准、不查物流 |
| 7 读已发货 / TikTok 物流 | `sync_shipped_tracking.py` | **北京时间 16:00 前直接拒绝** | 16:00 后只读即可；测试才 `--force` | `--force` 只过时间门，不写不发 |
| 8 物流回写飞书 | 同上 | 不写表 | `--write-feishu`。无匹配行不新建 | 不发第 9 步 |
| 9 物流私信 | 同上 | 不发 | `--write-feishu --send-tracking --execute --yes` | 不回头筛人/批准 |

## 常用命令（6 条）

把 `<店铺编号>` 换成本次店铺。复制逐步操作见 [使用方法.md](./使用方法.md)。

```bash
# 1 开店（已开则不重开）
python3 scripts/open_sample_store.py --store-id <店铺编号>

# 2 正式只读筛查（先登录并停在商家中心首页）
python3 scripts/screen_sample_requests.py \
  --store-id <店铺编号> --from-seller-home --data-source auto \
  --with-detail --require-detail

# 3 按已有导出批准 1 条（写飞书再加 --write-feishu）
python3 scripts/screen_sample_requests.py \
  --store-id <店铺编号> \
  --from-export exports/sample_screen_<时间戳>.json \
  --execute --yes --execute-limit 1

# 4 补写：约 10 分钟后核对「待发货」并补飞书（不重批）
python3 scripts/screen_sample_requests.py \
  --store-id <店铺编号> \
  --from-export exports/sample_screen_<时间戳>.json \
  --confirm-export --write-feishu

# 5 第 6 步介绍私信（指定达人；确认话术后再加 --execute --yes --execute-limit 1）
python3 scripts/send_sample_intro.py \
  --store-id <店铺编号> --creator-id <达人编号> --creator-name <达人名>

# 6 北京时间 16:00 后回写物流到飞书（先去掉 --write-feishu 做预演）
python3 scripts/sync_shipped_tracking.py \
  --store-id <店铺编号> --creator-name <达人名> --write-feishu
```

第 9 步不在这 6 条里：须在第 8 步成功后再加 `--send-tracking --execute --yes`。

## 参数

| 参数 | 含义 |
|---|---|
| `--store-id` | 本次店铺。多店必须显式传入 |
| `--from-seller-home` | 允许从商家中心首页进待审核；失败不切店、不重开 |
| `--with-detail --require-detail` | 正式筛查必带；缺详情指标不能通过 |
| `--from-export` | 用已有筛查 JSON 批准、补写或发介绍；不再扫表 |
| `--confirm-export` | **补写**：只确认已批行是否进「待发货」，可补飞书；不重新批准 |
| `--execute --yes` | 唯一批准 / 真发私信门闩；缺一不写 |
| `--execute-limit` | 写操作上限，默认 1 |
| `--write-feishu` | 允许写「达人关系管理(新)」。批准后新建；物流只回写已有行 |
| `--creator-id` / `--creator-name` | 只处理指定达人。指定已批达人发第 6 步时用这对，不要用 `--from-export --max-rows N` 代替 |
| `--force` | 仅绕过物流脚本的北京时间 16:00 检查 |
| `--send-tracking` | 第 8 步有单号后才允许发第 9 步；须同时 `--write-feishu --execute --yes` |

## 边界

- 没有 `--execute --yes` 绝不批准、不发私信。永不点「拒绝」「邀请」。
- 批准以写接口接受为准，不等「待发货」。约 10 分钟后用命令 4 **补写**，不要再 `--execute`。
- 物流脚本看启动时的北京时间：`< 16:00` 退出。16:00 后直接跑命令 6；`--force` 只给测试过时间门。
- 发给达人的是 **TikTok 物流单号**，不是订单 ID。飞书已有不同快递单号默认不覆盖。
- 空简介介绍私信默认英语；已有西语会话也不会改判。预演先看话术。
