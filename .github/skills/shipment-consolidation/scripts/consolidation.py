"""Dependency-free, read-only-input consolidation screening engine."""

import argparse
import calendar
import csv
import hashlib
import html
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from statistics import median

getcontext().prec = 40
D = Decimal
SCENARIOS = ("Daily", "TueFri", "Weekly")
BASE_COLUMNS = ("shipment_id", "activity_date", "origin", "destination", "mode", "cost", "weight")
CITY_COLUMNS = ("origin_city", "destination_city")
METRICS = (
    "CurrentShipments", "ActualCostUSD", "ConsolidatedShipments", "ShipmentReduction",
    "ShipmentReductionPct", "AverageOriginalShipmentsPerBatch", "EstimatedCostUSD",
    "SavingsUSD", "SavingsPct", "NegativeSavingsBatches",
)


class AnalysisError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AnalysisError(message)


def total(values):
    return sum(values, D(0))


def fraction(a, b):
    return D(a) / D(b) if b else None


def number(value, context):
    try:
        result = D(str(value))
    except InvalidOperation as exc:
        raise AnalysisError(f"{context}: invalid number {value!r}") from exc
    require(result.is_finite(), f"{context}: non-finite number")
    return result


def period_info(period):
    start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    require(start <= end, "Analysis period starts after it ends.")
    require(period["date_handling"] == "source_calendar_date_no_timezone_conversion",
            "Resolve timezone/date handling before running.")
    month = date(start.year, start.month, 1)
    months = []
    while month <= end:
        months.append(month.isoformat()[:7])
        month = date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)
    partial = []
    if start.day != 1:
        partial.append(months[0])
    if end.day != calendar.monthrange(end.year, end.month)[1] and months[-1] not in partial:
        partial.append(months[-1])
    policy = period["partial_month_policy"]
    require(policy in ("require_confirmation", "count_each_touched_month"), "Unknown partial-month policy.")
    if partial:
        require(policy == "count_each_touched_month" and period.get("partial_month_confirmation"),
                f"Partial calendar months: {partial}. Ask how to treat them before calculating. "
                "Only count_each_touched_month is implemented; other choices require an agreed extension.")
    return start, end, months, partial


def validate_config(config):
    required = ("profile_name", "period", "columns", "units", "geography", "eligibility", "location_mapping",
                "scenarios", "reference_tie_policy", "ranking", "data_quality_policy", "coverage_confirmation")
    require(all(k in config for k in required), f"Required config keys: {', '.join(required)}")
    require(config["units"] == {"currency": "USD", "weight": "lb"},
            "Engine requires USD and pounds. Confirm and document conversions before import.")
    require(isinstance(config["period"], dict) and all(
        isinstance(config["period"].get(k), str) and config["period"][k].strip()
        for k in ("start", "end", "date_field_meaning", "date_handling", "partial_month_policy")
    ), "An explicit analysis period, date meaning, and partial-month policy are required; ask the user if missing.")
    require(isinstance(config["coverage_confirmation"], str) and config["coverage_confirmation"].strip(),
            "Confirm dataset coverage, not just observed active dates.")
    require(config["data_quality_policy"] == "stop_on_invalid_or_duplicate",
            "Resolve invalid inputs/duplicates; automatic dropping or deduplication is not implemented.")
    require(all(k in config["columns"] for k in BASE_COLUMNS), "Missing required column mappings.")
    require(all(k in config["columns"] for k in CITY_COLUMNS),
            "Map origin_city and destination_city to shipment city fields or a verified location mapping.")
    require(isinstance(config["location_mapping"].get("source"), str) and config["location_mapping"]["source"].strip()
            and isinstance(config["location_mapping"].get("verification"), str)
            and config["location_mapping"]["verification"].strip(),
            "Declare the city source and verification evidence; do not infer warehouse cities.")
    if "mapping_file" in config["location_mapping"]:
        require(isinstance(config["location_mapping"]["mapping_file"], str)
                and config["location_mapping"]["mapping_file"].strip(),
                "location_mapping.mapping_file must be a nonblank path when provided.")
    eligibility = config["eligibility"]
    excluded_modes = eligibility.get("excluded_modes")
    require(isinstance(excluded_modes, list) and all(isinstance(mode, str) and mode.strip()
                                                    for mode in excluded_modes),
            "eligibility.excluded_modes must be an explicit list of nonblank mode labels; [] means none.")
    require(len({mode.strip().casefold() for mode in excluded_modes}) == len(excluded_modes),
            "eligibility.excluded_modes contains duplicate labels after case normalization.")
    if excluded_modes:
        require(isinstance(eligibility.get("excluded_modes_rationale"), str)
                and eligibility["excluded_modes_rationale"].strip(),
                "Document a nonblank rationale for configured excluded modes.")
    elif "excluded_modes_rationale" in eligibility:
        require(isinstance(eligibility["excluded_modes_rationale"], str)
                and eligibility["excluded_modes_rationale"].strip(),
                "eligibility.excluded_modes_rationale must be nonblank when provided.")
    sites = eligibility["excluded_sites"]
    require(isinstance(sites.get("warehouse_codes"), list),
            "Explicit excluded site warehouse-code list required; [] means none.")
    require(set(sites.get("directions", [])) <= {"origin", "destination"} and sites.get("directions"),
            "Excluded site directions must explicitly include origin, destination, or both.")
    require(number(eligibility["minimum_shipments_per_calendar_month"], "Frequency threshold") >= 0,
            "Frequency threshold must be nonnegative.")
    geo = config["geography"]
    for k in ("origin_countries", "destination_countries"):
        require(isinstance(geo[k], list) and all(isinstance(v, str) and v for v in geo[k]),
                f"{k} must be an explicit list; [] means unrestricted.")
    require(isinstance(geo["input_is_geography_prefiltered"], bool), "Explicit geography provenance required.")
    if geo["input_is_geography_prefiltered"]:
        require(bool(geo.get("prefilter_evidence", "").strip()), "Prefiltered input requires geographic filter evidence.")
    else:
        require(all(k in config["columns"] for k in ("origin_country", "destination_country")),
                "Map actual pickup and final delivery country fields.")
    require(config["reference_tie_policy"] in ("lowest_rate_among_highest_weight", "require_confirmation"),
            "Unsupported reference tie rule. Ask before changing it.")
    ranking = config["ranking"]
    require(ranking["scenario"] in (*SCENARIOS, "separate"), "Confirm ranking scenario or separate lists.")
    require(ranking["metric"] == "savings_amount", "This engine ranks by net estimated savings USD.")
    require(type(ranking["top_n"]) is int and ranking["top_n"] > 0, "top_n must be a positive integer.")
    rules = {
        "Daily": ("calendar_date",),
        "TueFri": ("saturday_tuesday_wednesday_friday",),
        "Weekly": ("saturday_friday_calendar_year_weeknum",),
    }
    require(set(config["scenarios"]) == set(SCENARIOS), "Exactly Daily, TueFri and Weekly scenarios required.")
    for scenario, spec in config["scenarios"].items():
        require(spec["source"] in ("column", "derive"), f"{scenario}: source must be column or derive.")
        if scenario == "Daily":
            require(spec["rule"] == "calendar_date",
                    "Daily requires calendar_date (year-month-date). Legacy year-day-of-month pooling is unsupported; "
                    "update the profile and regenerate any stored Daily keys from the full date.")
        require(spec["rule"] in rules[scenario], f"{scenario}: unresolved or unsupported grouping rule.")
        if spec["source"] == "column":
            require(spec.get("mapping") in config["columns"], f"{scenario}: missing key column mapping.")
    return period_info(config["period"])


def expected_key(day, rule):
    if rule == "calendar_date":
        return day.isoformat()
    jan1 = date(day.year, 1, 1)
    week = ((day - jan1).days + (jan1.weekday() - 5) % 7) // 7 + 1
    yearweek = day.year * 100 + week
    if rule == "saturday_friday_calendar_year_weeknum":
        return f"{yearweek}Batch1"
    require(rule == "saturday_tuesday_wednesday_friday", "Unknown batch rule.")
    return f"{yearweek}Batch{1 if (day.weekday() - 5) % 7 <= 3 else 2}"


def load_shipments(path, config):
    start, end, months, partial = validate_config(config)
    mapping = config["columns"]
    required = set(BASE_COLUMNS + CITY_COLUMNS)
    if not config["geography"]["input_is_geography_prefiltered"]:
        required.update(("origin_country", "destination_country"))
    required.update(s["mapping"] for s in config["scenarios"].values() if s["source"] == "column")
    rows = []
    input_count = outside_period = outside_geography = 0
    seen = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames),
                "Missing or duplicate CSV headers.")
        require(set(mapping[k] for k in required) <= set(reader.fieldnames), "CSV is missing mapped columns.")
        for line, raw in enumerate(reader, 2):
            input_count += 1
            require(None not in raw and all(v is not None for v in raw.values()), f"CSV row {line}: malformed field count.")
            values = {k: raw[mapping[k]] for k in required}
            require(all(v.strip() for k, v in values.items() if k not in CITY_COLUMNS),
                    f"CSV row {line}: blank required input.")
            try:
                dt = datetime.fromisoformat(values["activity_date"])
            except ValueError as exc:
                raise AnalysisError(f"CSV row {line}: require ISO date or datetime.") from exc
            require(dt.tzinfo is None, f"CSV row {line}: timezone-bearing timestamps require an explicit conversion upstream.")
            day = dt.date()
            if not start <= day <= end:
                outside_period += 1
                continue
            geo = config["geography"]
            if not geo["input_is_geography_prefiltered"]:
                if any(geo[k] and values[field] not in geo[k] for k, field in (
                    ("origin_countries", "origin_country"), ("destination_countries", "destination_country")
                )):
                    outside_geography += 1
                    continue
            shipment_id = values["shipment_id"]
            require(shipment_id not in seen, f"Duplicate shipment ID {shipment_id!r} in scoped input. Do not silently deduplicate.")
            seen.add(shipment_id)
            weight = number(values["weight"], f"Row {line} weight")
            cost = number(values["cost"], f"Row {line} cost")
            require(weight > 0 and cost > 0, f"Row {line}: nonpositive cost/weight; ask how to treat credits, free loads or missing weights.")
            row = {
                "Load": shipment_id, "Date": day.isoformat(), "Origin": values["origin"],
                "Destination": values["destination"], "Mode": values["mode"], "CostUSD": cost, "WeightLB": weight,
                "OriginCityRaw": values["origin_city"].strip(),
                "DestinationCityRaw": values["destination_city"].strip(),
            }
            for scenario, spec in config["scenarios"].items():
                expected = expected_key(day, spec["rule"])
                key = values[spec["mapping"]] if spec["source"] == "column" else expected
                require(key == expected, f"Row {line} {scenario}: stored key {key!r} differs from confirmed rule {expected!r}. "
                        "Resolve semantics; keys must be canonical strings (not spreadsheet-rounded numbers).")
                row[scenario] = key
            rows.append(row)
    metadata = {
        "InputRows": input_count, "OutsidePeriodRows": outside_period, "OutsideGeographyRows": outside_geography,
        "ScopedRows": len(rows), "CalendarMonths": months, "PartialMonths": partial,
        "MonthDenominator": len(months),
    }
    return rows, metadata


def make_batch(scenario, key, rows, tie_policy):
    origin, destination, mode, batch_key = key
    max_weight = max(r["WeightLB"] for r in rows)
    tied = [r for r in rows if r["WeightLB"] == max_weight]
    distinct_costs = {r["CostUSD"] for r in tied}
    require(tie_policy != "require_confirmation" or len(distinct_costs) <= 1,
            f"{scenario} {key}: highest-weight shipments have different rates; ask which tied reference to use.")
    reference = min(tied, key=lambda r: (r["CostUSD"], r["Load"]))
    rate = reference["CostUSD"] / max_weight
    weight = total(r["WeightLB"] for r in rows)
    cost = total(r["CostUSD"] for r in rows)
    estimate = cost if len(rows) == 1 else weight * rate
    dates = sorted({r["Date"] for r in rows})
    return {
        "Scenario": scenario, "Origin": origin, "Destination": destination, "Mode": mode, "BatchKey": batch_key,
        "OriginalShipments": len(rows), "OriginalCostUSD": cost, "CombinedWeightLB": weight,
        "ReferenceLoad": reference["Load"], "ReferenceWeightLB": max_weight,
        "ReferenceCostUSD": reference["CostUSD"], "ReferenceRateUSDperLB": rate,
        "HighestWeightTies": len(tied), "DifferentCostHighestWeightTie": len(distinct_costs) > 1,
        "EstimatedCostUSD": estimate, "SavingsUSD": cost - estimate,
        "NegativeSavings": estimate > cost, "FirstDate": dates[0], "LastDate": dates[-1],
        "DistinctMonths": len({d[:7] for d in dates}),
    }


def rollup(batches, dimensions):
    groups = defaultdict(list)
    for batch in batches:
        groups[tuple(batch[k] for k in dimensions)].append(batch)
    result = []
    for key, rows in sorted(groups.items()):
        n = sum(b["OriginalShipments"] for b in rows)
        cost = total(b["OriginalCostUSD"] for b in rows)
        estimate = total(b["EstimatedCostUSD"] for b in rows)
        result.append({
            **dict(zip(dimensions, key)), "CurrentShipments": n, "ActualCostUSD": cost,
            "ConsolidatedShipments": len(rows), "ShipmentReduction": n - len(rows),
            "ShipmentReductionPct": fraction(n - len(rows), n),
            "AverageOriginalShipmentsPerBatch": fraction(n, len(rows)),
            "EstimatedCostUSD": estimate, "SavingsUSD": cost - estimate,
            "SavingsPct": fraction(cost - estimate, cost),
            "NegativeSavingsBatches": sum(b["NegativeSavings"] for b in rows),
            "NegativeSavingsAtLeastOneCentBatches": sum(b["SavingsUSD"] <= D("-0.01") for b in rows),
            "NegativeBatchSavingsUSD": total(b["SavingsUSD"] for b in rows if b["NegativeSavings"]),
            "CrossMonthBatches": sum(b["DistinctMonths"] > 1 for b in rows),
        })
    return result


def analyze(rows, config, metadata):
    validate_config(config)
    months = metadata["MonthDenominator"]
    excluded_modes = {mode.strip().casefold() for mode in config["eligibility"]["excluded_modes"]}
    excluded = set(config["eligibility"]["excluded_sites"]["warehouse_codes"])
    directions = set(config["eligibility"]["excluded_sites"]["directions"])
    threshold = number(config["eligibility"]["minimum_shipments_per_calendar_month"], "Frequency threshold")

    def is_excluded_mode(row):
        return row["Mode"].strip().casefold() in excluded_modes

    def is_excluded_site(row):
        return (("origin" in directions and row["Origin"] in excluded) or
                ("destination" in directions and row["Destination"] in excluded))

    mode_rows = [r for r in rows if is_excluded_mode(r)]
    after_mode = [r for r in rows if not is_excluded_mode(r)]
    site_rows = [r for r in after_mode if is_excluded_site(r)]
    remaining = [r for r in after_mode if not is_excluded_site(r)]
    groups = defaultdict(list)
    for r in remaining:
        groups[r["Origin"], r["Destination"], r["Mode"]].append(r)
    frequency = []
    eligible = []
    frequency_rows = []
    for (o, d, m), group in sorted(groups.items()):
        keep = D(len(group)) >= threshold * months
        (eligible if keep else frequency_rows).extend(group)
        month_counts = {month: sum(r["Date"].startswith(month) for r in group) for month in metadata["CalendarMonths"]}
        frequency.append({
            "Origin": o, "Destination": d, "Mode": m, "Shipments": len(group),
            "CalendarMonths": months, "AverageShipmentsPerMonth": fraction(len(group), months),
            "ActiveMonths": sum(n > 0 for n in month_counts.values()), "Eligible": keep,
            "ActualCostUSD": total(r["CostUSD"] for r in group), **month_counts,
        })
    categories = {}
    for label, group in (("ExcludedMode", mode_rows), ("ExcludedSite", site_rows),
                         ("ExcludedFrequency", frequency_rows), ("Eligible", eligible)):
        for r in group:
            require(r["Load"] not in categories, "Exclusion categories overlap.")
            categories[r["Load"]] = label
    require(len(categories) == len(rows), "Exclusion categories do not reconcile.")
    require(abs(total(r["CostUSD"] for r in rows)
                - total(r["CostUSD"] for r in mode_rows + site_rows + frequency_rows + eligible)) < D("1e-20"),
            "Exclusion costs do not reconcile.")
    waterfall = []
    for label, group in (
        ("Original scope", rows), ("Excluded modes", mode_rows), ("After mode exclusions", after_mode),
        ("Excluded sites", site_rows), ("After site exclusions", remaining),
        ("Excluded low frequency: non-site and non-mode only", frequency_rows), ("Retained", eligible),
    ):
        waterfall.append({
            "Stage": label, "Shipments": len(group), "ActualCostUSD": total(r["CostUSD"] for r in group),
            "Lanes": len({(r["Origin"], r["Destination"]) for r in group}),
            "LaneModes": len({(r["Origin"], r["Destination"], r["Mode"]) for r in group}),
        })
    batches, members = [], []
    for scenario in SCENARIOS:
        groups = defaultdict(list)
        for r in eligible:
            if scenario == "Daily":
                require(r[scenario] == r["Date"],
                        f"Shipment {r['Load']}: Daily key must equal the full calendar date.")
            groups[r["Origin"], r["Destination"], r["Mode"], r[scenario]].append(r)
        for key, group in sorted(groups.items()):
            b = make_batch(scenario, key, group, config["reference_tie_policy"])
            batches.append(b)
            members.extend({
                "Scenario": scenario, "Origin": r["Origin"], "Destination": r["Destination"],
                "Mode": r["Mode"], "BatchKey": r[scenario], "Load": r["Load"], "Date": r["Date"],
                "WeightLB": r["WeightLB"], "CostUSD": r["CostUSD"],
                "UnitRateUSDperLB": r["CostUSD"] / r["WeightLB"], "SelectedReference": r["Load"] == b["ReferenceLoad"],
            } for r in group)
    summary = rollup(batches, ["Scenario"])
    if not eligible:
        summary = [{
            "Scenario": s, **{k: None if k in ("ShipmentReductionPct", "AverageOriginalShipmentsPerBatch", "SavingsPct") else 0
                              for k in METRICS}, "NegativeSavingsAtLeastOneCentBatches": 0,
            "NegativeBatchSavingsUSD": D(0), "CrossMonthBatches": 0,
        } for s in SCENARIOS]
    lanes = rollup(batches, ["Scenario", "Origin", "Destination"])
    modes = rollup(batches, ["Scenario", "Origin", "Destination", "Mode"])
    rank_scenarios = SCENARIOS if config["ranking"]["scenario"] == "separate" else [config["ranking"]["scenario"]]
    top = {}
    for s in rank_scenarios:
        top[s] = sorted((r for r in lanes if r["Scenario"] == s),
                        key=lambda r: (-r["SavingsUSD"], r["Origin"], r["Destination"]))[:config["ranking"]["top_n"]]
    selected = {(r["Origin"], r["Destination"]) for rs in top.values() for r in rs}
    wide = []
    for o, d in sorted(selected):
        matching = {r["Scenario"]: r for r in lanes if (r["Origin"], r["Destination"]) == (o, d)}
        first = matching["Daily"]
        row = {"Origin": o, "Destination": d, "CurrentShipments": first["CurrentShipments"], "ActualCostUSD": first["ActualCostUSD"]}
        row.update({s + "_" + k: matching[s][k] for s in SCENARIOS for k in METRICS if k not in ("CurrentShipments", "ActualCostUSD")})
        row["WeeklyMinusTueFriSavingsUSD"] = matching["Weekly"]["SavingsUSD"] - matching["TueFri"]["SavingsUSD"]
        row["TueFriMinusDailySavingsUSD"] = matching["TueFri"]["SavingsUSD"] - matching["Daily"]["SavingsUSD"]
        row["WeeklyMinusDailySavingsUSD"] = matching["Weekly"]["SavingsUSD"] - matching["Daily"]["SavingsUSD"]
        wide.append(row)
    examples = []
    for s, ranked in top.items():
        for rank, lane in enumerate(ranked, 1):
            candidates = [b for b in batches if b["Scenario"] == s and
                          (b["Origin"], b["Destination"]) == (lane["Origin"], lane["Destination"])]
            example = sorted(candidates, key=lambda b: (-b["SavingsUSD"], b["Mode"], b["BatchKey"]))[0]
            examples.append({"Example": f"{s}_rank{rank}", **example})
    example_keys = {(e["Scenario"], e["Origin"], e["Destination"], e["Mode"], e["BatchKey"]): e["Example"] for e in examples}
    example_members = [{"Example": example_keys[key], **r} for r in members
                       if (key := (r["Scenario"], r["Origin"], r["Destination"], r["Mode"], r["BatchKey"])) in example_keys]
    diagnostic_groups = defaultdict(list)
    for r in eligible:
        diagnostic_groups[r["Origin"], r["Destination"], r["Mode"]].append(r)
    diagnostics = []
    for (o, d, m), group in sorted(diagnostic_groups.items()):
        rates = [r["CostUSD"] / r["WeightLB"] for r in group]
        diagnostics.append({
            "Origin": o, "Destination": d, "Mode": m, "Shipments": len(group),
            "ActiveCalendarDates": len({r["Date"] for r in group}),
            "MinWeightLB": min(r["WeightLB"] for r in group), "MaxWeightLB": max(r["WeightLB"] for r in group),
            "MinRateUSDperLB": min(rates), "MedianRateUSDperLB": median(rates), "MaxRateUSDperLB": max(rates),
        })

    city_observations = defaultdict(list)
    city_missing = defaultdict(int)
    for r in rows:
        for code, city in ((r["Origin"], r["OriginCityRaw"]),
                           (r["Destination"], r["DestinationCityRaw"])):
            if city:
                city_observations[code].append(city.strip())
            else:
                city_missing[code] += 1
    city_mapping = {}
    city_audit = []
    mapping_file = config["location_mapping"].get("mapping_file")
    if mapping_file:
        city_observations = defaultdict(list)
        city_missing = defaultdict(int)
        mapping_pairs = {}
        with Path(mapping_file).open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            require(reader.fieldnames and {"WarehouseCode", "City", "Observations"} <= set(reader.fieldnames),
                    "Verified city mapping file requires WarehouseCode, City and Observations columns.")
            for line, record in enumerate(reader, 2):
                code, city = record["WarehouseCode"].strip(), record["City"].strip()
                require(bool(code), f"City mapping row {line}: warehouse code is blank.")
                observations = number(record["Observations"], f"City mapping row {line} observations")
                require(observations > 0 and observations == observations.to_integral_value(),
                        f"City mapping row {line}: observations must be a positive integer.")
                pair = (code, city.casefold())
                representative, previous_count = mapping_pairs.get(pair, (city, 0))
                representative = min((representative, city), key=lambda v: (v.casefold(), v))
                mapping_pairs[pair] = (representative, previous_count + int(observations))
        mapped_observations = {}
        for (code, _), (city, observations) in mapping_pairs.items():
            mapped_observations[code, city] = observations
            if city:
                city_observations[code].append(city)
            else:
                city_missing[code] += observations
        mapped_codes = set(city_observations) | set(city_missing)
    else:
        mapped_codes = set(city_observations) | set(city_missing)
    for code in sorted(mapped_codes):
        observed = sorted(set(city_observations[code]), key=lambda v: (v.casefold(), v))
        normalized = {v.casefold() for v in observed}
        if not observed:
            status, resolved = "MISSING", ""
        elif len(normalized) > 1:
            status, resolved = "CONFLICT", ""
        else:
            status, resolved = "VERIFIED_FROM_SHIPMENT_DATA", observed[0]
        city_mapping[code] = {"City": resolved, "Status": status}
        observation_count = (
            sum(n for (mapped_code, _), n in mapped_observations.items() if mapped_code == code)
            if mapping_file else len(city_observations[code]) + city_missing[code]
        )
        city_audit.append({
            "WarehouseCode": code, "City": resolved, "MappingStatus": status,
            "ObservedCityValues": " | ".join(observed), "MissingCitySourceRows": city_missing[code],
            "Observations": observation_count,
        })

    def add_lane_cities(record):
        for warehouse_key, prefix in (("Origin", "Origin"), ("Destination", "Destination")):
            mapping_result = city_mapping.get(record[warehouse_key], {"City": "", "Status": "MISSING"})
            record[prefix + "City"] = mapping_result["City"]
            record[prefix + "CityMappingStatus"] = mapping_result["Status"]
        return record

    for collection in (frequency, lanes, modes, batches, members, diagnostics):
        for record in collection:
            add_lane_cities(record)
    eligibility_decisions = [
        add_lane_cities({
            "Load": r["Load"], "Origin": r["Origin"], "Destination": r["Destination"], "Mode": r["Mode"],
            "CostUSD": r["CostUSD"], "Decision": categories[r["Load"]],
        }) for r in rows
    ]
    for record in wide:
        add_lane_cities(record)
    for record in examples:
        add_lane_cities(record)
    for record in example_members:
        add_lane_cities(record)
    selected_modes = [add_lane_cities(r) for r in modes if (r["Origin"], r["Destination"]) in selected]
    for s in summary:
        require(s["CurrentShipments"] == len(eligible), "Scenario shipment counts do not reconcile.")
        require(abs(D(s["ActualCostUSD"]) - total(r["CostUSD"] for r in eligible)) < D("1e-20"),
                "Scenario cost does not reconcile.")
    return {
        "metadata": metadata, "waterfall": waterfall, "frequency": frequency, "summary": summary,
        "lanes": lanes, "modes": modes, "top5": top, "side_by_side": wide, "batches": batches,
        "members": members, "examples": examples, "example_members": example_members,
        "rate_diagnostics": diagnostics, "city_mapping": city_audit,
        "eligibility_decisions": eligibility_decisions, "selected_modes": selected_modes,
    }


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r)) if rows else ["Status"]
    with path.open("x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def report_html(result, config):
    def table(rows):
        if not rows:
            return "<p>No qualifying records.</p>"
        fields = list(rows[0])
        return "<div class='wide'><table><tr>" + "".join(f"<th>{html.escape(k)}</th>" for k in fields) + "</tr>" + \
            "".join("<tr>" + "".join(f"<td>{html.escape(str(r[k]))}</td>" for k in fields) + "</tr>" for r in rows) + "</table></div>"

    def display(rows):
        return [{k: f"{v:,.2f}" if isinstance(v, D) else ("N/A" if v is None else v) for k, v in r.items()} for r in rows]

    warning = "Daily means the full calendar date (year-month-date); shipments on different dates are never pooled."
    period = config["period"]
    geo = config["geography"]
    eligibility = config["eligibility"]
    excluded_mode_summary = (
        f'Excluded transport modes (case-insensitive): {eligibility["excluded_modes"]}. '
        + (f'{eligibility["excluded_modes_rationale"]} '
           if eligibility.get("excluded_modes_rationale") else "")
    )
    summaries = {r["Scenario"]: r for r in result["summary"]}
    incremental = summaries["Weekly"]["SavingsUSD"] - summaries["TueFri"]["SavingsUSD"]
    count_change = summaries["TueFri"]["ConsolidatedShipments"] - summaries["Weekly"]["ConsolidatedShipments"]
    main = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Consolidation screening</title>"
        "<style>body{font:14px Arial;margin:24px;color:#203040}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccd3da;padding:6px;white-space:nowrap}th{background:#e8eef5}"
        ".wide{overflow-x:auto;margin:15px 0}.warning{background:#fff0d0;padding:12px}</style></head><body>",
        "<h1>" + html.escape(config["profile_name"]) + "</h1><p>" + html.escape(warning) + "</p>",
        "<h2>Analysis period and business rules</h2><p>" + html.escape(
            f'{period["date_field_meaning"]}: {period["start"]} through {period["end"]}, inclusive. '
            f'{result["metadata"]["MonthDenominator"]} calendar months; partial months: {result["metadata"]["PartialMonths"]}. '
            f'Origin countries: {geo["origin_countries"] or "unrestricted"}; '
            f'destination countries: {geo["destination_countries"] or "unrestricted"}. '
            f'Excluded warehouse codes: {eligibility["excluded_sites"]["warehouse_codes"]} '
            f'in directions {eligibility["excluded_sites"]["directions"]}. '
            f'{excluded_mode_summary}'
            f'Minimum shipments/calendar month per lane-mode: {eligibility["minimum_shipments_per_calendar_month"]}. '
            f'Ranking: {config["ranking"]["scenario"]}, by net estimated savings USD.'
        ) + "</p>",
        "<p>Currency USD; weight lb. Percentage fields below are decimal fractions (0.25 = 25%)."
        " Displayed decimals are rounded; CSVs and JSON retain precision.</p>",
        "<h2>Scope and eligibility</h2>", table(display(result["waterfall"])),
        "<h2>Warehouse city mapping audit</h2>",
        table(display(result["city_mapping"])),
        "<p>Conflicting or missing city names are left blank and flagged; warehouse codes remain lane keys.</p>",
        "<h2>Scenario comparison</h2>", table(display(result["summary"])),
        f"<p>Weekly minus Tuesday/Friday estimated savings: ${incremental:,.2f}. "
        f"Further shipment-count reduction: {count_change:,}. These do not include additional holding, inventory or service costs.</p>",
    ]
    for s, rows in result["top5"].items():
        main.extend(["<h2>Top opportunities: " + s + "</h2>", table(display(rows))])
    main.extend([
        "<h2>Selected lanes: all scenarios side by side</h2>", table(display(result["side_by_side"])),
        "<h2>Selected modes</h2>", table(display(result["selected_modes"])),
        "<h2>Rate and weight diagnostics</h2>", table(display(result["rate_diagnostics"])),
        "<h2>Batch examples</h2>", table(display(result["examples"])),
        "<h2>Example original shipments</h2>", table(display(result["example_members"])),
        "<h2>Assumptions and limitations</h2><p>Highest-weight shipment reference rate, NOT the lowest observed batch rate."
        " Tie rule: " + html.escape(config["reference_tie_policy"]) + ". Each batch becomes one shipment."
        " Negative savings are retained; singleton savings are zero."
        " Reference rate is assumed applicable to the entire combined weight. No carrier acceptance, capacity,"
        " service feasibility, minimum-charge, fixed-fee or accessorial validation is established."
        " No annualization, outlier removal or best-scenario switching. Boundary batches use in-scope shipments only.</p>"
        "<p>Ranked zero or negative estimates are not positive savings opportunities. Lower shipment count does not guarantee"
        " lower cost. Examine small weights and rate dispersion before treating estimates as achievable benefits.</p>"
        "<p>Discuss holding periods, receiver frequency, weight/cube limits, rate outliers and carrier pricing validation.</p>"
        "</body></html>",
    ])
    return "".join(main)


def run(input_path, config, output_path):
    require(not Path(output_path).exists(), "Output path already exists; choose a new directory. No overwrites allowed.")
    input_hash = hashlib.sha256(Path(input_path).read_bytes()).hexdigest()
    rows, metadata = load_shipments(input_path, config)
    result = analyze(rows, config, metadata)
    require(hashlib.sha256(Path(input_path).read_bytes()).hexdigest() == input_hash,
            "Source file changed during analysis; rerun against a stable extract.")
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=False)
    for name in ("waterfall", "frequency", "summary", "lanes", "modes", "side_by_side", "batches", "members",
                 "examples", "example_members", "rate_diagnostics", "eligibility_decisions", "selected_modes",
                 "city_mapping"):
        write_csv(output / (name + ".csv"), result[name])
    write_csv(output / f'top{config["ranking"]["top_n"]}.csv',
              [{"RankingScenario": s, "Rank": rank, **r}
               for s, rows in result["top5"].items() for rank, r in enumerate(rows, 1)])
    manifest = {
        "engine_version": "1.0", "python_version": sys.version, "decimal_precision": getcontext().prec,
        "input_sha256": input_hash,
        "config": config, "metadata": metadata,
        "empty_opportunity_scope": not result["batches"],
        "checks": ["unique scoped IDs", "positive finite costs and weights", "key semantics",
                   "disjoint mode, bidirectional site and frequency exclusion categories", "cost waterfall",
                   "scenario count and cost reconciliation", "city mapping conflict/missing audit",
                   "warehouse codes retained as lane keys"],
    }
    with (output / "manifest.json").open("x", encoding="utf-8") as f:
        json.dump(manifest, f, default=str, indent=2)
    with (output / "results.json").open("x", encoding="utf-8") as f:
        json.dump(result, f, default=str, indent=2)
    with (output / "report.html").open("x", encoding="utf-8") as f:
        f.write(report_html(result, config))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Local UTF-8 CSV; never modified.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory only.")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        result = run(args.input, config, args.output)
    except (AnalysisError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output), "summary": result["summary"]}, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
