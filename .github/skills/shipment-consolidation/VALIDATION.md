# Validation and reproducibility

All distributed shipment records, warehouse identifiers and city labels are fictional.
There are no customer benchmarks or verified customer-extract attestations in this package.

## Run local checks

From this skill directory:

```powershell
python -B -m unittest discover -s .\tests -v
```

Tests cover highest-weight reference pricing, confirmed ties, deterministic identifiers,
zero savings for singletons, negative rollups, lane-mode eligibility including zero months,
sequential mode/site/frequency exclusions, endpoint directions, country filtering, explicit
periods, full and partial months, batch boundaries, stored-key semantics, invalid inputs,
duplicate IDs, source preservation, no overwrite, city conflicts/missing values, and city-based
display without changing warehouse-code lane keys.

Regression checks also cover repeated city observation counts and saved-run validation for
empty opportunities versus missing/inconsistent nonempty rankings. Aggregate mapping counts
remain authoritative when a verified mapping file is supplied.

## Fabricated demonstration

From this skill directory, choose paths that do not already exist:

```powershell
python -B .\scripts\consolidation.py `
  --input ".\examples\synthetic.csv" `
  --config ".\examples\synthetic.json" `
  --output "C:\Data\Results\synthetic-run-01"

python -B .\scripts\validate_run.py `
  --run "C:\Data\Results\synthetic-run-01" `
  --output "C:\Data\Results\synthetic-validation-01.json"
```

The fixture explicitly uses 1 January-31 August 2026 and a 0.5/month eligibility threshold to
demonstrate zero-month counting. These dates and this threshold are not defaults for real work.

| Stage | Shipments |
|---|---:|
| Input | 10 |
| Outside DE-DE geography | 1 |
| Scoped | 9 |
| Excluded mode | 1 |
| Excluded site among remaining modes | 1 |
| Excluded low frequency among remaining shipments | 3 |
| Retained | 4 |

Retained actual cost is USD 54.

| Scenario | Consolidated batches | Estimated cost USD | Savings USD |
|---|---:|---:|---:|
| Legacy daily key, not true daily | 3 | 52 | 2 |
| Tuesday/Friday | 2 | 48 | 6 |
| Weekly | 1 | 37.80 | 16.20 |

City-audit endpoint observation counts are SYN_ORIGIN = 8, SYN_DEST = 9 and SYN_EXCLUDED_A = 1.
These include all scoped records, before mode/site/frequency exclusions, across both endpoint roles.

The integration test independently asserts these fixture expectations and invokes the saved-run
validator. Both unit tests and the integration tests run through unittest discovery.

## Meaning and limits of a pass

The validator reads the saved profile and audit CSVs and checks exclusion counts/costs, lane-mode
eligibility, city propagation, batch/reference arithmetic and configured top-N ranking.
An empty opportunity scope is valid only with correspondingly empty lane and ranking outputs.
Missing or altered nonempty rankings are failures.

It is a consistency check, not an independent re-extraction of the source. It does not establish
complete dataset coverage, truth of configuration attestations, carrier pricing or operational
feasibility. No customer-data rerun is claimed. Copilot installation/discovery and hosted CI
must be checked separately; local Python success alone proves neither.
