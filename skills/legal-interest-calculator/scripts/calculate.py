"""Command-line entry point: validate -> calculate -> export -> independently verify."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from calculator import PlanError, calculate, load_rates, read_json, require
from exports import export_docx, export_html, export_xlsx, json_file
from verify import verify_math


def run(input_path, output_dir, rates_path=None, formats="json,html,docx,xlsx", force=False):
    requested = set(formats.split(","))
    require(requested <= {"json", "html", "docx", "xlsx"} and bool(requested), "formats: 只支持 json,html,docx,xlsx")
    plan, rates = read_json(input_path), load_rates(rates_path)
    result = calculate(plan, rates)
    verification = verify_math(plan, result, rates)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".interest-export-", dir=output) as tmp:
        staging = Path(tmp)
        json_file(staging / "source-plan.json", plan)
        json_file(staging / "interest-result.json", result)
        if result["lpr_snapshot"]:
            json_file(staging / "lpr-snapshot.json", rates)
        for extension, exporter in (("html", export_html), ("docx", export_docx), ("xlsx", export_xlsx)):
            if extension in requested:
                exporter(staging / ("interest-report." + extension), plan, result)
        manifest = {"schema_version": "1.0", "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(staging.iterdir())}}
        json_file(staging / "manifest.json", manifest)
        from verify import verify_artifacts
        verification["checked_files"] = verify_artifacts(staging, result)
        json_file(staging / "verification.json", verification)
        names = {p.name for p in staging.iterdir()}
        require(force or not any((output / name).exists() for name in names), "输出文件已存在，请使用新的输出目录或明确指定 --force")
        # Remove obsolete known exports only when replacing a previous bundle was requested.
        if force:
            known = {"lpr-snapshot.json", "interest-report.html", "interest-report.docx", "interest-report.xlsx"}
            for name in known - names:
                p = output / name
                if p.is_file():
                    p.unlink()
        for path in sorted(staging.iterdir()):
            path.replace(output / path.name)
    return {"status": "passed", "totals": result["totals"], "checked_days": verification["checked_days"],
            "output_dir": str(output), "files": sorted(names)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="根据已确定的计息参数，在本地计算、导出并独立核验")
    parser.add_argument("--input", required=True, help="计息参数JSON")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--rates", help="指定公开LPR快照")
    parser.add_argument("--formats", default="json,html,docx,xlsx")
    parser.add_argument("--force", action="store_true", help="替换此目录内同名工具输出")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.input, args.output_dir, args.rates, args.formats, args.force), ensure_ascii=False))
    except (PlanError, OSError, ValueError, TypeError, KeyError) as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        raise SystemExit(2)
