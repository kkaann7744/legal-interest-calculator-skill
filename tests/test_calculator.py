"""Financial boundary, deterministic export and tampering tests using fictitious data."""
import copy
import hashlib
import json
import random
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

SKILL = Path(__file__).resolve().parents[1] / "skills" / "legal-interest-calculator"
sys.path.insert(0, str(SKILL / "scripts"))
from calculator import PlanError, calculate, load_rates, read_json
from calculate import run
from exports import json_file, write_zip
from verify import verify_bundle, verify_math


class InterestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rates = load_rates()

    def setUp(self):
        self.plan = read_json(SKILL / "examples" / "fixed-repayment.json")

    def result(self):
        result = calculate(self.plan, self.rates)
        verify_math(self.plan, result, self.rates)
        return result

    def simple(self, start="2024-01-01", end="2024-01-10", unit="daily_percent", value="0.05", basis=None):
        leg = self.plan["general"][0]
        leg.update(principal="100000", start=start, end=end, repayments=[])
        leg["rate"] = {"mode": "fixed", "unit": unit, "value": value}
        if basis is not None:
            leg["rate"]["basis"] = basis
        self.plan["delay"] = []
        return leg

    def test_fixed_repayment_golden(self):
        result = self.result()
        self.assertEqual(result["totals"], {"general": "800.00", "delay": "52.50", "interest": "852.50"})
        self.assertEqual([r["days"] for r in result["rows"]], [5, 5, 5])

    def test_same_day_repayment(self):
        self.plan["general"][0]["repayment_effective"] = "same_day"
        self.assertEqual(self.result()["totals"]["general"], "760.00")

    def test_rate_and_repayment_same_day(self):
        self.plan = read_json(SKILL / "examples" / "lpr-floating.json")
        result = self.result()
        self.assertEqual([r["start"] for r in result["rows"]], ["2024-07-01", "2024-07-22", "2024-10-21"])
        self.assertEqual([r["days"] for r in result["rows"]], [21, 91, 12])
        self.assertEqual([r["principal"] for r in result["rows"]], ["1000000", "900000", "900000"])
        self.assertEqual([r["rate"] for r in result["rows"]], ["3.45", "3.35", "3.10"])
        self.assertEqual(result["totals"]["interest"], "10419.04")

    def test_fixed_lpr_does_not_float(self):
        self.plan = read_json(SKILL / "examples" / "lpr-floating.json")
        self.plan["general"][0]["rate"].update(policy="fixed", anchor_date="2024-07-01")
        self.assertEqual([r["rate"] for r in self.result()["rows"]], ["3.45", "3.45"])

    def test_lpr_multiplier_and_spread(self):
        leg = self.simple("2024-07-01", "2024-07-01")
        leg["rate"] = {"mode": "lpr", "unit": "annual_percent", "basis": 365, "term": "one_year",
                       "policy": "fixed", "anchor_date": "2024-07-01", "multiplier": "2", "spread_points": "1"}
        self.assertEqual(self.result()["rows"][0]["rate"], "7.90")

    def test_day_count_all_modes(self):
        leg = self.simple("2024-01-01", "2024-01-03")
        for rule, expected in {"both": "150.00", "exclude_start": "100.00", "exclude_end": "100.00", "exclude_both": "50.00"}.items():
            with self.subTest(rule=rule):
                leg["day_count"] = rule
                self.assertEqual(self.result()["totals"]["general"], expected)

    def test_one_day_empty_adjusted_ranges(self):
        leg = self.simple("2024-01-01", "2024-01-01")
        for rule in ("exclude_start", "exclude_end", "exclude_both"):
            leg["day_count"] = rule
            self.assertEqual(self.result()["totals"]["general"], "0.00")

    def test_daily_rate_not_divided_again(self):
        self.simple()
        self.assertEqual(self.result()["totals"]["general"], "500.00")
        self.plan["general"][0]["rate"]["basis"] = 365
        with self.assertRaises(PlanError):
            self.result()

    def test_monthly_rate_annualization(self):
        self.simple("2024-01-01", "2024-01-30", "monthly_percent", "1", 360)
        self.assertEqual(self.result()["totals"]["general"], "1000.00")
        self.assertTrue(self.result()["warnings"])

    def test_actual_actual_across_leap_year(self):
        leg = self.simple("2023-12-31", "2024-01-01", "annual_percent", "1", "actual_actual")
        leg["principal"] = "133590"
        result = self.result()
        self.assertEqual([r["basis"] for r in result["rows"]], [365, 366])
        self.assertEqual(result["totals"]["general"], "7.31")

    def test_leap_day_counts(self):
        self.simple("2024-02-28", "2024-03-01")
        self.assertEqual(self.result()["rows"][0]["days"], 3)

    def test_schedule(self):
        leg = self.simple()
        leg["rate"] = {"mode": "schedule", "unit": "daily_percent",
                       "schedule": [{"effective_date": "2024-01-01", "value": "0.05", "source": "虚构合同"},
                                    {"effective_date": "2024-01-06", "value": "0.10", "source": "虚构补充协议"}]}
        self.assertEqual(self.result()["totals"]["general"], "750.00")

    def test_exclusions_and_repayment(self):
        self.plan = read_json(SKILL / "examples" / "delay-exclusions.json")
        self.assertEqual(self.result()["totals"]["delay"], "84.00")

    def test_overlapping_exclusions_union(self):
        self.plan = read_json(SKILL / "examples" / "delay-exclusions.json")
        self.plan["delay"][0]["exclude_periods"] = [
            {"start": "2024-01-03", "end": "2024-01-04", "reason": "虚构", "source": "虚构裁定"},
            {"start": "2024-01-04", "end": "2024-01-06", "reason": "虚构", "source": "虚构裁定"}]
        self.assertEqual(self.result()["totals"]["delay"], "77.00")

    def test_full_repayment_zero_balance(self):
        leg = self.simple()
        leg["repayments"] = [{"date": "2024-01-05", "amount": "100000", "source": "虚构还本"}]
        self.assertEqual(self.result()["totals"]["general"], "250.00")

    def test_distinct_same_day_repayments(self):
        leg = self.simple()
        leg["repayments"] = [{"id": "P1", "date": "2024-01-05", "amount": "20000", "source": "虚构凭证1"},
                             {"id": "P2", "date": "2024-01-05", "amount": "20000", "source": "虚构凭证2"}]
        self.assertEqual(self.result()["totals"]["general"], "400.00")

    def test_rounding_total_vs_segment(self):
        leg = self.simple("2024-01-01", "2024-01-01", value="0.5")
        leg["principal"] = "1"
        second = copy.deepcopy(leg)
        second["id"] = "G2"
        self.plan["general"].append(second)
        self.assertEqual(self.result()["totals"]["general"], "0.01")
        self.plan["rounding"] = "segment"
        self.assertEqual(self.result()["totals"]["general"], "0.02")

    def test_unknown_field_rejected(self):
        self.plan["general"][0]["repayment_effectiv"] = "same_day"
        with self.assertRaises(PlanError):
            self.result()

    def test_missing_basis_rejected(self):
        del self.plan["general"][0]["rate"]["basis"]
        with self.assertRaises(PlanError):
            self.result()

    def test_draft_and_unresolved_rejected(self):
        self.plan["analysis"]["status"] = "draft"
        with self.assertRaises(PlanError):
            self.result()
        self.plan["analysis"].update(status="ready", unresolved=["首尾日未确认"])
        with self.assertRaises(PlanError):
            self.result()

    def test_over_repayment_rejected(self):
        self.plan["general"][0]["repayments"][0]["amount"] = "100000.01"
        with self.assertRaises(PlanError):
            self.result()

    def test_duplicate_repayment_id_rejected(self):
        payment = self.plan["general"][0]["repayments"][0]
        self.plan["general"][0]["repayments"].append(copy.deepcopy(payment))
        with self.assertRaises(PlanError):
            self.result()

    def test_invalid_dates_and_float_rejected(self):
        self.plan["general"][0]["start"] = "2024-02-30"
        with self.assertRaises(PlanError):
            self.result()
        self.setUp()
        self.plan["general"][0]["principal"] = 100000.01
        with self.assertRaises(PlanError):
            self.result()

    def test_unverified_lpr_rejected(self):
        self.plan = read_json(SKILL / "examples" / "lpr-floating.json")
        self.plan["general"][0]["end"] = "2026-12-31"
        with self.assertRaises(PlanError):
            self.result()

    def test_pre_2014_delay_rejected(self):
        self.plan["delay"][0].update(start="2014-07-31", end="2014-08-10")
        with self.assertRaises(PlanError):
            self.result()

    def test_independent_checker_detects_tampering(self):
        result = self.result()
        result["rows"][0]["days"] += 1
        with self.assertRaises(PlanError):
            verify_math(self.plan, result, self.rates)

    def test_random_repayment_boundary_cases(self):
        rng = random.Random(20261002)
        for _ in range(50):
            leg = self.simple("2024-02-20", "2024-03-10", "annual_percent", str(rng.randint(0, 35)), rng.choice((360, 365, 366, "actual_actual")))
            leg["principal"] = str(rng.randint(1000, 10000000))
            leg["day_count"] = rng.choice(("both", "exclude_start", "exclude_end", "exclude_both"))
            leg["repayment_effective"] = rng.choice(("same_day", "next_day"))
            leg["repayments"] = [{"date": (date(2024, 2, 20) + timedelta(days=rng.randint(0, 19))).isoformat(),
                                  "amount": str(int(leg["principal"]) // 3), "source": "虚构随机还本"}]
            self.result()

    def test_full_exports_offline_and_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            json_file(path / "plan.json", self.plan)
            with patch("socket.socket", side_effect=AssertionError("calculation must be offline")):
                run(path / "plan.json", path / "one")
                run(path / "plan.json", path / "two")
                self.assertEqual(verify_bundle(path / "one")["status"], "passed")
            for file in (path / "one").iterdir():
                self.assertEqual(file.read_bytes(), (path / "two" / file.name).read_bytes(), file.name)

    def test_office_tampering_even_with_updated_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            json_file(path / "plan.json", self.plan)
            run(path / "plan.json", path / "out")
            target = path / "out" / "interest-report.xlsx"
            with ZipFile(target) as z:
                members = {name: z.read(name) for name in z.namelist()}
            members["xl/worksheets/sheet1.xml"] = members["xl/worksheets/sheet1.xml"].replace(b"<v>500.00</v>", b"<v>501.00</v>")
            write_zip(target, members)
            manifest = read_json(path / "out" / "manifest.json")
            manifest["files"][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
            json_file(path / "out" / "manifest.json", manifest)
            with self.assertRaises(PlanError):
                verify_bundle(path / "out")

    def test_existing_outputs_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            json_file(path / "plan.json", self.plan)
            run(path / "plan.json", path / "out")
            with self.assertRaises(PlanError):
                run(path / "plan.json", path / "out")

    def test_excel_semantic_changes_with_updated_checksum(self):
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        changes = {"category": ("A8", "s:is/s:t", "加倍部分"),
                   "unit": ("H8", "s:is/s:t", "月"),
                   "basis": ("I8", "s:v", "360"),
                   "rounding_formula": ("K8", "s:f", "ROUND(J8,0)"),
                   "principal_formula": ("F8", None, "1"),
                   "extra_row": (None, None, None)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            json_file(path / "plan.json", self.plan)
            run(path / "plan.json", path / "out")
            target = path / "out" / "interest-report.xlsx"
            with ZipFile(target) as z:
                original = {name: z.read(name) for name in z.namelist()}
            for label, (ref, child, value) in changes.items():
                with self.subTest(change=label):
                    members = original.copy()
                    root = ET.fromstring(members["xl/worksheets/sheet1.xml"])
                    if ref is None:
                        row = copy.deepcopy(root.find(".//s:row[@r='8']", ns))
                        row.set("r", "99")
                        for cell in row:
                            cell.set("r", cell.get("r")[:-1] + "99")
                        root.find("s:sheetData", ns).append(row)
                    else:
                        cell = root.find(f".//s:c[@r='{ref}']", ns)
                        if child:
                            cell.find(child, ns).text = value
                        else:
                            ET.SubElement(cell, "{" + ns["s"] + "}f").text = value
                    members["xl/worksheets/sheet1.xml"] = ET.tostring(root)
                    write_zip(target, members)
                    manifest = read_json(path / "out" / "manifest.json")
                    manifest["files"][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
                    json_file(path / "out" / "manifest.json", manifest)
                    with self.assertRaises(PlanError):
                        verify_bundle(path / "out")

    def test_check_only_validates_without_creating_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            json_file(path / "plan.json", self.plan)
            command = [sys.executable, str(SKILL / "scripts" / "calculate.py"),
                       "--input", str(path / "plan.json"), "--check-only"]
            checked = subprocess.run(command, capture_output=True, text=True, cwd=path)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            report = json.loads(checked.stdout)
            self.assertEqual(report["status"], "valid")
            self.assertNotIn("totals", report)
            self.assertEqual(list(path.iterdir()), [path / "plan.json"])
            for change in ("missing_basis", "unresolved"):
                invalid = copy.deepcopy(self.plan)
                if change == "missing_basis":
                    del invalid["general"][0]["rate"]["basis"]
                else:
                    invalid["analysis"].update(status="draft", unresolved=["首尾日未明确"])
                json_file(path / "plan.json", invalid)
                checked = subprocess.run(command, capture_output=True, text=True, cwd=path)
                self.assertEqual(checked.returncode, 2)
                self.assertEqual(json.loads(checked.stdout)["status"], "error")
                self.assertEqual(list(path.iterdir()), [path / "plan.json"])

    def test_repayment_rule_only_required_with_payments(self):
        leg = self.simple()
        del leg["repayment_effective"]
        self.assertEqual(self.result()["totals"]["general"], "500.00")
        leg["repayments"] = [{"date": "2024-01-05", "amount": "1", "source": "虚构还本"}]
        with self.assertRaises(PlanError):
            self.result()

    def test_blank_repayment_source_rejected(self):
        self.plan["general"][0]["repayments"][0]["source"] = " "
        with self.assertRaises(PlanError):
            self.result()

    def test_failed_reverification_replaces_stale_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            json_file(path / "plan.json", self.plan)
            run(path / "plan.json", path / "out")
            result_path = path / "out" / "interest-result.json"
            result = read_json(result_path)
            result["totals"]["interest"] = "0.00"
            json_file(result_path, result)
            checked = subprocess.run([sys.executable, str(SKILL / "scripts" / "verify.py"),
                                      "--bundle", str(path / "out")], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 2)
            self.assertEqual(read_json(path / "out" / "verification.json")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
