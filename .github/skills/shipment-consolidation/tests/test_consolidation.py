import csv
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from consolidation import AnalysisError, analyze, expected_key, load_shipments, make_batch, period_info, run

ROOT = Path(__file__).resolve().parents[1]


def profile():
    config = json.loads((ROOT / "examples" / "project-example.json").read_text())
    config["geography"]["input_is_geography_prefiltered"] = True
    config["geography"]["prefilter_evidence"] = "Fabricated unit-test input; not evidence for a real extract."
    config["eligibility"]["minimum_shipments_per_calendar_month"] = 0
    for spec in config["scenarios"].values():
        spec["source"] = "derive"
    return config


def shipment(load="A", weight="10", cost="10", mode="LTL", origin="O", destination="D", day="2026-01-01"):
    from datetime import date
    config = profile()
    return {
        "Load": load, "WeightLB": D(weight), "CostUSD": D(cost), "Mode": mode,
        "Origin": origin, "Destination": destination, "Date": day,
        "OriginCityRaw": "Origin City", "DestinationCityRaw": "Destination City",
        **{s: expected_key(date.fromisoformat(day), spec["rule"]) for s, spec in config["scenarios"].items()},
    }


def metadata():
    return {"MonthDenominator": 8, "CalendarMonths": [f"2026-{m:02}" for m in range(1, 9)], "PartialMonths": []}


class ConsolidationTests(unittest.TestCase):
    def batch(self, rows, policy="lowest_rate_among_highest_weight"):
        return make_batch("Daily", ("O", "D", "LTL", "key"), rows, policy)

    def test_heaviest_not_lowest_batch_rate(self):
        b = self.batch([shipment("heavy", "10", "20"), shipment("light", "5", "1")])
        self.assertEqual(b["ReferenceLoad"], "heavy")
        self.assertEqual(b["EstimatedCostUSD"], D("30"))
        self.assertEqual(b["SavingsUSD"], D("-9"))

    def test_lowest_rate_only_among_max_weight_ties(self):
        b = self.batch([shipment("A", "10", "20"), shipment("B", "10", "10"), shipment("C", "5", "1")])
        self.assertEqual(b["ReferenceLoad"], "B")
        self.assertEqual(b["ReferenceRateUSDperLB"], 1)

    def test_ties_block_when_unconfirmed(self):
        with self.assertRaisesRegex(AnalysisError, "ask"):
            self.batch([shipment("A", "10", "20"), shipment("B", "10", "10")], "require_confirmation")

    def test_equal_rate_tie_trace_identifier(self):
        b = self.batch([shipment("Z"), shipment("A")])
        self.assertEqual(b["ReferenceLoad"], "A")

    def test_singleton_exact_zero(self):
        b = self.batch([shipment(weight="3", cost="1")])
        self.assertEqual(b["SavingsUSD"], 0)

    def test_zero_months_threshold_and_boundary(self):
        config = profile()
        config["eligibility"]["minimum_shipments_per_calendar_month"] = 10
        rows = [shipment(str(i)) for i in range(80)]
        result = analyze(rows, config, metadata())
        self.assertTrue(result["frequency"][0]["Eligible"])
        self.assertEqual(result["frequency"][0]["AverageShipmentsPerMonth"], 10)
        self.assertEqual(result["frequency"][0]["2026-02"], 0)
        self.assertFalse(analyze(rows[:-1], config, metadata())["frequency"][0]["Eligible"])

    def test_lane_total_cannot_rescue_modes(self):
        config = profile()
        config["eligibility"]["minimum_shipments_per_calendar_month"] = 10
        rows = [shipment(f"{mode}{i}", mode=mode) for mode in ("LTL", "Parcel") for i in range(40)]
        result = analyze(rows, config, metadata())
        self.assertTrue(all(not r["Eligible"] for r in result["frequency"]))

    def test_site_exclusion_has_priority(self):
        config = profile()
        config["eligibility"]["minimum_shipments_per_calendar_month"] = 10
        rows = [shipment("N", origin="SYN_EXCLUDED_A"), shipment("F")]
        waterfall = {r["Stage"]: r for r in analyze(rows, config, metadata())["waterfall"]}
        self.assertEqual(waterfall["Excluded sites"]["Shipments"], 1)
        self.assertEqual(waterfall["Excluded low frequency: non-site and non-mode only"]["Shipments"], 1)
        self.assertEqual(waterfall["Retained"]["Shipments"], 0)

    def test_site_exclusion_applies_at_both_lane_ends(self):
        config = profile()
        config["eligibility"]["minimum_shipments_per_calendar_month"] = 0
        rows = [
            shipment("origin-site", origin="SYN_EXCLUDED_A"),
            shipment("destination-site", destination="SYN_EXCLUDED_B"),
            shipment("ordinary", origin="O", destination="D"),
        ]
        result = analyze(rows, config, metadata())
        waterfall = {r["Stage"]: r for r in result["waterfall"]}
        self.assertEqual(waterfall["Excluded sites"]["Shipments"], 2)
        self.assertEqual(waterfall["Excluded sites"]["ActualCostUSD"], D("20"))
        decisions = {r["Load"]: r["Decision"] for r in result["eligibility_decisions"]}
        self.assertEqual(decisions, {
            "origin-site": "ExcludedSite",
            "destination-site": "ExcludedSite",
            "ordinary": "Eligible",
        })

    def test_never_mix_modes(self):
        rows = [shipment("A", mode="LTL"), shipment("B", mode="Parcel")]
        result = analyze(rows, profile(), metadata())
        self.assertTrue(all(r["ConsolidatedShipments"] == 2 for r in result["summary"]))

    def test_excluded_modes_precede_sites_frequency_and_batches(self):
        config = profile()
        config["eligibility"]["minimum_shipments_per_calendar_month"] = 0
        rows = [
            shipment("air-origin-site", mode=" AIR ", origin="SYN_EXCLUDED_A", cost="12"),
            shipment("air", mode="aIr", cost="8"),
            shipment("ground", mode="LTL", cost="10"),
        ]
        result = analyze(rows, config, metadata())
        waterfall = {r["Stage"]: r for r in result["waterfall"]}
        self.assertEqual(waterfall["Excluded modes"]["Shipments"], 2)
        self.assertEqual(waterfall["Excluded modes"]["ActualCostUSD"], D("20"))
        self.assertEqual(waterfall["Excluded sites"]["Shipments"], 0)
        self.assertEqual(waterfall["After mode exclusions"]["Shipments"], 1)
        decisions = {r["Load"]: r["Decision"] for r in result["eligibility_decisions"]}
        self.assertEqual(decisions, {
            "air-origin-site": "ExcludedMode",
            "air": "ExcludedMode",
            "ground": "Eligible",
        })
        self.assertEqual({r["Mode"] for r in result["frequency"]}, {"LTL"})
        self.assertEqual({r["Mode"] for r in result["batches"]}, {"LTL"})

    def test_excluded_modes_is_configurable_to_empty(self):
        config = profile()
        config["eligibility"]["excluded_modes"] = []
        result = analyze([shipment("air", mode="Air")], config, metadata())
        self.assertEqual(result["eligibility_decisions"][0]["Decision"], "Eligible")

    def test_configured_excluded_modes_require_a_rationale(self):
        config = profile()
        config["eligibility"].pop("excluded_modes_rationale")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path)
            with self.assertRaisesRegex(AnalysisError, "rationale"):
                load_shipments(path, config)

    def test_daily_keys_include_year_month_and_date(self):
        from datetime import date
        days = (date(2026, 1, 14), date(2026, 7, 14), date(2027, 1, 14), date(2024, 2, 29))
        self.assertEqual([expected_key(day, "calendar_date") for day in days],
                         ["2026-01-14", "2026-07-14", "2027-01-14", "2024-02-29"])
        with self.assertRaisesRegex(AnalysisError, "Unknown batch rule"):
            expected_key(days[0], "legacy_year_day_of_month")

    def test_daily_consolidates_same_date_without_cross_month_pooling(self):
        rows = [
            shipment("heavy", "10", "20", day="2026-01-14"),
            shipment("light", "5", "12", day="2026-01-14"),
            shipment("july", "2", "8", day="2026-07-14"),
        ]
        result = analyze(rows, profile(), metadata())
        daily = next(r for r in result["summary"] if r["Scenario"] == "Daily")
        self.assertEqual(daily["CurrentShipments"], 3)
        self.assertEqual(daily["ConsolidatedShipments"], 2)
        self.assertEqual(daily["ActualCostUSD"], D("40"))
        self.assertEqual(daily["EstimatedCostUSD"], D("38"))
        self.assertEqual(daily["SavingsUSD"], D("2"))
        self.assertEqual(daily["CrossMonthBatches"], 0)
        members = [r for r in result["members"] if r["Scenario"] == "Daily"]
        self.assertEqual({r["Load"]: r["BatchKey"] for r in members},
                         {"heavy": "2026-01-14", "light": "2026-01-14", "july": "2026-07-14"})

    def test_daily_same_date_keeps_lane_and_mode_boundaries(self):
        rows = [
            shipment("A"), shipment("B"),
            shipment("mode", mode="Parcel"),
            shipment("origin", origin="OTHER"),
            shipment("destination", destination="OTHER"),
        ]
        daily = [r for r in analyze(rows, profile(), metadata())["batches"] if r["Scenario"] == "Daily"]
        self.assertEqual(len(daily), 4)
        self.assertEqual(sorted(r["OriginalShipments"] for r in daily), [1, 1, 1, 2])
        self.assertEqual({r["BatchKey"] for r in daily}, {"2026-01-01"})

    def test_analyze_rejects_non_calendar_daily_key(self):
        row = shipment()
        row["Daily"] = "2026001"
        with self.assertRaisesRegex(AnalysisError, "Daily key must equal"):
            analyze([row], profile(), metadata())

    def test_tuesday_friday_boundaries(self):
        from datetime import date
        rule = "saturday_tuesday_wednesday_friday"
        self.assertEqual(expected_key(date(2026, 8, 1), rule), "202632Batch1")
        self.assertEqual(expected_key(date(2026, 8, 4), rule), "202632Batch1")
        self.assertEqual(expected_key(date(2026, 8, 5), rule), "202632Batch2")
        self.assertEqual(expected_key(date(2026, 8, 7), rule), "202632Batch2")
        self.assertEqual(expected_key(date(2026, 8, 8), rule), "202633Batch1")

    def test_calendar_year_week_split(self):
        from datetime import date
        rule = "saturday_friday_calendar_year_weeknum"
        self.assertEqual(expected_key(date(2026, 1, 1), rule), "202601Batch1")
        self.assertEqual(expected_key(date(2026, 1, 3), rule), "202602Batch1")
        self.assertEqual(expected_key(date(2027, 1, 1), rule), "202701Batch1")

    def test_full_months_including_leap_february(self):
        p = deepcopy(profile()["period"])
        p.update(start="2024-02-01", end="2024-02-29")
        self.assertEqual(period_info(p)[2:], (["2024-02"], []))

    def test_partial_month_requires_decision(self):
        p = profile()["period"]
        p["start"] = "2026-01-15"
        with self.assertRaisesRegex(AnalysisError, "Partial calendar"):
            period_info(p)
        p.update(partial_month_policy="count_each_touched_month", partial_month_confirmation="User chose whole touched months.")
        self.assertEqual(len(period_info(p)[2]), 8)

    def test_empty_scope_no_fake_savings(self):
        result = analyze([], profile(), metadata())
        self.assertEqual(len(result["summary"]), 3)
        self.assertTrue(all(r["SavingsUSD"] == 0 and r["SavingsPct"] is None for r in result["summary"]))
        self.assertTrue(all(not rows for rows in result["top5"].values()))

    def write_input(self, path, changes=None, duplicate=False):
        row = {"Load": "001", "Date": "2026-01-01T23:59:59", "Origin": "O", "Destination": "D",
               "OriginCity": "Origin City", "DestinationCity": "Destination City",
               "Mode": "LTL", "CostUSD": "10", "WeightLB": "10"}
        row.update(changes or {})
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row)
            writer.writeheader()
            writer.writerow(row)
            if duplicate:
                writer.writerow(row)

    def test_invalid_inputs_stop(self):
        for changes in ({"WeightLB": "0"}, {"WeightLB": "-1"}, {"CostUSD": "0"}, {"CostUSD": "-3"},
                        {"CostUSD": "NaN"}, {"WeightLB": "Infinity"}, {"Mode": ""}, {"Date": "bad"},
                        {"Date": "2026-01-01T00:00:00+01:00"}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "input.csv"
                self.write_input(path, changes)
                with self.assertRaises(AnalysisError):
                    load_shipments(path, profile())

    def test_duplicate_ids_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path, duplicate=True)
            with self.assertRaisesRegex(AnalysisError, "Duplicate"):
                load_shipments(path, profile())

    def test_geography_filters_actual_countries(self):
        config = profile()
        config["geography"]["input_is_geography_prefiltered"] = False
        config["columns"].update(origin_country="PickCountry", destination_country="DropCountry")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path, {"PickCountry": "DE", "DropCountry": "FR"})
            rows, info = load_shipments(path, config)
            self.assertFalse(rows)
            self.assertEqual(info["OutsideGeographyRows"], 1)

    def test_city_names_are_added_without_changing_warehouse_lane_keys(self):
        rows = [
            shipment("A", origin="WH1", destination="WH2"),
            shipment("B", origin="WH1", destination="WH2"),
        ]
        result = analyze(rows, profile(), metadata())
        self.assertEqual(len(result["lanes"]), 3)
        lane = next(r for r in result["lanes"] if r["Scenario"] == "Daily")
        self.assertEqual((lane["Origin"], lane["Destination"]), ("WH1", "WH2"))
        self.assertEqual((lane["OriginCity"], lane["DestinationCity"]), ("Origin City", "Destination City"))
        self.assertEqual(lane["OriginCityMappingStatus"], "VERIFIED_FROM_SHIPMENT_DATA")
        self.assertIn("OriginCity", result["top5"]["Daily"][0])
        self.assertIn("DestinationCity", result["batches"][0])

    def test_conflicting_city_names_flag_mapping_and_do_not_guess(self):
        rows = [
            shipment("A", origin="WH1", destination="WH2"),
            shipment("B", origin="WH1", destination="WH2"),
        ]
        rows[1]["OriginCityRaw"] = "Conflicting City"
        result = analyze(rows, profile(), metadata())
        lane = next(r for r in result["lanes"] if r["Scenario"] == "Daily")
        self.assertEqual(lane["OriginCity"], "")
        self.assertEqual(lane["OriginCityMappingStatus"], "CONFLICT")
        audit = next(r for r in result["city_mapping"] if r["WarehouseCode"] == "WH1")
        self.assertEqual(audit["MappingStatus"], "CONFLICT")
        self.assertIn("Conflicting City", audit["ObservedCityValues"])
        self.assertEqual(len({(r["Origin"], r["Destination"]) for r in result["lanes"]}), 1)

    def test_missing_city_mapping_is_flagged(self):
        row = shipment()
        row["OriginCityRaw"] = ""
        row["DestinationCityRaw"] = ""
        result = analyze([row], profile(), metadata())
        self.assertTrue(all(r["MappingStatus"] == "MISSING" for r in result["city_mapping"]))
        self.assertTrue(all(r["OriginCityMappingStatus"] == "MISSING" for r in result["lanes"]))

    def test_city_observations_count_repeated_names_and_missing_values(self):
        rows = [shipment(str(i)) for i in range(4)]
        rows[1]["OriginCityRaw"] = "origin city"
        rows[2]["OriginCityRaw"] = ""
        rows[3]["OriginCityRaw"] = ""
        result = analyze(rows, profile(), metadata())
        audit = {r["WarehouseCode"]: r for r in result["city_mapping"]}
        self.assertEqual(audit["O"]["Observations"], 4)
        self.assertEqual(audit["O"]["MissingCitySourceRows"], 2)
        self.assertEqual(audit["O"]["MappingStatus"], "VERIFIED_FROM_SHIPMENT_DATA")
        self.assertEqual(audit["D"]["Observations"], 4)
        self.assertEqual(audit["D"]["MissingCitySourceRows"], 0)

    def test_city_observations_count_both_endpoint_roles(self):
        rows = [shipment("A", origin="SAME", destination="SAME"),
                shipment("B", origin="SAME", destination="SAME")]
        for row in rows:
            row["OriginCityRaw"] = row["DestinationCityRaw"] = "Same City"
        audit = analyze(rows, profile(), metadata())["city_mapping"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["Observations"], 4)
        self.assertEqual(audit[0]["City"], "Same City")

    def test_verified_location_mapping_file_preserves_conflicts_and_observation_counts(self):
        config = profile()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "locations.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=("WarehouseCode", "City", "Observations"))
                writer.writeheader()
                writer.writerows([
                    {"WarehouseCode": "WH1", "City": "City A", "Observations": 3},
                    {"WarehouseCode": "WH1", "City": "City B", "Observations": 2},
                    {"WarehouseCode": "WH2", "City": "", "Observations": 1},
                ])
            config["location_mapping"]["mapping_file"] = str(path)
            rows = [
                shipment("A", origin="WH1", destination="WH2"),
                shipment("B", origin="WH1", destination="WH2"),
            ]
            result = analyze(rows, config, metadata())
        audit = {r["WarehouseCode"]: r for r in result["city_mapping"]}
        self.assertEqual((audit["WH1"]["MappingStatus"], audit["WH1"]["Observations"]), ("CONFLICT", 5))
        self.assertEqual((audit["WH2"]["MappingStatus"], audit["WH2"]["MissingCitySourceRows"]), ("MISSING", 1))
        self.assertEqual(audit["WH2"]["Observations"], 1)
        lane = next(r for r in result["lanes"] if r["Scenario"] == "Daily")
        self.assertEqual((lane["OriginCity"], lane["OriginCityMappingStatus"]), ("", "CONFLICT"))
        self.assertEqual((lane["DestinationCity"], lane["DestinationCityMappingStatus"]), ("", "MISSING"))

    def test_aggregate_city_observations_keep_supplied_counts(self):
        config = profile()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "locations.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=("WarehouseCode", "City", "Observations"))
                writer.writeheader()
                writer.writerows([
                    {"WarehouseCode": "O", "City": "City A", "Observations": 3},
                    {"WarehouseCode": "O", "City": "city a", "Observations": 4},
                    {"WarehouseCode": "O", "City": "", "Observations": 2},
                ])
            config["location_mapping"]["mapping_file"] = str(path)
            audit = analyze([shipment()], config, metadata())["city_mapping"]
        self.assertEqual(audit[0]["Observations"], 9)
        self.assertEqual(audit[0]["MissingCitySourceRows"], 2)
        self.assertEqual(audit[0]["MappingStatus"], "VERIFIED_FROM_SHIPMENT_DATA")

    def test_missing_explicit_period_blocks(self):
        config = profile()
        del config["period"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path)
            with self.assertRaisesRegex(AnalysisError, "Required config keys"):
                load_shipments(path, config)

    def test_city_fields_must_be_mapped(self):
        config = profile()
        del config["columns"]["origin_city"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path)
            with self.assertRaisesRegex(AnalysisError, "origin_city"):
                load_shipments(path, config)


    def test_stored_key_mismatch_blocks(self):
        config = profile()
        config["scenarios"]["Daily"]["source"] = "column"
        config["scenarios"]["Daily"]["mapping"] = "daily_key"
        config["columns"]["daily_key"] = "Daily"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path, {"Daily": "2026014"})
            with self.assertRaisesRegex(AnalysisError, "stored key"):
                load_shipments(path, config)

    def test_stored_daily_key_matches_full_date_not_timestamp(self):
        config = profile()
        config["scenarios"]["Daily"].update(source="column", mapping="daily_key")
        config["columns"]["daily_key"] = "Daily"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path, {"Daily": "2026-01-01"})
            rows, info = load_shipments(path, config)
            self.assertEqual(rows[0]["Date"], "2026-01-01")
            self.assertEqual(rows[0]["Daily"], "2026-01-01")
            self.assertEqual(analyze(rows, config, info)["summary"][0]["SavingsUSD"], 0)

    def test_existing_output_refused_and_input_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "input.csv"
            self.write_input(path)
            original = path.read_bytes()
            result = run(path, profile(), root / "result")
            self.assertEqual(result["summary"][0]["CurrentShipments"], 1)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaisesRegex(AnalysisError, "already exists"):
                run(path, profile(), root / "result")

    def test_single_ranking_still_compares_three_scenarios(self):
        config = profile()
        config["ranking"]["scenario"] = "Weekly"
        result = analyze([shipment()], config, metadata())
        self.assertEqual(list(result["top5"]), ["Weekly"])
        self.assertIn("Daily_SavingsUSD", result["side_by_side"][0])
        self.assertEqual(len(result["selected_modes"]), 3)

    def test_legacy_daily_rule_blocks_even_with_acknowledgement(self):
        for source in ("derive", "column"):
            for acknowledgement in (None, "Previously approved legacy behavior"):
                with self.subTest(source=source, acknowledgement=acknowledgement), tempfile.TemporaryDirectory() as temp:
                    config = profile()
                    spec = config["scenarios"]["Daily"]
                    spec.update(source=source, rule="legacy_year_day_of_month", mapping="daily_key")
                    config["columns"]["daily_key"] = "Daily"
                    if acknowledgement:
                        spec["acknowledgement"] = acknowledgement
                    path = Path(temp) / "input.csv"
                    self.write_input(path, {"Daily": "2026001"})
                    original = path.read_bytes()
                    with self.assertRaisesRegex(AnalysisError, "Daily requires calendar_date"):
                        run(path, config, Path(temp) / "result")
                    self.assertFalse((Path(temp) / "result").exists())
                    self.assertEqual(path.read_bytes(), original)

    def test_period_end_is_inclusive(self):
        config = profile()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path, {"Date": "2026-08-31T23:59:59"})
            self.assertEqual(len(load_shipments(path, config)[0]), 1)
            self.write_input(path, {"Date": "2026-09-01T00:00:00"})
            self.assertEqual(len(load_shipments(path, config)[0]), 0)

    def test_unknown_units_or_missing_geography_provenance_block(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path)
            config = profile()
            config["units"]["weight"] = "kg"
            with self.assertRaisesRegex(AnalysisError, "requires USD and pounds"):
                load_shipments(path, config)
            config = profile()
            config["geography"]["prefilter_evidence"] = ""
            with self.assertRaisesRegex(AnalysisError, "filter evidence"):
                load_shipments(path, config)

    def test_negative_savings_remain_in_lane_and_summary(self):
        result = analyze([shipment("heavy", "10", "20"), shipment("light", "5", "1")], profile(), metadata())
        self.assertTrue(all(r["SavingsUSD"] == -9 for r in result["summary"]))
        self.assertTrue(all(r["SavingsUSD"] == -9 for r in result["lanes"]))

    def test_shipment_ids_retain_leading_zeros(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.csv"
            self.write_input(path)
            self.assertEqual(load_shipments(path, profile())[0][0]["Load"], "001")


if __name__ == "__main__":
    unittest.main()
