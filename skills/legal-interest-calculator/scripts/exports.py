"""Portable native Office/HTML exports; no external packages or remote services."""
from __future__ import annotations

import html
import json
import math
import unicodedata
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

KIND = {"general": "一般债务", "delay": "加倍部分"}
UNIT = {"annual_percent": "年", "monthly_percent": "月", "daily_percent": "日"}
DAY_COUNT = {"both": "首尾日均计息", "exclude_start": "不计起始日，计截止日",
             "exclude_end": "计起始日，不计截止日", "exclude_both": "首尾日均不计息"}
REPAYMENT = {"same_day": "还本当日扣减本金", "next_day": "还本次日扣减本金"}
ROUNDING_LABEL = {"total": "按类别汇总后舍入", "segment": "逐段舍入后汇总"}
ROUNDING = {
    "total": "未舍入利息先分别按类别求和，再四舍五入至分；分段展示金额不直接求和。利息合计为两个类别的展示合计相加。",
    "segment": "每个计息分段先四舍五入至分，再按类别求和。利息合计为两个类别合计相加。",
}
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def rate_description(rate):
    basis = rate.get("basis")
    suffix = "；按实际年度365或366天分段" if basis == "actual_actual" else f"；每年按{basis}天折算" if basis else ""
    if rate["mode"] == "fixed":
        desc = f"{UNIT[rate['unit']]}利率{rate['value']}%"
    elif rate["mode"] == "schedule":
        nodes = "；".join(f"{n['effective_date']}起{n['value']}%（依据：{n['source']}）" for n in rate["schedule"])
        desc = f"{UNIT[rate['unit']]}利率分段：{nodes}"
    else:
        term = "一年期LPR" if rate["term"] == "one_year" else "五年期以上LPR"
        policy = "随公告调整" if rate["policy"] == "floating" else f"固定采用{rate['anchor_date']}适用的利率"
        desc = f"{term}，{policy}；LPR×{rate.get('multiplier', '1')}＋{rate.get('spread_points', '0')}个百分点"
    if rate["unit"] == "monthly_percent":
        suffix += "；月利率乘12年化后按实际天数计息"
    return desc + suffix


def parameter_rows(plan):
    rows = []
    for kind in ("general", "delay"):
        for leg in plan[kind]:
            prefix = f"{leg['id']} {KIND[kind]}"
            rows.extend([(f"{prefix} 本金", leg["principal"]),
                         (f"{prefix} 起始日", leg["start"]), (f"{prefix} 截止日", leg["end"]),
                         (f"{prefix} 首尾日", DAY_COUNT[leg["day_count"]]),
                         (f"{prefix} 利率", rate_description(leg["rate"]) if kind == "general" else "日利率0.0175%"),
                         (f"{prefix} 材料依据", leg["source"])])
            if leg.get("repayments"):
                rows.append((f"{prefix} 还本生效", REPAYMENT[leg["repayment_effective"]]))
            for payment in leg.get("repayments", []):
                rows.append((f"{prefix} 归还本金", f"{payment['date']}，金额{payment['amount']}；依据：{payment['source']}"))
            for period in leg.get("exclude_periods", []):
                rows.append((f"{prefix} 排除计息", f"{period['start']}至{period['end']}；{period['reason']}；依据：{period['source']}"))
    return rows


def write_zip(path, members):
    with ZipFile(path, "w") as z:
        for name, content in sorted(members.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            z.writestr(info, content.encode("utf-8") if isinstance(content, str) else content)


def json_file(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def paragraph(value, style="Normal", size=None, align=None):
    properties = f'<w:pStyle w:val="{style}"/>'
    if align:
        properties += f'<w:jc w:val="{align}"/>'
    run_properties = f'<w:rPr><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>' if size else ""
    lines = str(value).split("\n")
    content = "<w:br/>".join(f'<w:t xml:space="preserve">{escape(line)}</w:t>' for line in lines)
    return f"<w:p><w:pPr>{properties}</w:pPr><w:r>{run_properties}{content}</w:r></w:p>"


def word_table(records, widths):
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    rows = []
    for i, record in enumerate(records):
        cells = []
        for value, width in zip(record, widths):
            shading = '<w:shd w:fill="E7E6E6"/>' if i == 0 else ""
            properties = f'<w:tcW w:w="{width}" w:type="dxa"/><w:vAlign w:val="center"/>{shading}'
            cells.append(f'<w:tc><w:tcPr>{properties}</w:tcPr>{paragraph(value, "TableText", 19, "center")}</w:tc>')
        header = "<w:tblHeader/>" if i == 0 else ""
        rows.append(f'<w:tr><w:trPr><w:cantSplit/>{header}</w:trPr>{"".join(cells)}</w:tr>')
    borders = "".join(f'<w:{edge} w:val="single" w:sz="4" w:color="D9D9D9"/>'
                      for edge in ("top", "left", "bottom", "right", "insideH", "insideV"))
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/>'
            f'<w:tblLayout w:type="fixed"/><w:tblBorders>{borders}</w:tblBorders>'
            '<w:tblCellMar><w:top w:w="90" w:type="dxa"/><w:bottom w:w="90" w:type="dxa"/>'
            '<w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar>'
            f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>')


def export_docx(path, plan, result):
    body = [paragraph(plan["title"], "Title"),
            paragraph(f"本报告按所列材料和计息口径计算一般债务利息及迟延履行加倍部分，金额单位为 {plan['currency']}。"),
            paragraph(f"一般债务利息 {result['totals']['general']}    加倍部分债务利息 {result['totals']['delay']}    利息合计 {result['totals']['interest']}", "Heading1"),
            paragraph("计息明细", "Heading1")]
    records = [["类别", "编号", "起始日", "截止日", "天数", "计息本金", "利率", "分母", "利息"]]
    for r in result["rows"]:
        records.append([KIND[r["kind"]], r["id"], r["start"], r["end"], r["days"], r["principal"],
                        r["rate"] + "%/" + UNIT[r["rate_unit"]], r["basis"], r["interest"]])
    body.append(word_table(records, [1000, 720, 1400, 1400, 650, 1900, 1450, 650, 1700]))
    body += [paragraph(ROUNDING[plan["rounding"]]), paragraph("参数与材料依据", "Heading1")]
    for kind in ("general", "delay"):
        for leg in plan[kind]:
            repayment = f"；{REPAYMENT[leg['repayment_effective']]}" if leg.get("repayments") else ""
            body.append(paragraph(f"{leg['id']} {KIND[kind]}：本金{leg['principal']}；"
                                  f"计息区间{leg['start']}至{leg['end']}；{DAY_COUNT[leg['day_count']]}{repayment}。"))
            rate = rate_description(leg["rate"]) if kind == "general" else "日利率0.0175%"
            body.append(paragraph(f"利率：{rate}。材料依据：{leg['source']}"))
            for payment in leg.get("repayments", []):
                body.append(paragraph(f"归还本金：{payment['date']}，金额{payment['amount']}；依据：{payment['source']}"))
            for period in leg.get("exclude_periods", []):
                body.append(paragraph(f"排除计息：{period['start']}至{period['end']}；{period['reason']}；依据：{period['source']}"))
    if result["lpr_snapshot"]:
        snapshot = result["lpr_snapshot"]
        body.append(paragraph(f"LPR最新公告 {snapshot['latest_announcement']}，核验至 {snapshot['verified_through']}。来源：{snapshot['source']}"))
    if plan["analysis"].get("rules"):
        body.append(paragraph("\n".join(plan["analysis"]["rules"])))
    for item in plan["analysis"].get("assumptions", []):
        body.append(paragraph("采用的假设：" + item))
    for warning in result["warnings"]:
        body.append(paragraph(warning))
    body.append(paragraph(f"引擎版本 {result['engine_version']}。输入指纹 {result['plan_sha256']}。程序核验文件见 verification.json；法律口径由使用者结合材料复核。", size=20))
    section = '<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="500" w:footer="500"/></w:sectPr>'
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>{"".join(body)}{section}</w:body></w:document>'
    styles = f'''<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="{W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="仿宋"/><w:color w:val="000000"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:jc w:val="center"/><w:spacing w:after="200" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:eastAsia="黑体"/><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="180" w:after="120"/></w:pPr><w:rPr><w:rFonts w:eastAsia="黑体"/><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:eastAsia="宋体"/><w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr></w:style>
</w:styles>'''
    write_zip(path, {
        "[Content_Types].xml": f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": document, "word/styles.xml": styles,
        "word/_rels/document.xml.rels": f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
    })


def column(n):
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def sheet(rows, widths, header_rows=(), formulas=None, numeric=None, merges=(), freeze=False):
    formulas, numeric = formulas or {}, numeric or {}
    data = []
    for i, row in enumerate(rows, 1):
        cells = []
        for j, value in enumerate(row, 1):
            ref = f"{column(j)}{i}"
            style = 1 if i in header_rows else numeric.get(ref, 0)
            if ref in numeric:
                v = f"<v>{escape(str(value))}</v>"
                f = f"<f>{escape(formulas[ref])}</f>" if ref in formulas else ""
                cells.append(f'<c r="{ref}" s="{style}">{f}{v}</c>')
            else:
                cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>')
        line_count = 1
        for j, value in enumerate(row, 1):
            if f"{column(j)}{i}" in numeric:
                continue
            width = widths[j - 1]
            for merged in merges:
                first, last = merged.split(":")
                if first == f"{column(j)}{i}":
                    last_column = last.rstrip("0123456789")
                    last_index = 0
                    for letter in last_column:
                        last_index = last_index * 26 + ord(letter) - 64
                    width = sum(widths[j - 1:last_index])
            lines = sum(max(1, math.ceil(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in line)
                                        / max(1, width - 3))) for line in str(value).split("\n"))
            line_count = max(line_count, lines)
        height = max(32 if i in header_rows else 28, line_count * 17 + 8)
        data.append(f'<row r="{i}" ht="{height}" customHeight="1">{"".join(cells)}</row>')
    cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    pane = '<pane ySplit="7" topLeftCell="A8" activePane="bottomLeft" state="frozen"/>' if freeze else ""
    merge_xml = f'<mergeCells count="{len(merges)}">{"".join(f"<mergeCell ref={chr(34)}{r}{chr(34)}/>" for r in merges)}</mergeCells>' if merges else ""
    return (f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{S}">'
            f'<dimension ref="A1:{column(len(widths))}{len(rows)}"/><sheetViews><sheetView workbookViewId="0">{pane}</sheetView></sheetViews>'
            f'<sheetFormatPr defaultRowHeight="28"/><cols>{cols}</cols><sheetData>{"".join(data)}</sheetData>{merge_xml}'
            '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
            '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>')


def export_xlsx(path, plan, result):
    rows = [[plan["title"]], ["一般债务利息", "", "", "加倍部分债务利息", "", "", "利息合计"],
            ["", result["totals"]["general"], "", "", result["totals"]["delay"], "", "", result["totals"]["interest"]],
            ["金额单位", plan["currency"], "", "舍入口径", ROUNDING_LABEL[plan["rounding"]]],
            [ROUNDING[plan["rounding"]]], ["下表为本次计算快照；修改原始参数后，请重新运行工具。J列保留计算公式和未舍入值。"],
            ["类别", "编号", "起始日", "截止日", "天数", "计息本金", "利率百分数", "利率单位", "分母", "未舍入利息", "展示利息"]]
    numeric = {"B3": 2, "E3": 2, "H3": 2}
    formulas = {}
    for i, r in enumerate(result["rows"], 8):
        rows.append([KIND[r["kind"]], r["id"], r["start"], r["end"], r["days"], r["principal"], r["rate"],
                     UNIT[r["rate_unit"]], r["basis"], r["interest_raw"], r["interest"]])
        for col, style in (("E", 3), ("F", 2), ("G", 4), ("I", 3), ("J", 5), ("K", 2)):
            numeric[f"{col}{i}"] = style
        formulas[f"J{i}"] = f'F{i}*G{i}/100*E{i}/I{i}*IF(H{i}="月",12,1)'
        formulas[f"K{i}"] = f"ROUND(J{i},2)"
    inputs = [["参数及依据", "内容"], ["输入指纹", result["plan_sha256"]], ["引擎版本", result["engine_version"]]]
    inputs.extend(parameter_rows(plan))
    for key in ("rules", "assumptions"):
        inputs.extend([["计息口径" if key == "rules" else "采用的假设", item] for item in plan["analysis"].get(key, [])])
    if result["lpr_snapshot"]:
        labels = {"source": "公开来源", "verified_through": "核验日期", "latest_announcement": "最新公告日期"}
        inputs.extend([[f"LPR {labels[key]}", value] for key, value in result["lpr_snapshot"].items()])
    inputs.extend([["说明", item] for item in result["warnings"]])
    styles = f'''<styleSheet xmlns="{S}">
<numFmts count="4"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="0.########"/><numFmt numFmtId="166" formatCode="0.0000000000"/><numFmt numFmtId="167" formatCode="0"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="宋体"/><color rgb="FF000000"/></font><font><b/><sz val="11"/><name val="黑体"/><color rgb="FF000000"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border/><border><left style="thin"><color rgb="FFD9D9D9"/></left><right style="thin"><color rgb="FFD9D9D9"/></right><top style="thin"><color rgb="FFD9D9D9"/></top><bottom style="thin"><color rgb="FFD9D9D9"/></bottom></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/><xf numFmtId="167" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/><xf numFmtId="166" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    write_zip(path, {
        "[Content_Types].xml": f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{S}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="利息计算表" sheetId="1" r:id="rId1"/><sheet name="参数及依据" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": styles,
        "xl/worksheets/sheet1.xml": sheet(rows, [13, 10, 15, 15, 9, 20, 17, 12, 10, 22, 20], (1, 2, 7), formulas, numeric,
                                           ("A1:K1", "A2:C2", "D2:F2", "G2:K2", "E4:K4", "A5:K5", "A6:K6"), True),
        "xl/worksheets/sheet2.xml": sheet(inputs, [32, 115], (1,)),
    })


def export_html(path, plan, result):
    e = lambda v: html.escape(str(v), quote=True)
    rows = "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in
                      [KIND[r["kind"]], r["id"], r["start"], r["end"], r["days"], r["principal"],
                       r["rate"] + "%/" + UNIT[r["rate_unit"]], r["basis"], r["interest"]]) + "</tr>" for r in result["rows"])
    rules = "".join(f"<li>{e(item)}</li>" for item in plan["analysis"].get("rules", []))
    parameters = "".join(f"<dt>{e(label)}</dt><dd>{e(value)}</dd>" for label, value in parameter_rows(plan))
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{e(plan["title"])}</title><style>
body{{font:16px/1.65 -apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif;color:#17202a;background:#f4f5f7;margin:0}}
main{{max-width:1160px;margin:32px auto;padding:32px;background:#fff;border:1px solid #ddd;border-radius:12px}}
h1{{font-size:26px;margin-top:0}}.totals{{display:flex;gap:40px;flex-wrap:wrap;padding:20px;background:#f5f7f9;border-radius:8px}}
.totals strong{{display:block;font-size:25px}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px;border:1px solid #ddd;white-space:nowrap;text-align:right}}th{{background:#edf0f3}}small{{color:#555}}dt{{font-weight:600;margin-top:14px}}dd{{margin-left:0;overflow-wrap:anywhere}}
@media(max-width:700px){{main{{padding:18px;margin:10px}}}}@media print{{body{{background:white}}main{{border:0;margin:0;padding:0}}}}
</style><main><h1>{e(plan["title"])}</h1><p>金额单位 {e(plan["currency"])}。本报告由本地程序生成。</p>
<div class="totals"><div>一般债务利息<strong>{e(result["totals"]["general"])}</strong></div><div>加倍部分<strong>{e(result["totals"]["delay"])}</strong></div><div>利息合计<strong>{e(result["totals"]["interest"])}</strong></div></div>
<h2>计息明细</h2><div class="scroll"><table><thead><tr>{"".join(f"<th>{s}</th>" for s in ["类别","编号","起始日","截止日","天数","计息本金","利率","分母","利息"])}</tr></thead><tbody>{rows}</tbody></table></div>
<p><small>{e(ROUNDING[plan["rounding"]])}</small></p><h2>计息口径</h2><ul>{rules}</ul><h2>参数与材料依据</h2><dl>{parameters}</dl>
<small>引擎版本 {e(result["engine_version"])}；程序核验见 verification.json，法律口径需结合原始材料复核。</small></main></html>'''
    Path(path).write_text(document, encoding="utf-8")
