import csv
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from consolidation import run


class SavedRunTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / "run"
        self.validation = self.root / "validation.json"
        self.config = json.loads((ROOT / "examples" / "synthetic.json").read_text(encoding="utf-8"))

    def validate(self):
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "validate_run.py"),
             "--run", str(self.output), "--output", str(self.validation)],
            capture_output=True, text=True, check=False,
        )

    def read_rows(self, name):
        with (self.output / name).open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def replace_rows(self, name, rows):
        path = self.output / name
        with path.open(encoding="utf-8-sig", newline="") as f:
            fields = csv.DictReader(f).fieldnames
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def run_fixture(self):
        return run(ROOT / "examples" / "synthetic.csv", self.config, self.output)

    def test_synthetic_cli_contract(self):
        process = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "consolidation.py"),
             "--input", str(ROOT / "examples" / "synthetic.csv"),
             "--config", str(ROOT / "examples" / "synthetic.json"),
             "--output", str(self.output)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads((self.output / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(result["metadata"]["InputRows"], 10)
        self.assertEqual(result["metadata"]["OutsideGeographyRows"], 1)
        waterfall = {r["Stage"]: r["Shipments"] for r in result["waterfall"]}
        self.assertEqual(waterfall, {
            "Original scope": 9, "Excluded modes": 1, "After mode exclusions": 8,
            "Excluded sites": 1, "After site exclusions": 7,
            "Excluded low frequency: non-site and non-mode only": 3, "Retained": 4,
        })
        expected = {"Daily": (3, "2"), "TueFri": (2, "6"), "Weekly": (1, "16.2")}
        for row in result["summary"]:
            batches, savings = expected[row["Scenario"]]
            self.assertEqual(row["CurrentShipments"], 4)
            self.assertEqual(Decimal(row["ActualCostUSD"]), 54)
            self.assertEqual(row["ConsolidatedShipments"], batches)
            self.assertEqual(Decimal(row["SavingsUSD"]), Decimal(savings))
        observations = {r["WarehouseCode"]: r["Observations"] for r in result["city_mapping"]}
        self.assertEqual(observations, {"SYN_ORIGIN": 8, "SYN_DEST": 9, "SYN_EXCLUDED_A": 1})
        process = self.validate()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(self.validation.read_text())["status"], "PASS")
        self.assertTrue((self.output / "report.html").is_file())

    def test_no_eligible_shipments_is_valid(self):
        self.config["eligibility"]["minimum_shipments_per_calendar_month"] = 999
        result = self.run_fixture()
        self.assertFalse(result["lanes"])
        process = self.validate()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(self.validation.read_text())["eligible_shipments"], 0)

    def test_completely_empty_scope_is_valid(self):
        empty = self.root / "empty.csv"
        with (ROOT / "examples" / "synthetic.csv").open(encoding="utf-8", newline="") as f:
            fields = csv.DictReader(f).fieldnames
        with empty.open("w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()
        run(empty, self.config, self.output)
        process = self.validate()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(self.validation.read_text())["scoped_shipments"], 0)

    def test_empty_single_scenario_ranking_is_valid(self):
        self.config["ranking"]["scenario"] = "Weekly"
        self.config["eligibility"]["minimum_shipments_per_calendar_month"] = 999
        self.run_fixture()
        process = self.validate()
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_nonempty_single_scenario_ranking_is_valid(self):
        self.config["ranking"]["scenario"] = "Weekly"
        self.run_fixture()
        process = self.validate()
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_missing_nonempty_rankings_fail(self):
        self.run_fixture()
        self.replace_rows("top5.csv", [])
        process = self.validate()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Ranking", process.stderr)
        self.assertFalse(self.validation.exists())

    def test_missing_nonempty_ranking_scenario_fails(self):
        self.run_fixture()
        rows = [r for r in self.read_rows("top5.csv") if r["RankingScenario"] != "Weekly"]
        self.replace_rows("top5.csv", rows)
        process = self.validate()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Ranking", process.stderr)

    def test_duplicate_ranked_lane_fails(self):
        self.run_fixture()
        rows = self.read_rows("top5.csv")
        self.replace_rows("top5.csv", rows + [rows[0]])
        process = self.validate()
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(self.validation.exists())

    def test_wrong_rank_number_fails(self):
        self.run_fixture()
        rows = self.read_rows("top5.csv")
        rows[0]["Rank"] = "2"
        self.replace_rows("top5.csv", rows)
        process = self.validate()
        self.assertNotEqual(process.returncode, 0)

    def test_wrong_ranked_savings_fails(self):
        self.run_fixture()
        rows = self.read_rows("top5.csv")
        rows[0]["SavingsUSD"] = "999"
        self.replace_rows("top5.csv", rows)
        process = self.validate()
        self.assertNotEqual(process.returncode, 0)

    def test_missing_lanes_with_retained_shipments_fail(self):
        self.run_fixture()
        self.replace_rows("lanes.csv", [])
        self.replace_rows("top5.csv", [])
        process = self.validate()
        self.assertNotEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
