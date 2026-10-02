"""Offline, deterministic simple-interest engine. Python 3.10+, stdlib only."""
from __future__ import annotations

import hashlib
import json
import re
from calendar import isleap
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path

VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parents[1]
CENT = Decimal("0.01")
DAY_RULES = {"both", "exclude_start", "exclude_end", "exclude_both"}
MAX_TOTAL_DAYS = 2_000_000


class PlanError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise PlanError(message)


def fields(obj, allowed, required, path):
    require(isinstance(obj, dict), f"{path}: 应为对象")
    extra = set(obj) - set(allowed)
    missing = set(required) - set(obj)
    require(not extra, f"{path}: 不支持的字段 {', '.join(sorted(extra))}")
    require(not missing, f"{path}: 缺少字段 {', '.join(sorted(missing))}")


def decimal(value, path, positive=False):
    require(isinstance(value, (str, int)) and not isinstance(value, bool),
            f"{path}: 使用十进制字符串或整数，避免 JSON 浮点数")
    raw = str(value)
    require(bool(re.fullmatch(r"\d{1,18}(?:\.\d{1,12})?", raw)),
            f"{path}: 应为非负十进制数，最多18位整数和12位小数")
    n = Decimal(raw)
    require(n > 0 if positive else n >= 0, f"{path}: 数值必须{'大于' if positive else '不小于'}0")
    return n


def day(value, path):
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)),
            f"{path}: 日期格式应为 YYYY-MM-DD")
    try:
        d = date.fromisoformat(value)
    except ValueError as e:
        raise PlanError(f"{path}: 日期不存在") from e
    require(1900 <= d.year <= 2100, f"{path}: 日期须在1900至2100年内")
    return d


def text(value, path, max_length=2000):
    require(isinstance(value, str) and len(value) <= max_length,
            f"{path}: 应为不超过{max_length}字的文本")
    require(not any(ord(c) < 32 and c not in "\n\r\t" for c in value),
            f"{path}: 含不支持的控制字符")
    return value


def unique_pairs(pairs):
    obj = {}
    for k, v in pairs:
        require(k not in obj, "JSON: 对象含重复字段")
        obj[k] = v
    return obj


def read_json(path):
    def reject_constant(_):
        raise PlanError("JSON: 不接受 NaN 或 Infinity")
    try:
        require(Path(path).stat().st_size <= 10_000_000, "JSON: 文件超过10MB")
        return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                          object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        raise PlanError("无法读取有效的 UTF-8 JSON 文件") from e


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def load_rates(path=None):
    data = read_json(path or ROOT / "data" / "lpr.json")
    fields(data, {"schema_version", "source", "verified_through", "latest_announcement", "records"},
           {"schema_version", "source", "verified_through", "latest_announcement", "records"}, "LPR")
    require(data["schema_version"] == "1.0", "LPR: 版本不支持")
    text(data["source"], "LPR.source")
    verified = day(data["verified_through"], "LPR.verified_through")
    latest = day(data["latest_announcement"], "LPR.latest_announcement")
    require(isinstance(data["records"], list) and 0 < len(data["records"]) <= 5000,
            "LPR.records: 记录数量无效")
    previous = None
    for i, row in enumerate(data["records"]):
        path = f"LPR.records[{i}]"
        fields(row, {"date", "one_year", "five_year"}, {"date", "one_year", "five_year"}, path)
        d = day(row["date"], path + ".date")
        require(previous is None or d > previous, "LPR: 日期必须严格升序且不重复")
        for key in ("one_year", "five_year"):
            require(decimal(row[key], path + "." + key) <= 100, f"{path}: 利率过大")
        previous = d
    require(previous == latest and latest <= verified, "LPR: 最新公告和核验日期不一致")
    return data


def rate_at(rate, d, rates):
    mode = rate["mode"]
    if mode == "fixed":
        return Decimal(str(rate["value"])), rate["unit"]
    if mode == "schedule":
        selected = [r for r in rate["schedule"] if date.fromisoformat(r["effective_date"]) <= d][-1]
        return Decimal(str(selected["value"])), rate["unit"]
    lookup = date.fromisoformat(rate["anchor_date"]) if rate["policy"] == "fixed" else d
    selected = [r for r in rates["records"] if date.fromisoformat(r["date"]) <= lookup][-1]
    key = "one_year" if rate["term"] == "one_year" else "five_year"
    return (Decimal(selected[key]) * Decimal(str(rate.get("multiplier", "1")))
            + Decimal(str(rate.get("spread_points", "0")))), "annual_percent"


def effective_range(leg):
    start, end = date.fromisoformat(leg["start"]), date.fromisoformat(leg["end"])
    if leg["day_count"] in {"exclude_start", "exclude_both"}:
        start += timedelta(days=1)
    if leg["day_count"] in {"exclude_end", "exclude_both"}:
        end -= timedelta(days=1)
    return start, end


def excluded(leg, d):
    return any(date.fromisoformat(p["start"]) <= d <= date.fromisoformat(p["end"])
               for p in leg.get("exclude_periods", []))


def balance_at(leg, d):
    lag = 1 if leg.get("repayment_effective", "next_day") == "next_day" else 0
    return Decimal(str(leg["principal"])) - sum(
        (Decimal(str(p["amount"])) for p in leg.get("repayments", [])
         if date.fromisoformat(p["date"]) + timedelta(days=lag) <= d), Decimal(0))


def denominator(rate, d):
    if rate["unit"] == "daily_percent":
        return 1
    basis = rate["basis"]
    return 366 if basis == "actual_actual" and isleap(d.year) else 365 if basis == "actual_actual" else basis


def validate_plan(plan, rates):
    fields(plan, {"schema_version", "title", "currency", "rounding", "general", "delay", "analysis"},
           {"schema_version", "title", "currency", "rounding", "general", "delay", "analysis"}, "plan")
    require(plan["schema_version"] == "1.0", "plan.schema_version: 版本不支持")
    text(plan["title"], "plan.title", 100)
    require(bool(plan["title"].strip()), "plan.title: 不能为空")
    text(plan["currency"], "plan.currency", 20)
    require(bool(plan["currency"].strip()), "plan.currency: 不能为空")
    require(plan["rounding"] in {"total", "segment"}, "plan.rounding: 应为 total 或 segment")
    analysis = plan["analysis"]
    fields(analysis, {"status", "unresolved", "rules", "assumptions"}, {"status", "unresolved"}, "analysis")
    require(analysis["status"] in {"draft", "ready"}, "analysis.status: 应为 draft 或 ready")
    require(isinstance(analysis["unresolved"], list), "analysis.unresolved: 应为数组")
    for i, v in enumerate(analysis["unresolved"]):
        text(v, f"analysis.unresolved[{i}]")
    for key in ("rules", "assumptions"):
        require(isinstance(analysis.get(key, []), list), f"analysis.{key}: 应为文本数组")
        for i, v in enumerate(analysis.get(key, [])):
            text(v, f"analysis.{key}[{i}]")
    require(analysis["status"] == "ready" and not analysis["unresolved"],
            "计息口径尚未齐备：请解决 analysis.unresolved，完成后将 status 设为 ready")
    ids, total_days, warnings = set(), 0, []
    for kind in ("general", "delay"):
        require(isinstance(plan[kind], list) and len(plan[kind]) <= 1000, f"{kind}: 应为不超过1000项的数组")
        for i, leg in enumerate(plan[kind]):
            path = f"{kind}[{i}]"
            allowed = {"id", "principal", "start", "end", "day_count", "repayment_effective",
                       "repayments", "exclude_periods", "source", "rate"}
            needed = {"id", "principal", "start", "end", "day_count", "source"}
            if kind == "general":
                needed.add("rate")
            else:
                allowed.remove("rate")
            fields(leg, allowed, needed, path)
            identifier = text(leg["id"], path + ".id", 50)
            require(identifier.strip() and identifier not in ids, path + ".id: 不能为空或重复")
            ids.add(identifier)
            principal = decimal(leg["principal"], path + ".principal", True)
            start, end = day(leg["start"], path + ".start"), day(leg["end"], path + ".end")
            require(end >= start and (end - start).days <= 36600, path + ": 日期倒置或超过100年")
            total_days += (end - start).days + 1
            require(total_days <= MAX_TOTAL_DAYS, "计息日总量超过200万，需拆分任务")
            require(leg["day_count"] in DAY_RULES, path + ".day_count: 不支持的首尾日口径")
            if "repayment_effective" in leg:
                require(leg["repayment_effective"] in {"same_day", "next_day"}, path + ": 还本生效口径无效")
            text(leg["source"], path + ".source")
            require(leg["source"].strip(), path + ".source: 需注明参数来源")
            payments = leg.get("repayments", [])
            require(isinstance(payments, list) and len(payments) <= 10000, path + ".repayments: 数量无效")
            require(not payments or "repayment_effective" in leg, path + ": 存在还本时须指定当日或次日生效口径")
            paid, payment_ids = Decimal(0), set()
            for j, payment in enumerate(payments):
                pp = f"{path}.repayments[{j}]"
                fields(payment, {"id", "date", "amount", "source"}, {"date", "amount", "source"}, pp)
                pd = day(payment["date"], pp + ".date")
                require(start <= pd <= end, pp + ": 还本日期超出输入区间")
                paid += decimal(payment["amount"], pp + ".amount", True)
                text(payment["source"], pp + ".source")
                require(payment["source"].strip(), pp + ".source: 需注明还本依据")
                if "id" in payment:
                    pid = text(payment["id"], pp + ".id", 50)
                    require(pid not in payment_ids, pp + ".id: 重复还本编号")
                    payment_ids.add(pid)
            require(paid <= principal, path + ": 累计归还本金超过本金；工具不自动分配费用或利息")
            periods = leg.get("exclude_periods", [])
            require(isinstance(periods, list) and len(periods) <= 1000, path + ".exclude_periods: 数量无效")
            for j, period in enumerate(periods):
                ep = f"{path}.exclude_periods[{j}]"
                fields(period, {"start", "end", "reason", "source"}, {"start", "end", "reason", "source"}, ep)
                a, b = day(period["start"], ep + ".start"), day(period["end"], ep + ".end")
                require(start <= a <= b <= end, ep + ": 排除期间倒置或超出区间")
                text(period["reason"], ep + ".reason")
                text(period["source"], ep + ".source")
                require(period["reason"].strip() and period["source"].strip(), ep + ": 需注明排除原因及依据")
            if kind == "delay":
                require(start >= date(2014, 8, 1), path + ": 加倍部分只支持2014-08-01起的规则")
                continue
            rate = leg["rate"]
            require(isinstance(rate, dict), path + ".rate: 应为对象")
            mode = rate.get("mode")
            common = {"mode", "unit", "basis"}
            if mode == "fixed":
                fields(rate, common | {"value"}, {"mode", "unit", "value"}, path + ".rate")
                require(decimal(rate["value"], path + ".rate.value") <= 1000, path + ": 利率超过支持范围")
            elif mode == "schedule":
                fields(rate, common | {"schedule"}, {"mode", "unit", "schedule"}, path + ".rate")
                require(isinstance(rate["schedule"], list) and 0 < len(rate["schedule"]) <= 1000,
                        path + ".rate.schedule: 不能为空或超过1000项")
                previous = None
                for j, node in enumerate(rate["schedule"]):
                    np = f"{path}.rate.schedule[{j}]"
                    fields(node, {"effective_date", "value", "source"}, {"effective_date", "value", "source"}, np)
                    d = day(node["effective_date"], np + ".effective_date")
                    require(previous is None or d > previous, np + ": 变动日期必须严格升序")
                    require(decimal(node["value"], np + ".value") <= 1000, np + ": 利率超过支持范围")
                    text(node["source"], np + ".source")
                    require(node["source"].strip(), np + ".source: 需注明利率变动依据")
                    previous = d
                require(day(rate["schedule"][0]["effective_date"], path) <= start,
                        path + ": 首个利率节点晚于计息起始日")
            elif mode == "lpr":
                fields(rate, {"mode", "unit", "basis", "term", "policy", "anchor_date", "multiplier", "spread_points"},
                       {"mode", "unit", "basis", "term", "policy"}, path + ".rate")
                require(rate["unit"] == "annual_percent", path + ": LPR 为年利率")
                require(rate["term"] in {"one_year", "five_year"}, path + ": LPR 品种无效")
                require(rate["policy"] in {"floating", "fixed"}, path + ": 需区分动态LPR和固定基准日LPR")
                require(start >= date.fromisoformat(rates["records"][0]["date"]), path + ": 起始日超出LPR数据范围")
                if rate["policy"] == "fixed":
                    anchor = day(rate.get("anchor_date"), path + ".rate.anchor_date")
                    require(date.fromisoformat(rates["records"][0]["date"]) <= anchor <= date.fromisoformat(rates["verified_through"]),
                            path + ": LPR基准日超出核验范围")
                else:
                    require("anchor_date" not in rate, path + ": 动态LPR不可指定固定基准日")
                    require(end <= date.fromisoformat(rates["verified_through"]),
                            path + ": 截止日超出LPR核验日期，请更新公开利率数据")
                decimal(rate.get("multiplier", "1"), path + ".rate.multiplier", True)
                decimal(rate.get("spread_points", "0"), path + ".rate.spread_points")
                require(rate_at(rate, start, rates)[0] <= 1000 and rate_at(rate, end, rates)[0] <= 1000,
                        path + ": 换算后的年利率超过支持范围")
            else:
                raise PlanError(path + ".rate.mode: 应为 fixed、schedule 或 lpr")
            require(rate["unit"] in {"annual_percent", "monthly_percent", "daily_percent"}, path + ": 利率单位无效")
            if rate["unit"] == "daily_percent":
                require("basis" not in rate, path + ": 日利率不应再除以年基准")
            else:
                require(type(rate.get("basis")) is int and rate["basis"] in {360, 365, 366}
                        or rate.get("basis") == "actual_actual", path + ": 年基准应为360、365、366或actual_actual")
            if rate["unit"] == "monthly_percent":
                warnings.append(f"{leg['id']}：月利率按月利率×12年化后按实际天数计算，未采用整月结息法。")
    require(ids, "至少需要一项一般债务利息或加倍部分")
    return warnings


def money(n):
    return format(n.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def raw_number(n):
    return format(n, "f")


def calculate_leg(leg, kind, rates):
    start, end = effective_range(leg)
    if start > end:
        return []
    boundaries = {start, end + timedelta(days=1)}
    for payment in leg.get("repayments", []):
        effective = date.fromisoformat(payment["date"]) + timedelta(days=1 if leg["repayment_effective"] == "next_day" else 0)
        boundaries.add(effective)
    for period in leg.get("exclude_periods", []):
        boundaries.update({date.fromisoformat(period["start"]), date.fromisoformat(period["end"]) + timedelta(days=1)})
    rate = leg.get("rate", {"mode": "fixed", "unit": "daily_percent", "value": "0.0175"})
    if rate["mode"] == "lpr" and rate["policy"] == "floating":
        boundaries.update(date.fromisoformat(r["date"]) for r in rates["records"])
    if rate["mode"] == "schedule":
        boundaries.update(date.fromisoformat(r["effective_date"]) for r in rate["schedule"])
    if rate.get("basis") == "actual_actual":
        boundaries.update(date(y, 1, 1) for y in range(start.year + 1, end.year + 1))
    nodes = sorted(d for d in boundaries if start <= d <= end + timedelta(days=1))
    rows = []
    for a, stop in zip(nodes, nodes[1:]):
        principal = balance_at(leg, a)
        if excluded(leg, a) or principal == 0:
            continue
        value, unit = rate_at(rate, a, rates)
        basis = denominator(rate, a)
        annualizer = 12 if unit == "monthly_percent" else 1
        days = (stop - a).days
        interest = principal * value / 100 * annualizer * days / basis
        row = {"kind": kind, "id": leg["id"], "start": a.isoformat(), "end": (stop - timedelta(days=1)).isoformat(),
               "days": days, "principal": raw_number(principal), "rate": raw_number(value), "rate_unit": unit,
               "basis": basis, "interest_raw": raw_number(interest), "interest": money(interest)}
        # Unchanged monthly LPR announcements do not create unnecessary display rows.
        if rows and rows[-1]["end"] == (a - timedelta(days=1)).isoformat() and all(
                rows[-1][k] == row[k] for k in ("principal", "rate", "rate_unit", "basis")):
            last = rows[-1]
            last["end"] = row["end"]
            last["days"] += days
            merged = principal * value / 100 * annualizer * last["days"] / basis
            last["interest_raw"], last["interest"] = raw_number(merged), money(merged)
        else:
            rows.append(row)
    return rows


def calculate(plan, rates):
    with localcontext() as ctx:
        ctx.prec = 60
        warnings = validate_plan(plan, rates)
        rows = [row for kind in ("general", "delay") for leg in plan[kind] for row in calculate_leg(leg, kind, rates)]
        totals = {}
        for kind in ("general", "delay"):
            key = "interest_raw" if plan["rounding"] == "total" else "interest"
            totals[kind] = money(sum((Decimal(row[key]) for row in rows if row["kind"] == kind), Decimal(0)))
        totals["interest"] = money(Decimal(totals["general"]) + Decimal(totals["delay"]))
        has_lpr = any(leg["rate"]["mode"] == "lpr" for leg in plan["general"])
        return {"schema_version": "1.0", "engine_version": VERSION, "plan_sha256": digest(plan),
                "rates_sha256": digest(rates) if has_lpr else None,
                "lpr_snapshot": {k: rates[k] for k in ("source", "verified_through", "latest_announcement")} if has_lpr else None,
                "title": plan["title"], "currency": plan["currency"], "rounding": plan["rounding"],
                "rows": rows, "totals": totals, "warnings": warnings}
