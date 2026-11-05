# Methodology and configurable decisions

## Required inputs and mappings

The model fields below are illustrative mappings, not claims about an existing customer model.
Inspect actual metadata and confirm each field before extraction.

| Canonical mapping | Example Power BI field | Example CSV field | Meaning |
|---|---|---|---|
| shipment_id | Shipments[ShipmentID] | Load | Unique shipment ID, one record per shipment |
| activity_date | Shipments[ActivityDate] | Date | Scope and batch assignment date |
| origin | Shipments[OriginWarehouseCode] | Origin | Actual origin point code |
| destination | Shipments[DestinationWarehouseCode] | Destination | Final delivery point code |
| mode | Shipments[Mode] | Mode | Transport mode; never merge different modes |
| cost | Shipments[CostUSD] | CostUSD | Shipment freight in USD |
| weight | Shipments[WeightLB] | WeightLB | Shipment weight in pounds |
| origin_city | Shipments[OriginCity] | OriginCity | City associated with origin warehouse code |
| destination_city | Shipments[DestinationCity] | DestinationCity | City associated with destination warehouse code |
| origin_country | Shipments[PickupCountry] | PickCountry | Actual pickup country |
| destination_country | Shipments[DeliveryCountry] | DropCountry | Actual final delivery country |
| daily_key | Daily_consol | Daily | Full calendar-date key, ISO YYYY-MM-DD |
| twice_weekly_key | WeeklyTueFri_consol | TueFri | Saturday-Tuesday / Wednesday-Friday key |
| weekly_key | Weekly_consol | Weekly | Saturday-Friday weekly key |

Both city mappings are mandatory. Source them from shipment records or a separately verified
warehouse-code-to-city lookup joined before analysis. The engine allows blank city cells only
to produce an explicit mapping warning. It groups city observations by warehouse code across
both origin and destination roles. Conflicting city values resolve to blank/CONFLICT, not a
first-seen or majority guess; wholly absent values resolve to blank/MISSING. Case and outer
whitespace differences alone are normalized. `city_mapping.csv` records observed values and
missing cells. Lane, batch and output grouping always uses warehouse codes, never city names.
For a verified aggregate mapping extract, `location_mapping.mapping_file` may point to a UTF-8
CSV with `WarehouseCode`, `City`, and positive integer `Observations` columns (one row per
warehouse-code/city pair; blank city represents missing source values). The engine uses this
file as the authoritative city audit and preserves conflict/missing observation counts. Include
the mapping extract with the run artifacts and record its source and verification evidence.
Country mappings are mandatory unless upstream geographical filtering is explicitly attested.
Scenario columns are required only when their source is `column`; `derive` needs ActivityDate.
Optional source fields such as StopCount, TotalWeightMetric, capacity, cube and service constraints
should be inspected by the agent. They do not silently create exclusions or change the model.
StopCount = 2 means direct endpoints; >2 indicates intermediate stops. Endpoint lanes alone
do not describe a multi-stop route. Confirm the source's stop-count definition and coverage.

## Scope and frequency

Every run requires an explicit user-provided inclusive analysis period, date meaning and
timezone/date-handling rule. Do not copy example dates into an unrelated project. If the
period is absent, ask before extraction/calculation. Apply the period and actual-country
scope first. Next:

1. Exclude a shipment if its mode matches `eligibility.excluded_modes`. Mode labels are trimmed
   and compared case-insensitively. This explicit list is configurable; `[]` means no mode
   exclusions. When supplied, record the business rationale in the profile. Mode-excluded loads
   are reported separately and do not enter site exclusions, lane-mode frequency, batching,
   savings or rankings. The fictional example excludes `Air` to demonstrate the configurable
   rule. It is not a general claim about air freight savings; confirm exclusions for each project.
2. Among remaining modes, exclude a shipment if either configured endpoint matches an excluded site. Which directions
   are checked is an explicit business rule. The fictional example excludes BOTH origin and
   destination warehouse codes SYN_EXCLUDED_A and SYN_EXCLUDED_B for its illustrative period.
3. Group remaining shipments by `(origin, destination, mode)`.
4. Compute `average_monthly_shipments = shipment_count / calendar_months_in_scope`.
5. Keep combinations with average >= configured threshold; apply before batching or ranking.

All calendar months in scope count, even when an individual lane-mode has zero shipments.
Eight complete months at 10/month imply >=80 loads, not a minimum of 10 in each individual month.
Monthly counts in `frequency.csv` make zeros visible. Mode, site and frequency exclusion categories are sequential and mutually exclusive. The
waterfall reports counts and actual costs at each stage. Lane counts themselves need not add
because a lane may have qualifying and nonqualifying modes.

The site rule is configurable: `eligibility.excluded_sites.warehouse_codes` lists codes and
`eligibility.excluded_sites.directions` explicitly lists `"origin"`, `"destination"` or both.
This replaces origin-only exclusions. Site exclusion is applied once per shipment even if
both endpoints match exclusions.

For partial start/end months, stop before calculation and ask the user. The engine supports
`partial_month_policy = count_each_touched_month` only with a nonempty
`partial_month_confirmation` copied from an actual user decision. This counts each partial
month as one whole denominator month. Prorating or dropping partial months is NOT implemented;
agree and extend the method before using those choices. Never infer the period from min/max
observed shipment dates.

## Confirmed batch rules

Batch identity is `(origin, destination, mode, scenario_key)`. Use structured tuple keys, not
ambiguous string concatenation.

- **Daily:** the full calendar date (year-month-date), serialized as ISO `YYYY-MM-DD`.
  Thus 14 January and 14 July always belong to separate batches. Profiles must declare
  `calendar_date`, either derived from ActivityDate or supplied as a matching full-date key.
  The former `legacy_year_day_of_month` rule (`YEAR(ActivityDate)*1000 + DAY(ActivityDate)`)
  omits the month and is unsupported, even with an acknowledgement. Old profiles and stored
  keys must be explicitly updated and regenerated from full shipment dates before rerunning;
  they are never silently migrated. Daily batches cannot span different dates.
- **Tuesday/Friday:** `WEEKNUM(ActivityDate,16)` with Saturday as week start; Saturday-Tuesday
  is Batch1 and Wednesday-Friday is Batch2. Key is
  `(YEAR(ActivityDate)*100 + WEEKNUM(...,16)) & "Batch1/Batch2"`.
- **Weekly:** the same calendar-year week number, always `"Batch1"`, covering Saturday-Friday.

Tuesday/Friday and Weekly use DAX calendar-year week rules, NOT ISO week-year rules. Weeks crossing 1 January are
split by calendar year; this preserves the stored logic. Period-edge batches include only
in-scope records, never borrow from outside the period. Nonzero time of day does not change
the calendar-date assignment. Cross-month twice-weekly/weekly batches can be legitimate.

When using supplied keys, the engine compares every scoped row with the declared rule.
Unexpected keys block execution rather than silently regrouping. Canonical string keys are
required, for example Daily `2026-01-14` or Weekly `202632Batch1`. Legacy Daily keys such as
`2026014` are rejected.

## Reusable calculations

For a batch of n original shipments with positive finite USD cost and pounds:

```text
Reference candidates = original shipments with WeightLB = maximum batch WeightLB
Reference shipment = lowest unit rate among those candidates (confirmed tie rule)
Reference rate = reference original shipment cost / reference original shipment pounds
Original batch cost = sum(original shipment costs)
Combined batch pounds = sum(original shipment pounds)
Estimated consolidated cost = combined pounds * reference rate
Estimated savings = original batch cost - estimated consolidated cost
```

The reference is the **highest-weight shipment reference rate**, NOT the lowest observed
rate across all originals. Equal weight means equal stored Decimal value; do not introduce
an unapproved tolerance. Equal-weight, equal-cost ties select lexical shipment ID solely
to make traceability deterministic.

Every nonempty batch becomes one shipment. For a singleton, estimated cost equals actual
cost exactly, avoiding roundoff-generated savings. For lanes, modes or the full scope:

```text
Current shipments = sum(original batch shipment counts)
Consolidated shipments = number of batches
Shipment reduction = current shipments - consolidated shipments
Shipment reduction % = shipment reduction / current shipments
Average originals per batch = current shipments / consolidated shipments
Savings % = sum(batch savings) / sum(original batch costs)
```

Use ratios of sums, not unweighted means of batch percentages or rates. Retain negative
savings at all levels. Do not assume savings must improve as batch size increases: the
selected reference shipment may change. Empty opportunity scopes return zero totals with
undefined percentages as null/N/A, not fabricated positive opportunities.

## Separation of logic from business policy

| Reusable engine mechanics | Explicit profile decisions |
|---|---|
| Validate one shipment per ID | Explicit period and authoritative date meaning |
| Calendar-month denominator, including zeros | Actual-country scope and evidence |
| Sequential, disjoint exclusions | Excluded warehouse codes and endpoint directions |
| Lane-mode eligibility and batch boundaries | Monthly threshold |
| Highest-weight reference selection | Declared tie policy |
| Weight x reference-rate estimate | Supplied or derived batch rule |
| Mode/lane rollups, traceable audit | Ranking scenario and top-N |
| City mapping audit and lane output enrichment | Verified city source |
| Negative savings retained | Partial-month treatment when applicable |

Ranking is by net USD savings descending; ties use lexical origin then destination for
stable display only, not a different benefit definition. With `ranking.scenario = separate`,
produce separate lists. With one named scenario, select that list then compare all scenarios
for those lanes. If fewer than five lanes qualify, show only those available. Nonpositive
ranked entries must be described as nonpositive estimates, not savings opportunities.

## Data-quality gates and limits

- Stop on blank required fields, malformed CSV, invalid dates, duplicate scoped shipment IDs,
  nonfinite numbers, zero/negative cost or pounds, inconsistent grouping keys or unsupported units.
- Require origin/destination city column mappings or a verified location mapping. City values
  missing on all records or conflicting for a warehouse code are flagged and left unresolved;
  warehouse-code lane keys and cost calculations remain intact.
- Include resolved origin and destination cities beside endpoint codes throughout rankings,
  lane and mode tables, batch-level and shipment-level detail. Never group by city.
- Require explicit period input; no project period is the general default. The Jan-Aug 2026
  dates exist only in fictional example profiles and are not defaults.
- No automatic credit filtering, default weights, deduplication, pooling of currencies,
  mode remapping, or rate substitution. Ask for the missing rule and revise explicitly.
- Strict schema checks inspect required fields even in rows outside final eligibility.
  Shipment ID and numeric checks apply inside period/geographic scope, before site/frequency rules.
- Validating prefiltered country scope depends on its attestation. Obtain upstream row-count
  and cost reconciliation and preserve extract completeness evidence.
- The engine preserves original input, refuses existing output directories and records a
  SHA-256 input hash, full config, Python version and calendar denominator in `manifest.json`.
- Decimal precision is 40 significant digits; no per-shipment or per-batch currency rounding.
  Display rounding and CSV decimal serialization are not new pricing rules.
- Weight/rate min, median and max diagnostics are descriptive; no arbitrary outlier cutoff
  is used to delete or cap records. Tiny weights can amplify observed USD/lb.

This is a directional screening estimate. It assumes the selected reference rate applies to
the full combined weight. It does not confirm carrier acceptance, equipment/cube/weight capacity,
transit or holding-time feasibility, minimum charges, fixed fees, accessorials, inventory cost,
receiver constraints or customer commitments. Scenario differences exclude holding, inventory
and service costs. Do not annualize without approval.

## Standard output contract

`report.html` presents the following tables and caveats. CSVs retain precision:

- `waterfall.csv`: original scope, mode exclusions, after modes, site exclusions, after sites,
  low frequency among remaining shipments, and retained.
- `eligibility_decisions.csv`: shipment-level mutually exclusive decision for complete reconciliation.
- `frequency.csv`: lane-mode totals, month denominator, monthly zero counts, eligibility and spend.
- `summary.csv`: all three scenarios, count/cost/savings metrics and negative-batch diagnostics.
- `lanes.csv`, `modes.csv`: all eligible lane and mode-level scenario metrics.
- `top{N}.csv` (N is configured `top_n`), `side_by_side.csv`, `selected_modes.csv`: configured rankings, all-scenario comparison,
  selected-lane mode detail and scenario savings differences.
- Lane outputs include origin/destination warehouse codes, resolved city names, and each
  endpoint's mapping status. `city_mapping.csv` lists resolved, missing and conflicting mappings.
- `batches.csv`, `members.csv`: every batch and every original shipment membership for all scenarios.
- `examples.csv`, `example_members.csv`: leading batch for each ranked lane and its original shipments,
  selected reference, weight, cost and rate, allowing the entire calculation to be reproduced.
- `rate_diagnostics.csv`: eligible lane-mode shipment size and observed rate range.
- `results.json`, `manifest.json`: machine-readable results and provenance/configuration.

Interpret results with targeted questions on storage/holding, service promises, vehicle/cube
limits, unusual weights/rates, retained fixed costs and representative carrier quote validation.
