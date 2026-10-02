"""Independent daily Fraction recalculation plus final-export reconciliation."""
from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from calendar import isleap
from datetime import date, timedelta
from decimal import localcontext
from fractions import Fraction
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from calculator import PlanError, digest, load_rates, read_json, require, validate_plan


def rounded(value):
    cents = (value.numerator * 200 + value.denominator) // (2 * value.denominator)
    return f"{cents // 100}.{cents % 100:02d}"


def daily_rows(plan, rates):
    rows = []
    for kind in ("general", "delay"):
        for leg in plan[kind]:
            d, end = date.fromisoformat(leg["start"]), date.fromisoformat(leg["end"])
            if leg["day_count"] in ("exclude_start", "exclude_both"):
                d += timedelta(days=1)
            if leg["day_count"] in ("exclude_end", "exclude_both"):
                end -= timedelta(days=1)
            while d <= end:
                active = not any(date.fromisoformat(p["start"]) <= d <= date.fromisoformat(p["end"])
                                 for p in leg.get("exclude_periods", []))
                principal = Fraction(str(leg["principal"]))
                for p in leg.get("repayments", []):
                    payment_day = date.fromisoformat(p["date"])
                    if payment_day < d or payment_day == d and leg["repayment_effective"] == "same_day":
                        principal -= Fraction(str(p["amount"]))
                if active and principal:
                    if kind == "delay":
                        value, unit, basis = Fraction("0.0175"), "daily_percent", 1
                    else:
                        rate = leg["rate"]
                        unit = rate["unit"]
                        if rate["mode"] == "fixed":
                            value = Fraction(str(rate["value"]))
                        elif rate["mode"] == "schedule":
                            selected = max((n for n in rate["schedule"] if date.fromisoformat(n["effective_date"]) <= d),
                                           key=lambda n: n["effective_date"])
                            value = Fraction(str(selected["value"]))
                        else:
                            reference = date.fromisoformat(rate["anchor_date"]) if rate["policy"] == "fixed" else d
                            selected = max((n for n in rates["records"] if date.fromisoformat(n["date"]) <= reference),
                                           key=lambda n: n["date"])
                            value = Fraction(selected[rate["term"]]) * Fraction(str(rate.get("multiplier", "1")))
                            value += Fraction(str(rate.get("spread_points", "0")))
                        basis = 1 if unit == "daily_percent" else rate["basis"]
                        if basis == "actual_actual":
                            basis = 366 if isleap(d.year) else 365
                    amount = principal * value / 100 / basis * (12 if unit == "monthly_percent" else 1)
                    signature = (kind, leg["id"], principal, value, unit, basis)
                    if rows and rows[-1]["signature"] == signature and rows[-1]["end"] + timedelta(days=1) == d:
                        rows[-1]["end"], rows[-1]["days"] = d, rows[-1]["days"] + 1
                        rows[-1]["raw"] += amount
                    else:
                        rows.append({"signature": signature, "start": d, "end": d, "days": 1, "raw": amount})
                d += timedelta(days=1)
    return rows


def verify_math(plan, result, rates):
    with localcontext() as context:
        context.prec = 60
        validate_plan(plan, rates)
    require(result.get("schema_version") == "1.0", "结果版本不支持")
    require(result.get("plan_sha256") == digest(plan), "输入参数与结果不一致")
    has_lpr = any(leg["rate"]["mode"] == "lpr" for leg in plan["general"])
    require(result.get("rates_sha256") == (digest(rates) if has_lpr else None), "利率快照与结果不一致")
    for key in ("title", "currency", "rounding"):
        require(result.get(key) == plan[key], f"结果的{key}与输入不一致")
    expected = daily_rows(plan, rates)
    require(isinstance(result.get("rows"), list) and len(result["rows"]) == len(expected), "计息分段数量不一致")
    accum = {"general": Fraction(0), "delay": Fraction(0)}
    for i, (actual, wanted) in enumerate(zip(result["rows"], expected)):
        kind, identifier, principal, value, unit, basis = wanted["signature"]
        required = {"kind", "id", "start", "end", "days", "principal", "rate", "rate_unit", "basis", "interest_raw", "interest"}
        require(set(actual) == required, f"分段{i + 1}字段不完整")
        for key, expected_value in (("kind", kind), ("id", identifier), ("start", wanted["start"].isoformat()),
                                    ("end", wanted["end"].isoformat()), ("days", wanted["days"]), ("rate_unit", unit),
                                    ("basis", basis)):
            require(actual[key] == expected_value, f"分段{i + 1}的{key}不一致")
        require(Fraction(actual["principal"]) == principal and Fraction(actual["rate"]) == value,
                f"分段{i + 1}的本金或利率不一致")
        require(abs(Fraction(actual["interest_raw"]) - wanted["raw"]) < Fraction(1, 10 ** 38),
                f"分段{i + 1}的未舍入利息不一致")
        require(actual["interest"] == rounded(wanted["raw"]), f"分段{i + 1}舍入结果不一致")
        accum[kind] += Fraction(actual["interest"]) if plan["rounding"] == "segment" else wanted["raw"]
    totals = {k: rounded(v) for k, v in accum.items()}
    totals["interest"] = rounded(Fraction(totals["general"]) + Fraction(totals["delay"]))
    require(result.get("totals") == totals, "合计金额不一致")
    return {"status": "passed", "method": "independent_daily_fraction", "checked_rows": len(expected),
            "checked_days": sum(r["days"] for r in expected), "totals": totals,
            "legal_basis_review": "模型须另行核对原始材料和计息口径"}


def xml_cells(sheet):
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    result = {}
    for c in ET.fromstring(sheet).findall(".//s:sheetData/s:row/s:c", namespace):
        if c.get("t") == "inlineStr":
            value = "".join(t.text or "" for t in c.findall(".//s:t", namespace))
        else:
            value = c.findtext("s:v", "", namespace)
        result[c.get("r")] = (value, c.findtext("s:f", None, namespace))
    return result


def verify_artifacts(bundle, result):
    manifest = read_json(bundle / "manifest.json")
    require(isinstance(manifest.get("files"), dict), "输出清单无效")
    require({"source-plan.json", "interest-result.json"} <= set(manifest["files"]), "输出清单缺少必要文件")
    for name, checksum in manifest["files"].items():
        require(Path(name).name == name and name not in {"manifest.json", "verification.json"}, "输出清单文件名无效")
        path = bundle / name
        require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum, "导出文件校验值不一致")
        if path.suffix in {".xlsx", ".docx"}:
            with ZipFile(path) as z:
                require(z.testzip() is None, "Office文件压缩包损坏")
                for member in z.namelist():
                    if member.endswith((".xml", ".rels")):
                        ET.fromstring(z.read(member))
    xlsx = bundle / "interest-report.xlsx"
    if xlsx.name in manifest["files"]:
        with ZipFile(xlsx) as z:
            cells = xml_cells(z.read("xl/worksheets/sheet1.xml"))
        for i, row in enumerate(result["rows"], 8):
            for col, key in (("B", "id"), ("C", "start"), ("D", "end"), ("E", "days"), ("F", "principal"),
                             ("G", "rate"), ("J", "interest_raw"), ("K", "interest")):
                actual = cells.get(f"{col}{i}", ("", None))[0]
                require(actual == str(row[key]), f"Excel分段{i - 7}的{key}与计算结果不一致")
            expected_formula = f"F{i}*G{i}/100*E{i}/I{i}*IF(H{i}=\"月\",12,1)"
            require(cells[f"J{i}"][1] == expected_formula, "Excel计算公式不一致")
        require(cells.get("B3", ("",))[0] == result["totals"]["general"], "Excel一般债务合计不一致")
        require(cells.get("E3", ("",))[0] == result["totals"]["delay"], "Excel加倍部分合计不一致")
        require(cells.get("H3", ("",))[0] == result["totals"]["interest"], "Excel利息合计不一致")
    docx = bundle / "interest-report.docx"
    if docx.name in manifest["files"]:
        with ZipFile(docx) as z:
            root = ET.fromstring(z.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        table = root.find(".//w:tbl", ns)
        require(table is not None, "Word缺少计息明细表")
        trs = table.findall("w:tr", ns)
        require(len(trs) == len(result["rows"]) + 1, "Word明细行数不一致")
        for tr, row in zip(trs[1:], result["rows"]):
            values = ["".join(t.text or "" for t in cell.findall(".//w:t", ns)) for cell in tr.findall("w:tc", ns)]
            require(values == ["一般债务" if row["kind"] == "general" else "加倍部分", row["id"], row["start"], row["end"],
                               str(row["days"]), row["principal"], row["rate"] + "%/" + {"annual_percent": "年", "monthly_percent": "月", "daily_percent": "日"}[row["rate_unit"]],
                               str(row["basis"]), row["interest"]], "Word明细与计算结果不一致")
        full_text = "".join(t.text or "" for t in root.findall(".//w:t", ns))
        for label, key in (("一般债务利息", "general"), ("加倍部分债务利息", "delay"), ("利息合计", "interest")):
            require(f"{label} {result['totals'][key]}" in full_text, "Word合计与计算结果不一致")
    return len(manifest["files"])


def verify_bundle(bundle):
    bundle = Path(bundle)
    plan, result = read_json(bundle / "source-plan.json"), read_json(bundle / "interest-result.json")
    rates = load_rates(bundle / "lpr-snapshot.json") if (bundle / "lpr-snapshot.json").exists() else load_rates()
    report = verify_math(plan, result, rates)
    report["checked_files"] = verify_artifacts(bundle, result)
    return report


def main():
    parser = argparse.ArgumentParser(description="按日独立重算，并核对最终Word、Excel及文件完整性")
    parser.add_argument("--bundle", required=True)
    args = parser.parse_args()
    try:
        report = verify_bundle(args.bundle)
    except (PlanError, OSError, KeyError, ValueError, TypeError, ET.ParseError, BadZipFile) as e:
        report = {"status": "failed", "message": str(e)}
    try:
        (Path(args.bundle) / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        report = {"status": "failed", "message": f"无法保存核验记录：{e}"}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
