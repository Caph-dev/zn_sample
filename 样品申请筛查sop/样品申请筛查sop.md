# 样品申请筛查sop

# 第一步：进入联盟中心

![image\.png](图片和附件/image%204.png)

# 第二步：进入【样品申请管理】



![image\.png](图片和附件/image.png)

# 第三步：点击【待审核】

![image\.png](图片和附件/image%205.png)

# 项目自动化补充：进入页面和两轮筛查

业务员先运行脚本 1 打开目标店铺，完成 TikTok Shop 登录并停在商家中心首页；再运行脚本 2，脚本可自动进入 **联盟中心 → 样品申请管理 → 待审核**。

正式筛查采用两轮判定：先读取待审核列表做初筛，再对初筛有机会通过的达人读取详情做复筛。正式命令必须带 `--with-detail --require-detail`；申请样品必须是主推款。普通运行只导出名单，不会自动批准、写飞书或发私信。

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

## 找到都满足条件的达人后进入第五步

# 第五步：记录达人信息，点击同意样品申请，并同步到表格[内部达人建联记录表](https://rsed6zggjt.feishu.cn/base/CJXSbLIQWahB8esVOiscX7j1nLc?from=from_copylink)

## 需要记录信息：

1\.达人名

2\.产品货号[tk产品图\+货号](https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc?from=from_copylink)

3\.履约率

7\.粉丝数

## 项目自动化补充

- 自动化默认只导出 xlsx / csv / json，禁止点击「同意」；仅显式 `--execute --yes` 才可批准，默认 `--execute-limit 1`。
- 写入飞书须再显式添加 `--write-feishu`；先批准成功，再新增「达人关系管理(新)」记录。
- 当前默认批准调用已捕获的单条批准 API；页面 DOM 仍可通过显式 `--write-source dom` 使用。API 路径尚未用第二条真实申请重复执行验收，状态不确定时不自动重试或回退 DOM。
- 批准 API 只允许已登记的 `/api/v1/affiliate/sample/group/action`，请求为单条 `apply_ids`、`type=1`、`status_type=11`；不自动重试，也不在不确定时回退 DOM。
- 批准后以只读列表 API 确认申请进入「待发货」且 `curr_status=20`；状态确认成功后才允许写飞书。
- 新建飞书记录时，「人员」固定写为 **王良希（技术）**，「合作状态」写为「待发货」，「是否已寄样」不勾选。
- “申请样品必须是主推款（是否主推=是）”是正式硬性条件；0814 飞书导出原文遗漏，本项目已补入 SOP。
- 当前导出仍保留成交件数、视频/直播 GPM、视频达人/直播达人等诊断字段，不影响飞书必填字段。

# 第六步：打开达人私信界面，发送话术

英文

Hi  , thanks for requesting our lingerie sample! We’re excited to work with you.

To better coordinate the shipping and discuss the creative direction for the content, could you please share your WhatsApp or Gmail?

Once the tracking number is available, I will send it to you right away.

西语

Hola , ¡gracias por solicitar nuestra muestra de lencería!

Estamos muy emocionados de trabajar contigo. Para coordinar mejor el envío y hablar sobre la dirección creativa del contenido, ¿podrías compartirme tu WhatsApp o Gmail?

Además, querida, ¿estarías abierta a hacer transmisiones en vivo de nuestros productos? En cuanto tenga el número de seguimiento, te lo envío inmediatamente.

![image\.png](图片和附件/image%207.png)

## 第六步自动化补充

`send_sample_intro.py` 独立运行：默认只读预演，确认后才使用 `--execute --yes` 发送；语言只从达人详情简介判断，支持英语和西班牙语，空简介默认英语。真实发送默认调用页面内 IM SDK，不点「发送」按钮。批准命令结束后不会自动发送介绍私信。

# 第七步：北京时间下午四点后点击【样品申请】--【已发货】--【查看物流】

![image\.png](图片和附件/image%203.png)

## 第七步自动化补充

`sync_shipped_tracking.py` 在北京时间 16:00 前默认拒绝运行，只有测试时才加 `--force`。第七步只负责读取已发货记录和 TikTok 物流单号，默认走样品列表 API 和商家物流 GET；不会因为读取成功就自动写飞书或发私信。

# 第八步：复制订单号、物流单号到【内部达人建联表】，填进下图2中红标中（要打勾）

![image\.png](图片和附件/image%202.png)

![image\.png](图片和附件/image%201.png)

## 项目自动化补充

- 飞书写入订单号和商家订单页的「TikTok 物流」单号后，自动勾选「是否已寄样」。
- 只有本次已写入物流单号（或确认飞书已有同一单号）时，才将「合作状态」从「待发货」改为「待发布」；不得把「已发布」等后续状态回退。
- 飞书已有不同物流单号时默认不覆盖，也不推进合作状态；仅显式 `--overwrite` 可覆盖。

# 第九步：复制对应达人的物流单号打开私信发送给达人

发给达人的是商家订单页 **「TikTok 物流」单号**（如 `UUS68…` / `9200…` / `1LSD…`），**不是**「订单 ID」。下图示例误把订单号贴进了话术，以本文为准。

话术后接物流单号

英语：Dear, here is the tracking number:

西语：Estimada, aquí tienes el número seguimiento:

示例图如下：

![image\.png](图片和附件/image%206.png)

## 第八、九步自动化补充

第七步、第八步、第九步由同一个独立脚本按显式参数控制，不会在批准后自动触发：

1. `--write-feishu` 才允许回写订单号、TikTok 物流单号和寄样状态，并在满足条件时将「待发货」推进到「待发布」；
2. `--send-tracking --execute --yes` 才允许发送第九步物流私信，且必须先有飞书写入成功或已确认飞书已有同一物流单号；默认走 IM SDK；
3. 第九步发送的是商家订单页的 **TikTok 物流** 单号，不是 `main_order_id` / 订单 ID；
4. 飞书已有不同物流单号时默认不覆盖、不推进状态，只有显式 `--overwrite` 才可覆盖。
