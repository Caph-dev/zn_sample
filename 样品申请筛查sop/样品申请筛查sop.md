# 样品申请筛查sop

# 第一步：进入联盟中心

![image\.png](图片和附件/image%202.png)

# 第二步：进入【样品申请管理】



![image\.png](图片和附件/image%201.png)

# 第三步：点击【待审核】

![image\.png](图片和附件/image.png)

# 第四步：逐一点开达人数据主页

# **筛选条件 1：**

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

## 11\.视频平均互动率：＞2%    11.直播平均互动率：无要求         

# ** 筛选条件2：**

## 申请的样本必须是 [tk产品图\+货号](https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc?from=from_copylink) 里的“主推款”（是否主推=是）

## 都满足条件的达人整理成名单

- **默认**：只导出名单，**禁止**脚本执行「同意」。
- **可选危险路径**（须显式参数，见 `AGENTS.md` / CLI）：`--execute --yes` 可对通过筛查的申请点「同意」；测试环境默认 **不写飞书**，写飞书须再加 `--write-feishu`。
- 默认 `--execute-limit 1`；批准成功后再写「达人关系管理(新)」；去重键为红人ID+寄样产品。

xlsx 格式 + csv 格式（同时）

名单需要包含信息：

1\.达人名(网页中可能有多个类似的字段，如果你不确定，可以问我)

2\.产品货号 [tk产品图\+货号](https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc?from=from_copylink) （从这个飞书表格里对比获取）

3\.履约率 （网页内如果没有「履约率」，那么应该是「合作指标」-「预计发布率」）

4\.成交件数（「销量」-「成交件数」）

5\.GPM（「视频数据」-「视频 GPM」，「直播数据」-「直播 GPM」，两个 GPM 都需要记录）

6\.视频达人/直播达人（应该是选择）

7\.粉丝数







