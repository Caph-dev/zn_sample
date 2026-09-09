# 样品申请筛查sop

# 第一步：进入联盟中心

![image\.png](图片和附件/image%204.png)

# 第二步：进入【样品申请管理】



![image\.png](图片和附件/image.png)

# 第三步：点击【待审核】

![image\.png](图片和附件/image%205.png)

# 项目自动化补充：进入页面、销售筛查与内容审查

业务员先运行 0 号入口打开目标店铺，完成 TikTok Shop 登录（商家中心任意页即可，不必停在首页）；再运行 1 号只出名单入口，脚本可自动进入 **联盟中心 → 样品申请管理 → 待审核**。0/1/2/3 启动器编号不随 SOP 步骤变化。

正式筛查先完成第 4 步销售数据判定：读取待审核列表做初筛，再对初筛有机会通过的达人读取详情做复筛；通过后再执行第 5 步内容审查。正式筛查必须带 `--with-detail --require-detail`；申请样品必须是主推款。普通运行只导出名单，不会自动批准、写飞书或发私信。可复制命令见 README。

# 第四步：逐一点开达人数据主页

# **筛选条件：**

## 1\.粉丝数（达人名字下面）：＞2000

## 2\.GMV：＞1k5

## 3\.成交件数：＞80

## 4\.千次曝光成交金额：＞10

## 5\.客单价：10\-25＄

## 6\.【按商品类目查看GMV（饼状图）】需要包含类目：

- Beauty \& Personal Care

- Womenswear \& Underwear

- Household Appliances

- Fashion Accessories

- Shoes

- Sports \& Outdoor

- **Home Textiles** 

## 7\.预计发布率：＞80%

## 8\.粉丝性别占比：女性＞60%

# 视频或者直播数据其中一个满足条件即可：

## 9\.视频GPM：＞10＄                      9\.直播GPM：＞12＄ 

## 10\.平均视频播放量：＞300            10\.平均直播播放量：＞1000

## 11\.视频平均互动率：＞2%

# **筛选条件 2：**

## 申请的样品必须是 [tk产品图\+货号](https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc?from=from_copylink) 里的“主推款”（是否主推=是）

当前批准默认 **只过** 商品 ID `1732414717062320994`（货号 B005 且是否主推=是）。其它主推款筛为不通过。恢复全部主推：`--all-hero-products`。

## 找到都满足条件的达人后进入第五步

# 第五步：审查 TikTok 最近 7 天带货视频

第 4 步销售数据通过后，检查审查时点向前滚动 **7×24 小时**内的 TikTok 视频，同时满足：

1. 至少 **4 条相关带货视频**。相关类目沿用第 4 步既有 7 类：Beauty & Personal Care、Womenswear & Underwear、Household Appliances、Fashion Accessories、Shoes、Sports & Outdoor、Home Textiles。
2. 上述相关带货视频中至少 **1 条**明确展示：**产品穿在身上**，或**同一画面内露脸并手持产品**。

不强制 ASR / 语音转写，不强制口播，也没有每日发布条数配额。

## 第五步自动化补充

- 未知、缺失或部分采集且证据不足时标记 `needs_review`，不得标记为 `eligible`；完整计数少于 4 条标记 `failed`。解析不了的购物锚点不计入那 4 条，也不单独否决；已确认至少 4 条相关带货且有展示证据即可通过。未知只在相关还不足 4 条时把人标成待复核。
- 已确认至少 4 条相关带货视频，并已找到其中的展示证据时，可提前结束采集并通过；此时记录的是已知数量下界，不是全量总数。
- 第 4、5 步均通过后才进入第 6 步。旧导出没有内容通过证据不能用于新批准；历史 `--confirm-export` 核对和补写流程不变，不重新批准。
- 使用本地 `tiktok_creator_videos.py` 和 `creator_video_review.py`，不依赖独立 `video-analysis-api` 项目。外部环境配置仅接受显式白名单，保留已有 LLM 配置，不批量导入外部环境文件。
- 本次实现验证不批准、不写飞书、不发送私信，不代表已完成实店全链路验收。

# 第六步：记录达人信息，点击同意样品申请，并同步到表格[内部达人建联记录表](https://rsed6zggjt.feishu.cn/base/CJXSbLIQWahB8esVOiscX7j1nLc?from=from_copylink)

## 需要记录信息：

1\.达人名

2\.产品货号[tk产品图\+货号](https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc?from=from_copylink)

3\.履约率

7\.粉丝数

## 项目自动化补充

- 自动化默认只导出 CSV / JSON，禁止点击「同意」；仅显式 `--execute --yes` 才可批准，默认 `--execute-limit 1`。
- 写入飞书须再显式添加 `--write-feishu`；先批准成功，再新增「达人关系管理(新)」记录。
- 当前默认批准调用已捕获的单条批准 API；页面 DOM 仍可通过显式 `--write-source dom` 使用。API 路径尚未用第二条真实申请重复执行验收，状态不确定时不自动重试或回退 DOM。
- 批准 API 只允许已登记的 `/api/v1/affiliate/sample/group/action`，请求为单条 `apply_ids`、`type=1`、`status_type=11`；不自动重试，也不在不确定时回退 DOM。
- 批准写接口 `success_count=1` 即算批准成功，可随后写飞书，不等待「待发货」刷新；约 10 分钟后用 `--confirm-export` 只读核对申请进入「待发货」且 `curr_status=20`，并按原流程补写飞书、回填订单号，不重新批准。
- 新建飞书记录时，「人员」固定写为 **王良希（技术）**，「合作状态」写为「待发货」，「是否已寄样」不勾选。
- “申请样品必须是主推款（是否主推=是）”是正式硬性条件；0814 飞书导出原文遗漏，本项目已补入 SOP。
- 当前导出仍保留成交件数、视频/直播 GPM、视频达人/直播达人等诊断字段，不影响飞书必填字段。

# 第七步：打开达人私信界面，发送话术

英文

Hi  , thanks for requesting our lingerie sample! We’re excited to work with you.

To better coordinate the shipping and discuss the creative direction for the content, could you please share your WhatsApp or Gmail?

Once the tracking number is available, I will send it to you right away.

西语

Hola , ¡gracias por solicitar nuestra muestra de lencería!

Estamos muy emocionados de trabajar contigo. Para coordinar mejor el envío y hablar sobre la dirección creativa del contenido, ¿podrías compartirme tu WhatsApp o Gmail?

Además, querida, ¿estarías abierta a hacer transmisiones en vivo de nuestros productos? En cuanto tenga el número de seguimiento, te lo envío inmediatamente.

![image\.png](图片和附件/image%207.png)

## 第七步自动化补充

`send_sample_intro.py` 独立运行：默认只读预演，确认后才使用 `--execute --yes` 发送。指定达人用 `--creator-id` / `--creator-name`；语言只从达人详情简介判断，支持英语和西班牙语，空简介默认英语，已有会话语言不会改判。打开会话严格走样品申请页右下角「聊天数」→「发送消息」→在「发送给」输入达人 ID→点击结果行右侧「聊天」，不打开详情消息弹层、不进入单独的达人消息整页，也不要点「邀请」或最近联系人。真实发送默认调用页面内 IM SDK，不点「发送」按钮。批准命令结束后不会自动发送介绍私信。

# 第八步：北京时间下午四点后点击【样品申请】--【已发货】--【查看物流】

![image\.png](图片和附件/image%203.png)

## 第八步自动化补充

`sync_shipped_tracking.py` 在北京时间 16:00 前默认拒绝运行，只有测试时才加 `--force`。第八步只负责读取已发货记录和 TikTok 物流单号，默认走样品列表 API 和商家物流 GET；不会因为读取成功就自动写飞书或发私信。

# 第九步：复制订单号、物流单号到【内部达人建联表】，填进下图2中红标中（要打勾）

![image\.png](图片和附件/image%202.png)

![image\.png](图片和附件/image%201.png)

## 项目自动化补充

- 飞书写入订单号和商家订单页的「TikTok 物流」单号后，自动勾选「是否已寄样」。
- 只有本次已写入物流单号（或确认飞书已有同一单号）时，才将「合作状态」从「待发货」改为「待发布」；不得把「已发布」等后续状态回退。
- 飞书已有不同物流单号时默认不覆盖，也不推进合作状态；仅显式 `--overwrite` 可覆盖。

# 第十步：复制对应达人的物流单号打开私信发送给达人

发给达人的是商家订单页 **「TikTok 物流」单号**（如 `UUS68…` / `CBT, 9200…` / `USPS, 9200…` / `1LSD…`），**不是**「订单 ID」。有承运商时写成 `{承运商}, {单号}`，承运商以页面或接口原文为准，不按单号猜测。下图示例误把订单号贴进了话术，以本文为准。

话术后接物流单号展示格式

英语：Dear, here is the tracking number:CBT, 9200…

西语：Estimada, aquí tienes el número seguimiento:CBT, 9200…

示例图如下：

![image\.png](图片和附件/image%206.png)

## 第九、十步自动化补充

第八步、第九步、第十步由同一个独立脚本按显式参数控制，不会在批准后自动触发：

1. `--write-feishu` 才允许回写订单号、TikTok 物流单号和寄样状态，并在满足条件时将「待发货」推进到「待发布」；
2. `--send-tracking --execute --yes` 才允许发送第十步物流私信，且必须先有飞书写入成功或已确认飞书已有同一物流单号；默认走 IM SDK；
3. 第十步发送的是商家订单页的 **TikTok 物流** 单号，不是 `main_order_id` / 订单 ID；
4. 飞书已有不同物流单号时默认不覆盖、不推进状态，只有显式 `--overwrite` 才可覆盖。
