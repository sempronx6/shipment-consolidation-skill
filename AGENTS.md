# Contributor instructions

The canonical package is `.github\skills\shipment-consolidation`. Keep SKILL.md, relative
references, scripts and examples together so installed copies are self-contained.

Use Python 3.10+ and the standard library. Run from the repository root:

```powershell
python -B -m unittest discover -s .\.github\skills\shipment-consolidation\tests -v
```

The suite includes the fabricated CLI run, exact expected-result assertions and saved-run
validation. Update directly related documentation when behavior changes.

Preserve Decimal calculations, warehouse-code lane keys, highest-weight reference selection,
sequential disjoint exclusions, zero-month eligibility, negative savings and singleton zero
savings. Do not change business policy to repair implementation defects. Legacy daily keys
are cross-month pooling and must never be silently replaced or described as true daily.

Only fictional data and example attestations belong in version control. Do not add client
records, real profiles, completed reports, credentials or copied customer benchmarks.
Preserve input files and refuse existing output paths. Real runs require an explicit period
and confirmed units, coverage and policy decisions. Do not claim a check passed unless run.

Obtain approval before installation, overwrites, remote publication or release creation.
Repository visibility is not permission to upload client data. Do not introduce external
services or dependencies for this local workflow without an explicit need and authorization.
