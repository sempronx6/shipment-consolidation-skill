# Shipment consolidation skill

A local, auditable shipment-consolidation screening workflow for GitHub Copilot.
Compare daily, Tuesday/Friday and weekly batches by origin-destination lane and transport mode,
estimate costs using the highest-weight shipment's unit rate, and generate traceable reports.

**This is directional screening, not a carrier quote or an operational consolidation plan.**
The distributed records, warehouse identifiers and city labels are fictional. No customer data
or customer benchmark findings are included.

## What is included

The complete reusable skill lives in `.github\skills\shipment-consolidation\`:

| File or directory | Purpose |
|---|---|
| `SKILL.md` | Agent instructions and safeguards |
| `scripts\consolidation.py` | Python engine and report generation |
| `scripts\validate_run.py` | Saved-run consistency checks |
| `references\methodology.md` | Input schema, calculations and policy choices |
| `examples\synthetic.csv` and `synthetic.json` | Fabricated runnable demonstration |
| `examples\project-example.json` | Illustrative profile to copy and replace |
| `tests` and `VALIDATION.md` | Regression tests and reproducible expectations |

The repository root contains contributor guidance, MIT licensing and a synthetic-only CI workflow.
A GitHub repository stores and versions these files. A GitHub Projects board is optional and is
not needed to run or install the skill.

## Team quick start

The shared source is [sempronx6/shipment-consolidation-skill](https://github.com/sempronx6/shipment-consolidation-skill).
For the project-level team setup, each teammate clones it and opens the repository root in a
supported Copilot agent environment:

```powershell
git clone https://github.com/sempronx6/shipment-consolidation-skill.git
Set-Location shipment-consolidation-skill
```

The skill is already included under `.github\skills\shipment-consolidation`; no personal
installation or second copy is needed in this repository. Start a fresh Copilot session and use
the fabricated demonstration prompt in section 3. Python 3.10+ and local execution permission
are required. Use `git pull --ff-only` to receive reviewed changes when the checkout is clean;
if local changes conflict, stop and preserve them rather than forcing an update.

To use the skill inside another team repository, use the project-scope installation in section 3
after approval from that repository's maintainers. Public read access allows cloning; it does not
grant write access to this source repository. No collaborator access is created by these steps.
Keep real client inputs, profiles and generated reports local and outside version control.

## 1. Check requirements

- Python 3.10 or later; the engine uses only the standard library, so no pip installation is needed.
- A Copilot surface with agent-skill support and permission to execute local Python commands.
- Git for development; GitHub CLI is optional for repository and skill management.
- Optional Power BI extraction needs a separately configured model connection and authorization.
  A local shipment CSV does not require Power BI or an MCP server.

Check installed tools in PowerShell:

```powershell
python --version
git --version
gh --version
```

Commands below use Windows PowerShell paths. On other operating systems use native paths;
the Python engine itself is cross-platform.

## 2. Run the fabricated demonstration

From the repository root:

```powershell
python -B -m unittest discover -s .\.github\skills\shipment-consolidation\tests -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed; inspect the error." }

$Skill = Join-Path (Get-Location) ".github\skills\shipment-consolidation"
$RunRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("consol-demo-" + [guid]::NewGuid().ToString("N"))
$Run = Join-Path $RunRoot "run"
$Validation = Join-Path $RunRoot "validation.json"

python -B "$Skill\scripts\consolidation.py" `
  --input "$Skill\examples\synthetic.csv" `
  --config "$Skill\examples\synthetic.json" `
  --output $Run
if ($LASTEXITCODE -ne 0) { throw "Demonstration failed; inspect the error." }

python -B "$Skill\scripts\validate_run.py" --run $Run --output $Validation
if ($LASTEXITCODE -ne 0) { throw "Validation failed; do not report the run as validated." }

Write-Output "Report: $Run\report.html"
Write-Output "Validation: $Validation"
```

Expected: 10 input records, 1 outside DE-DE geography, 1 excluded mode, 1 excluded site,
3 low-frequency exclusions, and 4 retained shipments costing USD 54.

| Scenario | Consolidated batches | Estimated savings USD |
|---|---:|---:|
| Legacy daily key, **not true daily consolidation** | 3 | 2.00 |
| Tuesday/Friday | 2 | 6.00 |
| Weekly | 1 | 16.20 |

This named demonstration uses the fixture's explicit 1 January-31 August 2026 period and
0.5/month threshold. Neither is a default for real data. Details are in
[VALIDATION.md](.github/skills/shipment-consolidation/VALIDATION.md).

## 3. Use it with Copilot

### In this repository

Open the repository root in a supported Copilot agent environment and start a fresh session.
The standard `.github\skills\shipment-consolidation\SKILL.md` location enables project-skill
discovery. Discovery still depends on the Copilot surface/version; it is not proved by a Python run.

Use this safe first prompt:

> Use the shipment-consolidation skill to run only its fabricated demonstration. Use the
> synthetic profile's explicit period of 1 January-31 August 2026. Preserve all source files,
> choose a new local output directory, run the saved-output validator, and report the expected
> counts and scenario savings. Do not access customer data or upload anything.

Check that Copilot loads this skill, runs its scripts, gets a PASS from the validator and
reports the expected fixture results. If the skill is missing, verify the workspace root,
directory name, SKILL.md filename and skill support, then restart the session.

### Reuse in another repository or across personal projects

The commands below use the published source repository; substitute your own owner/repository
if you maintain an independent copy.
Inspect the skill before installing it; installation creates local files and must be approved.
GitHub CLI skill commands are preview features, so check `gh skill install --help` on your version.

```powershell
$Repository = "sempronx6/shipment-consolidation-skill"
gh skill preview $Repository shipment-consolidation --allow-hidden-dirs
```

For project scope, first change to the target repository root, then:

```powershell
gh skill install $Repository shipment-consolidation --agent github-copilot --scope project --allow-hidden-dirs
```

For personal scope, shared across local projects:

```powershell
gh skill install $Repository shipment-consolidation --agent github-copilot --scope user --allow-hidden-dirs
```

Do not use `--force` to overwrite an existing installation without reviewing and approving it.
Use `gh skill list` to inspect installed skills. Project locations supported by Copilot include
`.github\skills`, `.agents\skills` and `.claude\skills`; the CLI selects its supported destination.
Personal locations include `~\.copilot\skills` and `~\.agents\skills`. A local personal installation
does not automatically become available in a cloud session.

Alternatively, copy the **whole** `shipment-consolidation` folder, not only SKILL.md, into a
supported skills directory after checking that no destination already exists. Keep scripts,
examples, tests and references together. A root-level SKILL.md outside supported skill locations
can be read explicitly but does not by itself establish automatic discovery.

## 4. Prepare real input safely

Read [the methodology](.github/skills/shipment-consolidation/references/methodology.md).
Use one CSV row per shipment; preserve identifier strings and original source files.
Confirm the user's explicit inclusive period, date meaning, USD costs, pound weights,
actual-country fields, complete extract coverage and verified city mappings.

Copy `project-example.json` to a new **local, untracked** profile. Replace every fictional
choice and attestation, including dates, excluded modes/sites, threshold, grouping, tie rule,
ranking and evidence. The engine can validate required text and arithmetic, but cannot prove
the truth of approval or coverage statements. Do not reuse example attestations as evidence.

Both sample profiles demonstrate legacy daily grouping: the same day number across different
months may be pooled. Never present it as true daily grouping or silently switch methods.
For true daily grouping, agree the change and use:

```json
{"source": "derive", "rule": "calendar_date"}
```

The engine stops on invalid numeric inputs, duplicate scoped IDs, unsupported units, inconsistent
stored keys and unresolved partial months. Missing/conflicting cities are explicitly flagged
without changing warehouse-code lane keys. No automatic deduplication, conversion, outlier
removal, capacity cap or carrier repricing is performed.

Run the engine and validator with your new input/profile/output paths, as in the demonstration.
Keep real data, profiles and generated reports outside the repository. Ignore rules are a
safety aid, not a guarantee: always review staged files before committing.

## 5. Create and publish your GitHub repository

The shared source above is already published. These instructions are for maintainers creating
a separate new repository; do not rerun repository creation for the existing shared source.
Before running them, confirm
publication rights, copyright attribution, the exact owner/name and the complete sanitized file list.
Never upload real shipment records, local profiles, reports, secrets or historical client findings.

From the reviewed public-safe repository folder, if Git has not yet been initialized:

```powershell
git init -b main
git status --short --untracked-files=all
git add -- README.md LICENSE AGENTS.md .gitignore .github
git diff --cached --stat
git diff --cached
```

Review the staged content, then commit and, **only after publication approval**, create and push:

```powershell
git commit -m "Initial public shipment consolidation skill" `
  -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"

$Repository = "OWNER/REPOSITORY"
gh repo create $Repository --public --source . --remote origin --push
gh repo view $Repository --json nameWithOwner,url,visibility
gh run list --repo $Repository --limit 5
```

If GitHub authentication is unavailable, sign in through an approved account flow before retrying.
If the destination repository already exists, stop and inspect it rather than overwriting or
force-pushing. Check the actual remote file list and CI conclusion; a push alone is not a CI pass.
Use the repository's URL in the installation commands only after it exists.

## 6. Maintain and release

Edit the canonical skill files, run the local suite, inspect the diff, commit, push and check CI.
Keep real client analysis local; use only fabricated fixtures in tests and workflows.
For installed copies, review updates before running `gh skill update shipment-consolidation`.
Never treat a local install as a replacement for maintaining the canonical source.

The CI matrix covers Windows and Linux with Python 3.10 and 3.12 and runs unit tests plus the
synthetic CLI/expected-result/saved-run integration checks. Hosted CI is verified only when its
actual workflow run succeeds.

From the repository root, `gh skill publish --dry-run .\.github` checks skill metadata without
publishing. Pass the `.github` parent explicitly so the CLI finds its nested `skills` directory;
the default search may skip hidden directories. Do not use `gh skill publish .\.github`
without `--dry-run` unless you intend and have approved creating a public
release/tag. Releases are optional for installing from the default branch. For repeatability,
install a reviewed release or commit using `--pin` when appropriate.

## Limitations and licensing

Estimates apply one highest-weight shipment's rate to combined weight. They do not prove carrier
acceptance, service feasibility, capacity, minimum-charge, fixed-fee or accessorial treatment.
Retain negatives, discuss holding/receiver constraints, and validate representative carrier pricing.
Do not annualize or promise customer savings without authorization.

Code and documentation are distributed under the [MIT license](LICENSE). Confirm that you have
the rights to publish your contributions; the license does not authorize publishing customer data.

Official references: [Copilot agent skills](https://docs.github.com/en/copilot/concepts/customizations/agent-skills)
and [adding skills](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills).
