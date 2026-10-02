"""Fetch PUBLIC LPR history only. Never accepts case data. HTTPS verification stays enabled."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from calculator import ROOT, PlanError, day, load_rates, require

SOURCE = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/LprHis"


def refresh(through, output):
    end = day(through, "through")
    require(end <= date.today(), "核验日期不能晚于实际日期")
    cursor = date(2019, 8, 20)
    collected = {}
    while cursor <= end:
        stop = min(cursor + timedelta(days=350), end)
        url = SOURCE + "?" + urlencode({"lang": "CN", "strStartDate": cursor.isoformat(), "strEndDate": stop.isoformat()})
        process = subprocess.run(
            ["curl", "--fail", "--silent", "--show-error", "--max-time", "30", "-X", "POST", url],
            capture_output=True, text=True, check=False)
        require(process.returncode == 0, "官方LPR请求失败；保留原有数据，未关闭HTTPS证书核验")
        try:
            response = json.loads(process.stdout)
        except json.JSONDecodeError as e:
            raise PlanError("官方接口返回非JSON内容，原有数据未修改") from e
        require(response.get("head", {}).get("rep_code") == "200", "官方接口状态无效")
        records = response.get("records")
        require(isinstance(records, list) and records, "官方接口未提供所请求期间的LPR记录")
        for record in records:
            d = record.get("showDateCN")
            require(cursor <= day(d, "official.date") <= stop, "官方记录日期超出请求范围")
            row = {"date": d, "one_year": record.get("1Y"), "five_year": record.get("5Y")}
            require(d not in collected or collected[d] == row, "同一公告日期的利率冲突")
            collected[d] = row
        cursor = stop + timedelta(days=1)
    rows = sorted(collected.values(), key=lambda x: x["date"])
    require(rows[0]["date"] == "2019-08-20", "官方历史记录起点缺失")
    # A missing monthly announcement must not silently extend the preceding quote.
    months = {(date.fromisoformat(r["date"]).year, date.fromisoformat(r["date"]).month) for r in rows}
    year, month = 2019, 8
    while (year, month) < (end.year, end.month):
        require((year, month) in months, "官方LPR历史存在整月缺口，原有数据未修改")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if end.day >= 28:
        require((end.year, end.month) in months, "本月LPR公告缺失，原有数据未修改")
    data = {"schema_version": "1.0", "source": SOURCE, "verified_through": through,
            "latest_announcement": rows[-1]["date"], "records": rows}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".pending.json")
    require(not temporary.exists(), "临时数据文件已存在，请先检查")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        load_rates(temporary)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"status": "updated", "records": len(rows), "verified_through": through, "output": str(output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="只更新公开LPR数据，不读取案件信息；需要系统curl")
    parser.add_argument("--through", required=True, help="实际完成核验的日期 YYYY-MM-DD")
    parser.add_argument("--output", default=str(ROOT / "data" / "lpr.json"))
    args = parser.parse_args()
    try:
        print(json.dumps(refresh(args.through, args.output), ensure_ascii=False))
    except (PlanError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        raise SystemExit(2)
