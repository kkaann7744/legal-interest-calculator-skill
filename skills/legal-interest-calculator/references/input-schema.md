# 参数格式

程序严格拒绝拼错、重复或不支持的字段。日期为 YYYY-MM-DD；金额、利率、倍数、加点均使用非负十进制字符串，整数也可；不能使用 JSON 浮点数、千位分隔符、科学计数法或百分号。金额仍以原币种计算，不自动转换汇率。所有类别金额均四舍五入至两位小数。

根对象必须包含：

| 字段 | 值 |
| --- | --- |
| schema_version | "1.0" |
| title | 报告名称，不超过100字 |
| currency | 如 "人民币元" |
| rounding | "total" 或 "segment" |
| general | 一般债务计息项数组，可为空 |
| delay | 加倍部分计息项数组，可为空；两类不能同时为空 |
| analysis | {"status":"ready","unresolved":[],"rules":["口径及依据"],"assumptions":[]} |

ready 表示口径齐备；draft 或存在 unresolved 时程序拒绝计算。rules、assumptions 为可选文本数组，其余 analysis 字段必须存在。

每个计息项的字段如下（还本生效口径仅在存在还本时必填）：

| 字段 | 规则 |
| --- | --- |
| id | 所有类别中唯一的编号 |
| principal | 原始计息本金，必须大于0 |
| start / end | 输入区间，截止日不早于起始日 |
| day_count | both：首尾均算；exclude_start：不算首日；exclude_end：不算末日；exclude_both：首尾均不算 |
| repayment_effective | 存在还本记录时必填：same_day为当日扣减，next_day为次日扣减；无还本时可省略 |
| source | 参数来源，如“借款合同第3条、付款凭证第2页”或“用户提供参数” |
| rate | 一般债务必须有；加倍部分不能有，按0.0175%/日计算 |

可选 repayments 是 {"id":"P1","date":"2024-01-05","amount":"40000","source":"凭证第2页，已确认全部冲减本金"} 的数组；id 可选但同一计息项中不能重复。日期须在输入区间内，累计还本不能超过原本金。多笔同日还本按合计减少本金，不自行去重或分配清偿顺序。一般债务与加倍部分的还本各自明确录入。

可选 exclude_periods 是 {"start":"2024-01-03","end":"2024-01-04","reason":"裁定不计息期间","source":"裁定第2页"} 的数组。排除首尾均包含在内；重叠排除期间按并集处理，同一日只排除一次。

## 利率对象

固定年利率：

    {"mode":"fixed","unit":"annual_percent","value":"6","basis":365}

固定月利率：

    {"mode":"fixed","unit":"monthly_percent","value":"1","basis":365}

月利率乘12后年化，并按实际天数计算。按整月结息、月剩余天数采用另一基准的合同不能直接套用。

固定日利率：

    {"mode":"fixed","unit":"daily_percent","value":"0.05"}

0.05 代表0.05%，即每日万分之五。日利率不能再填写 basis。

指定利率节点：

    {"mode":"schedule","unit":"annual_percent","basis":365,
     "schedule":[
       {"effective_date":"2024-01-01","value":"6","source":"合同第3条"},
       {"effective_date":"2024-07-01","value":"5","source":"补充协议第1条"}]}

节点按日期严格升序、不得重复；第一个节点不晚于输入起始日。后续节点当日采用新利率。

动态LPR：

    {"mode":"lpr","unit":"annual_percent","basis":365,
     "term":"one_year","policy":"floating","multiplier":"1","spread_points":"0"}

固定基准日LPR：

    {"mode":"lpr","unit":"annual_percent","basis":365,
     "term":"one_year","policy":"fixed","anchor_date":"2024-07-01",
     "multiplier":"1","spread_points":"0"}

term 只能为 one_year 或 five_year。multiplier、spread_points 可省略，分别默认1和0。公式为 LPR百分数×倍数＋年利率加点百分数；加点1代表1个百分点。固定基准日取当日已经公布的最新报价。动态LPR按公告日期自动分段，不能指定 anchor_date。银行合同的年度重定价等情况应先整理成明确 schedule。

年基准可为360、365、366或 "actual_actual"。actual_actual 在平年使用365、闰年使用366，并按跨年节点分段。不自行默认年基准。

## 舍入与结果

- total：分段保留高精度；按一般债务和加倍部分分别求和后四舍五入至分；总利息为两个类别展示金额之和。分段展示值不直接求和。
- segment：每个展示分段先四舍五入至分，再求和。
- 相邻区间的本金、利率、分母相同且连续时合并，避免未变动的每月LPR公告改变分段舍入结果。

输出包含 source-plan.json、interest-result.json、manifest.json、verification.json，以及选择的 Word、Excel、HTML；使用LPR时另存 lpr-snapshot.json。JSON 中 interest_raw 保留高精度，interest 和 totals 为两位小数文本。Word和Excel采用展示金额，Excel另有未舍入值与逐行公式。Excel为计算快照，修改原始参数应重新运行工具。

支持的日期范围为1900至2100年；单项区间不超过100年、合计输入日数不超过200万；每类最多1000项。加倍部分起始日不得早于2014-08-01。LPR快照自2019-08-20起；动态LPR截止日不得晚于 verified_through，固定LPR的 anchor_date 不得超出核验范围。

完整虚构样例见 [examples/fixed-repayment.json](../examples/fixed-repayment.json)、[examples/lpr-floating.json](../examples/lpr-floating.json) 和 [examples/delay-exclusions.json](../examples/delay-exclusions.json)。
