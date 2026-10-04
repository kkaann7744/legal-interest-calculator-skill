# 法律利息计算 Skill

模型根据合同、裁判文书、还款记录和用户提供的信息确定计息口径；本地程序计算并稳定输出 Word、Excel、HTML 和完整计算记录；模型最后复核材料依据和结果。

本项目由既有法律利息计算工具的计算场景整理而来。计算使用十进制数，核验使用独立的逐日有理数算法。运行只需 Python 3.10 及以上，计算和导出不联网，不依赖模型算术、API密钥或第三方 Python 包。只有单独更新官方公开 LPR 数据时需要联网与系统 curl。

## 安装与调用

将下列链接交给支持从 GitHub 安装 skill、执行本地 Python 的智能体：

    请安装这个 Skill，并根据我提供的材料计算利息：
    https://github.com/kkaann7744/legal-interest-calculator-skill/tree/main/skills/legal-interest-calculator

Codex 可通过 skill-installer 安装该 GitHub 子目录。也可以下载仓库，将 skills/legal-interest-calculator 整个目录放进客户端支持的本地 skills 目录。安装方式与目录由客户端决定；普通聊天环境仅提供链接并不必然具备本地执行能力。

安装后使用：

    使用 $legal-interest-calculator，根据这些文件确定利息计算方式，
    计算至我指定的截止日，输出 Word 和 Excel，最后复核参数和结果。

模型会提取口径与来源。关键口径缺失时先补齐；口径明确时直接调用本地工具。

## 支持范围

- 约定年、月、日利率；明确的利率变动节点。
- 一年期／五年期以上 LPR，动态分段或固定基准日，可设置明确倍数和正向加点。
- 多项本金、多笔已确认冲减本金的还款；当日／次日扣减；四种首尾日口径。
- 360／365／366年基准及按实际年度天数分段。
- 2014-08-01起的迟延履行加倍部分，单独基数和区间，支持还本与排除计息期间。
- 逐段舍入或按类别汇总后舍入；每次保存输入、LPR快照、结果和核验记录。

工具执行单利及明确参数。复利、整月结息、清偿顺序自动分配、自动判断保护上限、银行合同复杂重定价或2014年前的加倍规则，需要先由使用者确定可表达的规则。原始文件的模型处理范围由客户端决定，本地计算不改变已经提交给模型的材料处理方式。

## 不通过模型直接运行

在仓库目录执行以下虚构样例：

    python3 skills/legal-interest-calculator/scripts/calculate.py \
      --input skills/legal-interest-calculator/examples/fixed-repayment.json \
      --output-dir outputs/example-01

完整计算包包含：

| 文件 | 用途 |
| --- | --- |
| interest-report.docx | Word计算报告 |
| interest-report.xlsx | Excel明细及公式 |
| interest-report.html | 可离线打开的报告 |
| source-plan.json | 输入参数和来源 |
| interest-result.json | 高精度分段和金额 |
| lpr-snapshot.json | 使用LPR时保存当次利率记录 |
| manifest.json | 输出文件校验清单 |
| verification.json | 独立重算和最终文件核对 |

模型完成依据与结果复核后另存 model-review.md。默认不覆盖已有输出。仅需JSON时使用 --formats json。

只检查参数，不生成报告或金额：

    python3 skills/legal-interest-calculator/scripts/calculate.py \
      --input skills/legal-interest-calculator/examples/fixed-repayment.json --check-only

预检通过返回 status=valid；未决口径、缺项、无效参数或未核验的LPR区间返回错误。预检不会自动补齐事实，也不代替材料依据复核。简单且完整的参数可以直接正式计算。

独立复核已有计算包：

    python3 skills/legal-interest-calculator/scripts/verify.py --bundle outputs/example-01

更新官方公开 LPR 快照：

    python3 skills/legal-interest-calculator/scripts/update_lpr.py --through 2026-10-02

将日期替换为实际核验当天。动态LPR截止日超过核验日期时会停止计算。官方历史接口日期按原值保留。

## 验证与依据

    python3 -m unittest discover -s tests -v

测试覆盖还本和利率变动同日、闰年、首尾日、日/月/年利率、固定及动态LPR、排除期间、舍入、拒绝未决口径、预检无文件写入、篡改检测与重复运行结果一致性。Excel复核同时检查利率单位、分母、类别、明细行数与计算及舍入公式，防止缓存金额正确而公式参数错误。测试和示例全部为虚构材料。

参数细节见 [输入格式](skills/legal-interest-calculator/references/input-schema.md)，材料复核要求及官方来源见 [计息规则](skills/legal-interest-calculator/references/legal-rules.md)。

反馈问题时请使用虚构或已获授权公开的材料。仓库仅发布程序与公开数据，不用于保存真实案件文件。

MIT License。
