# 年份与分类研究索引

本表按当前语料生成。分区为本库检索分类，不等同于B站历年官方分区。

单元格只索引 `origin.date` 为该年、`era` 包含该年且登记 `full/date` 命题的记录；`full` 标记不等于所有命题均已独立确认。无命中不表示该年不存在来源或使用，`date=null` 的传播节点不计入。这里不是诞生或B站首发清单，也不计算覆盖率。

`date_supported_entry_ids` 是输出字段名，只表示登记为 `full/date` 且通过日期/时期索引条件；不表示词条的其他命题均已确认。

## 逐年入口

| 年 | 有登记日期命题的分类格 / 11 | 研究锚点（至多4项） |
| --- | ---: | --- |
| 2009 | 0 | 本索引条件未命中；需继续研究，不表示不存在材料 |
| 2010 | 2 | 给力（early-geili）；日和中文配音（early-hiyori-dub） |
| 2011 | 1 | 膝盖中了一箭（early-arrow-knee） |
| 2012 | 1 | 元芳，你怎么看（early-yuanfang） |
| 2013 | 0 | 本索引条件未命中；需继续研究，不表示不存在材料 |
| 2014 | 2 | 我也是醉了（early-also-drunk）；画面太美我不敢看（early-too-beautiful）；张全蛋（early-zhang-quandan） |
| 2015 | 1 | Duang（early-duang）；Are you OK（early-are-you-ok） |
| 2016 | 4 | 感觉身体被掏空（mid-body-empty）；葛优躺（mid-geyou-recline）；先定一个小目标（mid-small-goal）；洪荒之力（mid-flood-power） |
| 2017 | 3 | 正方形打野（extra-square-jungle）；你有freestyle吗（mid-freestyle）；大碗宽面（mid-big-bowl）；7777（extra-7777） |
| 2018 | 3 | 橘里橘气（mid-orange-yuri）；skr（mid-skr）；这只竹鼠中暑了（mid-heatstroke） |
| 2019 | 3 | 律师函警告（mid-lawyer-letter）；有趣的女人（mid-interesting-woman）；猛男版新宝岛（mid-new-treasure-island） |
| 2020 | 5 | 后浪（mid-next-wave）；歪嘴龙王（mid-dragon-king）；年轻人不讲武德（mid-martial-etiquette）；耗子尾汁（mid-haozhi） |
| 2021 | 2 | 既见未来，为何不Buy（recent-why-not-buy）；为什么不ban猛犸（recent-ban-magnus） |
| 2022 | 0 | 本索引条件未命中；需继续研究，不表示不存在材料 |
| 2023 | 0 | 本索引条件未命中；需继续研究，不表示不存在材料 |
| 2024 | 3 | 广智救我（recent-guangzhi-save）；我来助你（recent-i-will-help）；鸣潮公式（recent-wuwa-formula）；沙威玛之歌（recent-shawarma） |
| 2025 | 0 | 本索引条件未命中；需继续研究，不表示不存在材料 |
| 2026 | 0 | 本索引条件未命中；需继续研究，不表示不存在材料 |

## 研究队列

当前队列277条，包含所有非 `verified`、缺少 `meaning/origin/platform_usage` 全文登记或 `origin.date` 未知的记录。队列之外的记录仍须按具体来源判断词义、别名、风险和材料局限。

```bash
python3 "SKILL_DIR/scripts/history.py" --output OUTPUT_DIR/history.json
```

输出为198个年份×分类格及确定性排序队列。只读本地数据，输出必须是新文件；完整词条及来源由查询工具或JSON分片读取。
