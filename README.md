# zn_sample · 样品申请筛查

按 `样品申请筛查sop/` 做 **待审核达人筛查**，导出 xlsx + csv。  
Agent / 完整约定见 **[AGENTS.md](./AGENTS.md)**。  
紫鸟浏览器启停见 **[../zn_daren/AGENTS.md](../zn_daren/AGENTS.md)**。

## 硬性纪律

1. **默认禁止**点「同意 / 批准 / 拒绝」——只读 + 导出。危险路径须 `--execute --yes`（默认 limit=1）；写飞书另加 `--write-feishu`。  
2. **导航由你完成**：先把店铺页停在 **样品申请 → 待审核**。  
3. **测试默认 1 号店**：`跨境1号店（Lingerie Outlet）` / `27437742526069`。  
4. **主推表仅飞书**（不再使用本地 `tk产品图+货号.xlsx`）。  
5. **飞书达人表默认「达人关系管理(新)」→「达人管理总表」**（`tblWT2SRKJ3CEZ5e`；与主推 wiki 表不是同一张；勿默认写旧表「达人关系管理」）。  
6. **密钥放 `config.toml`**（已 gitignore；见 `config.toml.example`）。

---

## 怎么用（3 步）

**只读导出，不会点同意/拒绝。** 页必须先停在 **样品申请 → 待审核**。

### 1. 首次配置（做一次即可）

```bash
cd ~/Projects/zn_sample
cp -n config.toml.example config.toml
# 编辑 config.toml，填 [feishu] app_secret = "..."
```

也可用环境变量：`export FEISHU_APP_SECRET='...'`（勿提交 git）。

### 2. 打开页面

```bash
ziniao-status          # 建议 GUI
ziniao-cli doctor
# 紫鸟已登录 → 打开 1 号店 → 手动进入：联盟中心 → 样品申请 →【待审核】
```

店未开时：

```bash
ziniao-cli zclaw invoke open_store --args '{"storeId":"27437742526069"}'
```

### 3. 跑筛查

```bash
cd ~/Projects/zn_sample
python3 scripts/screen_sample_requests.py --with-detail --require-detail
```

结果在 `exports/`（csv / xlsx / json）。默认 1 号店；正式筛查必须带这两个参数 + 飞书密钥。

---

## 常用可选

在上面那条主命令后面加即可：

| 需求 | 追加参数 |
|------|----------|
| 只导出通过名单 | `--eligible-only` |
| 指定导出文件名前缀 | `--out exports/my_run` |
| 全表都拉详情（慢，含非主推） | `--detail-all` |
| 冒烟测试（限扫表行数） | `--max-rows 6`（**禁止** `--detail-limit`） |
| 指定店铺 | `--store-id <storeId>` |

示例：

```bash
# 只导出通过
python3 scripts/screen_sample_requests.py --with-detail --require-detail --eligible-only

# 快速冒烟（只限扫表行数；详情不截断）
python3 scripts/screen_sample_requests.py --with-detail --require-detail --max-rows 6
```

**不要**用：省略 `--with-detail` / `--require-detail`、`--skip-hero-check`，或 **`--detail-limit`**（已移除，禁止使用）。  

### 可选：批准（危险）

```bash
# 试批 1 条（默认 limit=1；不写飞书）
python3 scripts/screen_sample_requests.py --with-detail --require-detail --execute --yes

# 批 + 写「达人关系管理(新)」（须 config [feishu.bitable]）
python3 scripts/screen_sample_requests.py --with-detail --require-detail \
  --execute --yes --write-feishu --execute-limit 1
```

完整参数见 [AGENTS.md](./AGENTS.md#样品申请筛查screen_sample_requestspy)。

### 可选：第 6–9 步（介绍私信 / 物流）

默认都不发送、不写表。语言只看达人详情简介（英/西）。第 9 步发 **TikTok 物流单号**，不是订单 ID。回写运单后合作状态改为「待发布」。

```bash
# 第 6 步：只读预演
python3 scripts/send_sample_intro.py --from-export exports/sample_screen_<ts>.json --max-rows 1

# 第 7–9 步：只读预演（16:00 前须 --force）
python3 scripts/sync_shipped_tracking.py --force --max-rows 1

# 回写飞书（不发私信）
python3 scripts/sync_shipped_tracking.py --write-feishu --force --max-rows 1
```

真发私信须 `--execute --yes`，细则见 [AGENTS.md · 第 6–9 步](./AGENTS.md#sop-第-69-步介绍私信--物流回写)。

---

## 脚本在干什么（最短）

```text
飞书主推表 → 待审核扫表 → 列表初判 →（可选）进达人详情只读 → 带详情重判 → 导出
可选：--execute --yes → 备份 → 同意（limit 默认 1）→（可选 --write-feishu）写达人关系管理(新)
```

**默认不点同意/拒绝。** 导航须你先停在「待审核」。

### 非主推还会进详情吗？

- 日常主命令：**一般不会**（列表初判因非主推失败，不进详情）。  
- 加了 `--detail-all`：**会**（最终仍因非主推不通过；只为补全 GPM）。

### 视频达人 / 直播达人

- 两列 **可同时为「是」**（非互斥）。  
- SOP：视频侧 **或** 直播侧达标即可过条件 9–11。  
- 导出「是」略宽于 SOP 达标（有 GPM 也可能标「是」）；**以「是否通过」列为准**。  

细则与阈值见 [AGENTS.md · 筛查规则](./AGENTS.md#筛查规则与-sop-对齐)。

---

## 筛查规则摘要

| SOP | 来源 |
|-----|------|
| 粉丝/GMV/成交件数/履约/女性/类目 | 待审核列表 React record |
| 千次曝光 / 客单价 | 详情优先；列表可作辅助 |
| 视频/直播 GPM、均播、互动率 | 达人详情（`--with-detail`，正式必开） |
| 主推款 | 飞书表「是否主推=是」的产品货号 + 商品ID |

**飞书两类资源（勿混用）：**

| 用途 | 默认 | 链接 / ID |
|------|------|-----------|
| 达人建联 / 达人关系（**项目默认**） | 表 **达人关系管理(新)** → 视图 **达人管理总表** | Base `CJXSbLIQWahB8esVOiscX7j1nLc` → 表 **`tblWT2SRKJ3CEZ5e`** → 视图 `vewNtqmTk4` |
| 主推款（SOP 条件 2） | wiki 电子表格 | `https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc` · 实现 `scripts/lib/feishu_hero.py` |

同 Base 里的「样品申请」等表**不是**默认；链接带了别的 `table=` 时不要当成项目默认。

---

## 目录

```text
config.toml.example                 # 密钥模板（含 [feishu] / [feishu.bitable]）
config.toml                         # 本地密钥（gitignore）
scripts/screen_sample_requests.py   # CLI
scripts/lib/feishu_hero.py          # 飞书主推表只读
scripts/lib/feishu_bitable.py       # 达人关系管理(新) 可选写入
scripts/lib/approve_dom.py          # 列表点「同意」（仅 execute）
scripts/lib/app_config.py           # 读 config.toml
scripts/lib/                        # DOM / 判定 / 导出
exports/                            # 输出（含可选 *_pre_execute 备份）
样品申请筛查sop/                    # SOP 原文
AGENTS.md                           # Agent 完整约定
```

---

## 故障速查（最短）

| 现象 | 处理 |
|------|------|
| not-on-sample-page | 手动回到「待审核」 |
| Bridge 失败 | `ziniao-cli doctor`；紫鸟 GUI 启动 |
| 无窗口 | `ziniao-gui`（见 zn_daren） |
| 飞书主推表失败 | 检查 `config.toml` / `FEISHU_APP_SECRET`、应用是否仍可阅读该表 |
| 主推全不匹配 | 货号 vs 标题；见 AGENTS.md |

更多见 [AGENTS.md · 故障速查](./AGENTS.md#故障速查)。
