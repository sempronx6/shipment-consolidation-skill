---
name: shipment-consolidation
description: Assess shipment-consolidation opportunities from a local shipment CSV, compare daily, Tuesday/Friday and weekly lane-mode batches, estimate costs with the highest-weight shipment reference rate, and produce traceable rankings, reports and validation.
license: MIT
---

# Shipment consolidation screening

Use this skill for directional consolidation screening, not executable carrier pricing.
Run locally. Preserve source data and save each run to a new directory. Do not send customer
records to external services, edit source models, overwrite files, install software, or publish
results without the necessary user authorization.

Resolve all paths relative to this skill's directory, not the agent's initial working directory.
The installed directory includes `scripts`, `references`, `examples`, and `tests`.
The CSV engine needs Python 3.10+ and only the standard library. No MCP server is needed for CSV use.

## 1. Confirm the analysis contract

Read `references/methodology.md` before calculating. Require the user's explicit analysis period
for every real run; never copy example dates into an unrelated analysis or infer scope from date
extrema. Confirm one row per shipment, authoritative date meaning, USD costs, pounds, actual
endpoint geography, complete extraction coverage, and column mappings.

Keep calculation mechanics in `scripts/consolidation.py` separate from JSON business-rule profiles.
Copy `examples/project-example.json` to a new local file outside version control and replace its
fictional values and attestations. Confirm excluded modes and rationale, excluded sites and
endpoint directions, lane-mode monthly threshold, grouping, tie policy, city evidence and ranking.
The illustrative 10/month threshold is not a default. The synthetic demonstration uses 0.5/month.

Pause for unresolved partial months, duplicates, credits, zero weights, currency/unit conversions,
timezone-bearing timestamps, incomplete coverage or ambiguous business rules. Ask one targeted
question at a time; do not silently drop records or invent a resolution.

## 2. Acquire and inspect inputs

Input is a UTF-8 CSV with one record per shipment. Preserve shipment IDs and warehouse codes as
strings, including leading zeros. Use the mapping table in `references/methodology.md`.
Map origin/destination city fields from verified source data; never guess locations.
Country fields must represent actual pickup and final delivery, not billing or customer country.
If country fields are absent from an already filtered extract, require explicit upstream filter
evidence in `geography.prefilter_evidence`. The engine cannot independently verify an attestation.

Optional Power BI extraction requires separately available model-connection tools and user
authorization. Inspect actual metadata and grouping expressions, connect to the correct model,
query read-only, check row limits/truncation, and reconcile extract counts and costs. Export to a
new local CSV with provenance; never modify the model. If the necessary tools are unavailable,
request a local CSV rather than claiming extraction was performed.

City observations are checked across both endpoint roles for each warehouse code. Conflicts
remain blank/CONFLICT; wholly missing names remain blank/MISSING. They do not change lane keys.
A verified aggregate mapping CSV is supported by `location_mapping.mapping_file`, with
`WarehouseCode`, `City`, and positive integer `Observations` columns. Preserve the mapping extract
and its provenance outside version control. Relative mapping-file paths resolve from the
process working directory, so prefer an absolute path for real runs.

## 3. Apply only confirmed mechanics

Apply period/geography scope, mode exclusions, site exclusions, then frequency eligibility.
Mode labels in the excluded list are case-insensitive. Site exclusions apply once if either
configured endpoint matches. Frequency eligibility is per origin-destination-mode, including
zero-shipment months in the denominator; one eligible mode cannot rescue another.

All three scenarios retain the same eligible shipment baseline and never mix modes:

- Daily: use the explicitly agreed stored/derived rule. `legacy_year_day_of_month` pools the
  same day number across months and MUST be labeled "stored daily key - NOT true daily
  consolidation." It requires explicit user approval. The included profiles demonstrate this
  legacy behavior, not approval for a real analysis. True daily grouping uses
  `{"source":"derive","rule":"calendar_date"}` in a separately approved profile.
- TueFri: Saturday-Tuesday and Wednesday-Friday.
- Weekly: Saturday-Friday, with calendar-year week splits rather than ISO week-year rules.

Reference the highest-weight shipment in each batch, not the lowest rate among all shipments.
For tied maximum weights, use the lowest rate only among those tied shipments when confirmed;
equal-rate ties use lexical shipment ID for traceability. Estimate consolidated cost as combined
weight times the reference rate. Singletons have zero savings; retain negative savings.
No automatic rate caps, capacity limits, service exclusions or annualization are implemented.

## 4. Execute and reconcile

From this skill's directory, with a new output directory:

```powershell
python -B .\scripts\consolidation.py `
  --input "C:\Data\shipments.csv" `
  --config "C:\Data\profiles\approved-project.json" `
  --output "C:\Data\Results\consolidation-run-01"

python -B .\scripts\validate_run.py `
  --run "C:\Data\Results\consolidation-run-01" `
  --output "C:\Data\Results\consolidation-validation-01.json"
```

For a fabricated demonstration only, use `examples/synthetic.csv` and `examples/synthetic.json`,
whose declared illustrative period is 1 January-31 August 2026. A request to run this named demo
may use that fixture period; never treat it as the period for new customer data.
See `VALIDATION.md` for exact expected results and the repository README for copyable commands.

Before reporting success, verify:

1. Scoped shipments and actual cost reconcile to disjoint mode, site, frequency and retained totals.
2. Each retained shipment belongs to exactly one batch per scenario, without mixing modes.
3. Scenario counts/costs share the same baseline; reference weights are batch maxima.
4. Singletons remain zero, negatives remain included, and rankings use net USD savings.
5. City mapping warnings propagate without city-based grouping or guessed names.
6. The output validator exits successfully; do not mistake a generated report for validated results.

Review `report.html`, `manifest.json`, `waterfall.csv`, `frequency.csv`, `summary.csv`,
`top{N}.csv`, `side_by_side.csv`, `selected_modes.csv`, `examples.csv` and `example_members.csv`.
Full lanes, modes, batches, memberships, city audit and rate diagnostics are also provided.
Percentage fields are decimal fractions; keep calculations unrounded. Empty eligible scopes
are valid zero-opportunity results, not a processing failure or evidence of positive savings.
Validation checks internal saved-run consistency; it does not prove extract completeness or
carrier feasibility.

## 5. Deliver an evidence-led assessment

Summarize eligible baseline, exclusion impact, largest net estimates, incremental weekly versus
twice-weekly benefit, drivers and evidence gaps. Keep row-level records in local audit outputs.
Distinguish facts, assumptions, estimates and recommendations. Flag tiny weights, rate dispersion,
large combined weights, negatives and unresolved city mappings without deleting or capping them.

Explain that applying one shipment's unit rate to combined weight does not establish carrier
acceptance, capacity, service feasibility, minimum charges, fixed fees or accessorial treatment.
Discuss holding periods, receiver frequency, weight/cube constraints and representative pricing.
Do not annualize, promise achievable savings or claim customer commitments without authorization.

## Example real-data request

> Use the shipment-consolidation skill on my local CSV and approved profile. My explicit analysis
> period is [start date] through [end date], inclusive. Validate units, mappings and coverage.
> Stop for unresolved exceptions, preserve the source, save new outputs, validate the run and
> explain the estimates with traceable examples. Do not upload my data.
