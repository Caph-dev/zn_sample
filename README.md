# zn_sample · TikTok Shop 样品申请筛查

按 SOP 筛「联盟中心 → 样品申请管理 → 待审核」，导出名单，批准、写飞书、发介绍/物流私信。

维护纪律见 [AGENTS.md](./AGENTS.md)，规则原文见 [样品申请筛查 SOP](./样品申请筛查sop/样品申请筛查sop.md)。新电脑配 Python / Node / `ziniao-cli` 见 [快速开始.md](./快速开始.md)。

不点「拒绝」「邀请」。

---

## 每次这样跑

先确认：

1. 紫鸟能看见工作台窗口，本人已登录
2. 工作台**只开着一家店**（其它店关掉）
3. 店铺窗口已登录 TikTok Shop，停在**商家中心首页**

在本仓库文件夹里==**双击**==：

| 系统 | a 只出名单 | b 筛查批准写飞书发私信 | c 获取物流信息写飞书发单号 |
|------|------------|------------------------|----------------------------|
| macOS | `1-只出名单.command` | `2-筛查批准写飞书发私信.command` | `3-获取物流信息写飞书发单号.command` |
| Windows | `1-只出名单.bat` | `2-筛查批准写飞书发私信.bat` | `3-获取物流信息写飞书发单号.bat` |

会弹出一个窗口，**不要关**，也不要自己点店铺页面。

| 双击 | 会做什么 | 会不会改店铺 / 飞书 |
|------|----------|---------------------|
| 1 只出名单 | 从首页进待审核，筛人，出名单 | 不会批准、不会发私信 |
| 2 筛查批准写飞书发私信 | 出名单 → 批准并写飞书 → **空等 10 分钟** → 核对待发货并补写 → 发介绍 | 输入 `y` 才继续；本轮最多 20 条；**同意和私信无法撤销** |
| 3 获取物流信息写飞书发单号 | 读已发货，把 TikTok 物流单号写回飞书，并私信发给达人 | 须 **北京时间 16:00 之后**。输入 `y` 就会写飞书并发送单号 |

`2-筛查批准写飞书发私信` 中间那 10 分钟是在等 **TikTok 商家中心** 把「同意」刷成「待发货」。这是平台侧刷新慢，不是脚本卡住。窗口里会有进度条。请不要关窗口。

输入 `n` 或直接回车都会退出，什么也不改。

macOS 双击提示没有权限：右键这个文件 → 打开；或让技术人员执行：

```bash
chmod +x \
  "1-只出名单.command" "2-筛查批准写飞书发私信.command" \
  "3-获取物流信息写飞书发单号.command" \
  scripts/run_launcher.sh
```

店还没开：在紫鸟工作台打开那一家，登录后停在商家中心首页，再双击「1-只出名单」。不要和别人同时操作同一家店。

---

## 检查环境

新电脑或双击报「找不到 Python / ziniao-cli」：先按 [快速开始.md](./快速开始.md) 装官网包，不要手改系统 PATH。

macOS：

```bash
cd ~/Projects/zn_sample
python3 --version
node -v
zsh scripts/ziniao-status.sh
ziniao-cli doctor
```

`ziniao-status.sh` 须打印 `mode: GUI`。连不上 Bridge：先保证紫鸟 GUI 已打开。

Windows（**cmd**，不要 Git Bash）：

```bat
py -3 --version
where py
node -v
where node
where ziniao-cli
ziniao-cli --version
ziniao-cli doctor
```

不要跑 `scripts\ziniao-status.sh`（依赖 zsh / pgrep / lsof）。Windows 预检只看工作台是否只开一家店。双击 `.bat` 时会把 `%APPDATA%\npm` 临时加进 PATH，以便找到 `ziniao-cli.cmd`。

---

## 出问题

| 看到什么 | 怎么做 |
|---|---|
| 找不到 Python 3 / 版本太旧 | 按《快速开始》装官网 Python。Windows 新开 **cmd** 跑 `py -3 --version`。不要用微软商店 Python |
| 还没配完 / 找不到 ziniao-cli | 按《快速开始》装官网 Node 和 `@ziniao-open/cli`。新开终端跑 `ziniao-cli --version`。不要用 nvm，不要手改 PATH |
| `python` 弹出 Microsoft Store | 占位符，不是真 Python。装官网包，验收只认 `py -3` |
| 还在登录页 / 空白页 | 自己登录，停在商家中心首页，再双击「1-只出名单」 |
| `not-on-sample-page` / `no-pending-tab` | 回到「样品申请 → 待审核」，或从首页重跑「1-只出名单」 |
| 扫到 0 行 | 确认是「待审核」tab，等列表加载完再跑 |
| `Bridge` 失败 | 再跑 `ziniao-cli doctor` |
| `runtime.reopen` | 停手，找技术人员 |
| `无法解析 storeId` / 多家店 | 只留一家店开着 |
| 飞书主推表读失败 | 找管理员查 `config.toml` |
| 进度条走到一半 | 继续等，不要关窗口。这是商家中心 10 分钟刷新，不是死机 |
| 找不到私信会话 | 不要点「邀请」 |
| 不到下午 4 点 | 默认回车退出，等到 4 点再双击。只有确定要闯时间门时才输入 `FORCE`（不推荐） |
| 没有 TikTok 物流单号 | 还没出单，再等 |
| 飞书没有匹配行 | 核对达人名和寄样产品 |

---

## 技术人员：终端等价命令

四个脚本仍然断开。`--from-export` 不写路径 = `exports/` 里最新一份筛查 json。未传 `--store-id` 且只开着一家店时，从 running 识别。终端默认只打进度；要看页面 API 明细再加 `--verbose`。筛查导出是 CSV + JSON，不再自动写 xlsx。

```bash
# 开店（店已开则不重开）
python3 scripts/open_sample_store.py

# a 只出名单
python3 scripts/screen_sample_requests.py \
  --from-seller-home --data-source auto \
  --with-detail --require-detail
# 要看每条页面 API：再加 --verbose

# b 对应「2-筛查批准写飞书发私信」里的后半段（批准 / 补写 / 介绍）
python3 scripts/screen_sample_requests.py \
  --from-export \
  --execute --yes --write-feishu --execute-limit 20
# 批准后必须等满 10 分钟（商家中心「待发货」刷新），再：
python3 scripts/screen_sample_requests.py \
  --from-export \
  --confirm-export --write-feishu --execute-limit 20
python3 scripts/send_sample_intro.py \
  --from-export \
  --execute --yes --write-feishu --execute-limit 20

# c 获取物流、写飞书、发单号（北京时间 16:00 之后）
python3 scripts/sync_shipped_tracking.py \
  --write-feishu --send-tracking --execute --yes --execute-limit 0
```

Windows（PowerShell）：`python3` 换成 `py -3`，`scripts/` 换成 `scripts\`。

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
| `--execute-limit 20` | 批准 / 介绍私信本轮最多 20 条 |
| `--execute-limit 0` | 物流私信不限条数（命令 3 默认） |
