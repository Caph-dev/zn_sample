# zn_sample · TikTok Shop 样品申请筛查

按 SOP 筛「联盟中心 → 样品申请管理 → 待审核」，导出名单，批准、写飞书、发介绍/物流私信。

维护纪律见 [AGENTS.md](./AGENTS.md)，规则原文见 [样品申请筛查 SOP](./样品申请筛查sop/样品申请筛查sop.md)。

不点「拒绝」「邀请」。

### 检查 ziniao-cli 状态：

```bash
cd ~/Projects/zn_sample
ziniao-status
ziniao-cli doctor
```

### 最常用的命令！！！！！！！！！！！！！！！！！！！！

紫鸟只开着一家店时，下面命令直接复制。先登录并停在 **商家中心首页**，再跑命令 2。物流（6 / 7）须 **北京时间 16:00 之后**。`--from-export` 不写路径 = `exports/` 里最新一份筛查 json。

目前限制：每次最多批准 20 个请求（对应下面的`--execute-limit 20`）


| SOP | 命令 |
|---|---|
| 开店 | 1。店已开则不重开 |
| 1–4 进待审核、筛人、出名单 | 2 |
| 5 同意并写达人关系 | 3 |
| 5 补写飞书（约 10 分钟后，不重批） | 4 |
| 6 介绍私信 | 5 |
| 7–8 读已发货 / 回写物流到飞书 | 6 |
| 9 物流私信 | 7 |

```bash
# 命令 1： 开店
python3 scripts/open_sample_store.py

# 命令 2： 正式筛查（先登录并停在商家中心首页）
python3 scripts/screen_sample_requests.py \
  --from-seller-home --data-source auto \
  --with-detail --require-detail

# 命令 3： 批准并通过飞书建档
python3 scripts/screen_sample_requests.py \
  --from-export \
  --execute --yes --write-feishu --execute-limit 20

# 上面的命令 3 成功后等约 10 分钟再跑 4。
# 命令 4： 核对「待发货」并写飞书
python3 scripts/screen_sample_requests.py \
  --from-export \
  --confirm-export --write-feishu --execute-limit 20

# 命令 5： 发送介绍私信
python3 scripts/send_sample_intro.py \
  --from-export \
  --execute --yes --execute-limit 20

# 命令 6： 回写物流到飞书
python3 scripts/sync_shipped_tracking.py \
  --write-feishu --execute-limit 20

# 命令 7： 回写物流并发送物流私信
python3 scripts/sync_shipped_tracking.py \
  --write-feishu --send-tracking --execute --yes --execute-limit 20
```



## 参数

| 参数 | 含义 |
|---|---|
| `--store-id` | 指定店铺。只开着一家时可省略 |
| `--from-seller-home` | 从商家中心首页进入待审核 |
| `--with-detail --require-detail` | 正式筛查必带 |
| `--from-export` | 用筛查 JSON。不写路径则用最新一份 |
| `--confirm-export` | 核对「待发货」并补写飞书，不重新批准 |
| `--execute --yes` | 批准或发送私信 |
| `--write-feishu` | 写「达人关系管理(新)」 |
| `--creator-id` / `--creator-name` | 只处理指定达人 |
| `--force` | 测试时绕过北京时间 16:00 |
| `--send-tracking` | 发送物流私信 |
| `--execute-limit 20` | 本轮最多处理 20 条 |

## 出问题

| 看到什么 | 怎么做 |
|---|---|
| 还在登录页 | 自己登录，停在商家中心首页，再跑命令 2 |
| `not-on-sample-page` / `no-pending-tab` | 回到「样品申请 → 待审核」，或从首页重跑命令 2 |
| 扫到 0 行 | 确认是「待审核」tab，等列表加载完再跑 |
| `Bridge` 失败 | 再跑 `ziniao-cli doctor` |
| `runtime.reopen` | 停手，找技术人员 |
| `无法解析 storeId` | 只留一家店开着，或补 `--store-id` |
| 飞书主推表读失败 | 找管理员查 `config.toml` |
| 批准后飞书没写上 | 等约 10 分钟跑命令 4 |
| 找不到私信会话 | 不要点「邀请」 |
| 不到下午 4 点 | 等到 4 点再跑命令 6 / 7 |
| 没有 TikTok 物流单号 | 还没出单，再等 |
| 飞书没有匹配行 | 核对达人名和寄样产品 |
