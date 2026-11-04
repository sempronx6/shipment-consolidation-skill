"""Reconcile a Skill output directory with its saved profile and audit CSVs."""

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path

from consolidation import AnalysisError

D = Decimal


def fail(message):
    raise AnalysisError(message)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def dec(value):
    return D(str(value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Existing Skill run directory.")
    parser.add_argument("--output", type=Path, required=True, help="New JSON validation file; no overwrite.")
    args = parser.parse_args()
    if not args.run.is_dir():
        fail(f"Run directory does not exist: {args.run}")
    if args.output.exists():
        fail(f"Validation output already exists: {args.output}")
    manifest = json.loads((args.run / "manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    directions = set(config["eligibility"]["excluded_sites"]["directions"])
    sites = set(config["eligibility"]["excluded_sites"]["warehouse_codes"])
    excluded_modes = {mode.strip().casefold() for mode in config["eligibility"]["excluded_modes"]}
    ranking_file = f'top{config["ranking"]["top_n"]}'
    required = ("waterfall", "frequency", "summary", "lanes", "modes", "side_by_side", "batches", "members",
                "examples", "example_members", "rate_diagnostics", "eligibility_decisions", "selected_modes",
                "city_mapping", ranking_file)
    files = {name: read_csv(args.run / (name + ".csv")) for name in required}
    checks = []

    decisions = files["eligibility_decisions"]
    ids = [r["Load"] for r in decisions]
    if len(ids) != len(set(ids)):
        fail("Duplicate scoped shipment IDs in eligibility decisions.")
    checks.append("Scoped shipment identifiers unique")

    def excluded(row):
        return (("origin" in directions and row["Origin"] in sites) or
                ("destination" in directions and row["Destination"] in sites))

    def excluded_mode(row):
        return row["Mode"].strip().casefold() in excluded_modes

    for r in decisions:
        if excluded_mode(r) != (r["Decision"] == "ExcludedMode"):
            fail(f"Excluded-mode rule mismatch for shipment {r['Load']}.")
        if not excluded_mode(r) and excluded(r) != (r["Decision"] == "ExcludedSite"):
            fail(f"Site exclusion direction mismatch for shipment {r['Load']}.")
    retained = [r for r in decisions if r["Decision"] == "Eligible"]
    mode_rows = [r for r in decisions if r["Decision"] == "ExcludedMode"]
    site_rows = [r for r in decisions if r["Decision"] == "ExcludedSite"]
    low_rows = [r for r in decisions if r["Decision"] == "ExcludedFrequency"]
    if len(retained) + len(mode_rows) + len(site_rows) + len(low_rows) != len(decisions):
        fail("Eligibility and exclusion decisions are not disjoint and exhaustive.")
    if any(excluded_mode(r) for r in retained + site_rows + low_rows):
        fail("Shipment in an excluded mode remains in the opportunity scope.")
    if any(excluded(r) for r in retained + low_rows):
        fail("Shipment excluded by a configured endpoint site remains in the opportunity scope.")
    checks.append("Excluded modes and bidirectional endpoint exclusions are disjoint and precede frequency")

    waterfall = {r["Stage"]: r for r in files["waterfall"]}
    required_stages = ("Original scope", "Excluded modes", "After mode exclusions", "Excluded sites",
                       "After site exclusions", "Excluded low frequency: non-site and non-mode only", "Retained")
    if not all(s in waterfall for s in required_stages):
        fail("Missing standard exclusion waterfall stages.")
    if int(waterfall["Original scope"]["Shipments"]) != len(decisions):
        fail("Waterfall source count differs from scoped decision records.")
    mode_remaining = [r for r in decisions if not excluded_mode(r)]
    site_remaining = [r for r in mode_remaining if not excluded(r)]
    for stage, rows in (
        ("Original scope", decisions), ("Excluded modes", mode_rows),
        ("After mode exclusions", mode_remaining), ("Excluded sites", site_rows),
        ("After site exclusions", site_remaining),
        ("Excluded low frequency: non-site and non-mode only", low_rows), ("Retained", retained),
    ):
        expected_count = len(rows)
        expected_cost = sum((dec(r["CostUSD"]) for r in rows), D(0))
        if int(waterfall[stage]["Shipments"]) != expected_count:
            fail(f"Waterfall count mismatch for {stage}.")
        if abs(dec(waterfall[stage]["ActualCostUSD"]) - expected_cost) > D("1e-20"):
            fail(f"Waterfall actual-cost mismatch for {stage}.")
    excluded_mode_cost = dec(waterfall["Excluded modes"]["ActualCostUSD"])
    excluded_cost = dec(waterfall["Excluded sites"]["ActualCostUSD"])
    low_cost = dec(waterfall["Excluded low frequency: non-site and non-mode only"]["ActualCostUSD"])
    retained_cost = dec(waterfall["Retained"]["ActualCostUSD"])
    original_cost = dec(waterfall["Original scope"]["ActualCostUSD"])
    if abs(original_cost - excluded_mode_cost - excluded_cost - low_cost - retained_cost) > D("1e-20"):
        fail("Waterfall cost does not reconcile.")
    checks.append("Exclusion counts and actual costs reconcile without double-counting")

    months = int(manifest["metadata"]["MonthDenominator"])
    threshold = dec(config["eligibility"]["minimum_shipments_per_calendar_month"])
    eligible_keys = set()
    for r in files["frequency"]:
        if r["Mode"].strip().casefold() in excluded_modes:
            fail(f"Excluded mode appears in frequency eligibility: {r['Mode']}.")
        is_eligible = dec(r["Shipments"]) >= threshold * months
        if str(r["Eligible"]).lower() != str(is_eligible).lower():
            fail(f"Frequency rule mismatch for {r['Origin']} -> {r['Destination']} / {r['Mode']}.")
        if is_eligible:
            eligible_keys.add((r["Origin"], r["Destination"], r["Mode"]))
    retained_keys = {(r["Origin"], r["Destination"], r["Mode"]) for r in retained}
    if eligible_keys != retained_keys:
        fail("Lane-mode frequency eligibility does not reconcile to retained shipment records.")
    checks.append("Frequency threshold verified per lane-mode using the full calendar-month denominator")

    city_map = {r["WarehouseCode"]: r for r in files["city_mapping"]}
    for r in files["city_mapping"]:
        status = r["MappingStatus"]
        if status == "CONFLICT" and (r["City"] or len(r["ObservedCityValues"].split(" | ")) < 2):
            fail(f"Conflicting city mapping is not flagged/resolved blank for {r['WarehouseCode']}.")
        if status == "MISSING" and r["City"]:
            fail(f"Missing city mapping unexpectedly has a city for {r['WarehouseCode']}.")
        if status not in ("CONFLICT", "MISSING", "VERIFIED_FROM_SHIPMENT_DATA"):
            fail(f"Unknown city mapping status {status!r}.")
    city_collections = ("frequency", "lanes", "modes", "side_by_side", "batches", "members", "examples",
                        "example_members", "rate_diagnostics", "eligibility_decisions", "selected_modes",
                        ranking_file)
    for name in city_collections:
        for r in files[name]:
            for key, prefix in (("Origin", "Origin"), ("Destination", "Destination")):
                expected_city = city_map.get(r[key], {"City": "", "MappingStatus": "MISSING"})
                if r.get(prefix + "City", "") != expected_city["City"]:
                    fail(f"{name}: city value does not reconcile for {r[key]}.")
                if r.get(prefix + "CityMappingStatus", "") != expected_city["MappingStatus"]:
                    fail(f"{name}: city status does not reconcile for {r[key]}.")
    checks.append("City mappings and statuses propagate to lane-level, ranking and detailed outputs")

    members_by_batch = {}
    for r in files["members"]:
        key = (r["Scenario"], r["Origin"], r["Destination"], r["Mode"], r["BatchKey"])
        members_by_batch.setdefault(key, []).append(r)
    if len(members_by_batch) != len(files["batches"]):
        fail("Batch membership count does not equal batch count.")
    for b in files["batches"]:
        key = (b["Scenario"], b["Origin"], b["Destination"], b["Mode"], b["BatchKey"])
        group = members_by_batch.get(key, [])
        if len(group) != int(b["OriginalShipments"]):
            fail(f"Original member count mismatch for {key}.")
        if max(dec(r["WeightLB"]) for r in group) != dec(b["ReferenceWeightLB"]):
            fail(f"Reference is not the highest-weight shipment for {key}.")
        tied = [r for r in group if dec(r["WeightLB"]) == dec(b["ReferenceWeightLB"])]
        if min(dec(r["CostUSD"]) / dec(r["WeightLB"]) for r in tied) != dec(b["ReferenceRateUSDperLB"]):
            fail(f"Tied highest-weight reference rate mismatch for {key}.")
        if abs(sum((dec(r["WeightLB"]) for r in group), D(0)) - dec(b["CombinedWeightLB"])) > D("1e-30"):
            fail(f"Batch weight does not reconcile for {key}.")
        actual = sum((dec(r["CostUSD"]) for r in group), D(0))
        if abs(actual - dec(b["OriginalCostUSD"])) > D("1e-20"):
            fail(f"Batch cost does not reconcile for {key}.")
        expected_estimate = actual if len(group) == 1 else dec(b["CombinedWeightLB"]) * dec(b["ReferenceRateUSDperLB"])
        if abs(expected_estimate - dec(b["EstimatedCostUSD"])) > D("1e-20"):
            fail(f"Batch estimate does not reconcile for {key}.")
        if abs(actual - expected_estimate - dec(b["SavingsUSD"])) > D("1e-20"):
            fail(f"Batch savings do not reconcile for {key}.")
    checks.append(f"All {len(files['batches'])} batch memberships, highest-weight references and savings reconcile")

    ranking_scenario = config["ranking"]["scenario"]
    rankings = {}
    for r in files[ranking_file]:
        rankings.setdefault(r["RankingScenario"], []).append(r)
    expected_scenarios = ("Daily", "TueFri", "Weekly") if ranking_scenario == "separate" else (ranking_scenario,)
    if set(rankings) != (set(expected_scenarios) if retained else set()):
        fail("Ranking scenarios do not match the saved profile.")
    for scenario in expected_scenarios:
        lanes = [r for r in files["lanes"] if r["Scenario"] == scenario]
        if bool(lanes) != bool(retained):
            fail(f"Ranking lane baseline does not match retained shipments in {scenario}.")
        expected = sorted(lanes, key=lambda r: (-dec(r["SavingsUSD"]), r["Origin"], r["Destination"]))[
            :config["ranking"]["top_n"]
        ]
        rows = rankings.get(scenario, [])
        if len(rows) != len(expected):
            fail(f"Ranking lane count does not match the configured top-N in {scenario}.")
        for rank, (row, lane) in enumerate(zip(rows, expected), 1):
            if (int(row["Rank"]) != rank or row["Scenario"] != scenario
                    or (row["Origin"], row["Destination"]) != (lane["Origin"], lane["Destination"])
                    or dec(row["SavingsUSD"]) != dec(lane["SavingsUSD"])):
                fail(f"Ranking lane, rank or net USD savings mismatch in {scenario} at rank {rank}.")
    checks.append("Ranking scenario(s), top-N and net-USD order match the profile")

    result = {
        "status": "PASS", "run_directory": str(args.run), "scoped_shipments": len(decisions),
        "eligible_shipments": len(retained), "eligible_lane_modes": len(eligible_keys),
        "batches": len(files["batches"]), "city_mapping_codes": len(city_map),
        "conflicting_city_mappings": sum(r["MappingStatus"] == "CONFLICT" for r in files["city_mapping"]),
        "missing_city_mappings": sum(r["MappingStatus"] == "MISSING" for r in files["city_mapping"]),
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
