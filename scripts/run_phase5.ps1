<#
.SYNOPSIS
Runs the complete Phase 5 campaign unattended.

.DESCRIPTION
Builds the Phase 5 image if needed, completes benchmark acquisition if the
corpus is incomplete, prints the run plan, then executes the full campaign:
telemetry and stratification, timeout calibration, selector fitting on the
calibration split, the mode A-E evaluation, the correctness gate, the component
ablation, plots and the report.

The campaign stops by itself if a definite-answer conflict is found. Results are
written to results/phase5/<RunId>/ and existing run directories are never
overwritten.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\run_phase5.ps1
#>
[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase5:native",
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ"),
    [int]$EvaluationLimit = 0,
    [switch]$PlanOnly,
    [switch]$SkipAcquire
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$output = Join-Path $repo "results\phase5\$RunId"
if (Test-Path $output) { throw "Phase 5 output already exists: $output" }

Write-Output "== Phase 5 run $RunId =="

# 1. Image
$imageId = docker image inspect $Image --format '{{.Id}}' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Output "-- building $Image"
    docker build -f Dockerfile.phase5 -t $Image .
    if ($LASTEXITCODE -ne 0) { throw "Phase 5 image build failed" }
    $imageId = docker image inspect $Image --format '{{.Id}}'
}

$mount = "${repo}:/work"

# 2. Corpus. Acquisition is idempotent: archives already present are reused and
#    only their checksums are re-verified.
if (-not $SkipAcquire) {
    Write-Output "-- acquiring benchmark corpus (skips anything already downloaded)"
    docker run --rm --volume $mount --entrypoint python3 $Image `
        -m phase5.acquire --skip-sc2024 --manifest /work/benchmarks/phase5/manifest_satlib.csv
    if ($LASTEXITCODE -ne 0) { throw "SATLIB acquisition failed" }
    docker run --rm --volume $mount --entrypoint python3 $Image `
        -m phase5.acquire --skip-satlib --sc2024-limit 250 --sc2024-max-mb 40 `
        --sc2024-budget-gb 40 --sc2024-max-cnf-mb 100 --sc2024-max-per-family 3 `
        --manifest /work/benchmarks/phase5/manifest_sc2024.csv
    if ($LASTEXITCODE -ne 0) { throw "SAT Competition acquisition failed" }
}

# 3. Plan. Counts runs and verifies the predecessor digests without measuring.
Write-Output "-- run plan"
docker run --rm --volume $mount --entrypoint python3 $Image `
    -m phase5.run_phase5 --output "/work/results/phase5/$RunId" --run-id $RunId `
    --evaluation-limit $EvaluationLimit --dry-run
if ($LASTEXITCODE -ne 0) { throw "Phase 5 planning failed" }
if ($PlanOnly) { Write-Output "Plan only; nothing measured."; exit 0 }

# 4. Campaign
Write-Output "-- campaign start $(Get-Date -Format o)"
docker run --rm --volume $mount --env "LKK_NATIVE_IMAGE_ID=$imageId" --entrypoint python3 $Image `
    -m phase5.run_phase5 --output "/work/results/phase5/$RunId" --run-id $RunId `
    --evaluation-limit $EvaluationLimit
$code = $LASTEXITCODE
Write-Output "-- campaign end $(Get-Date -Format o) (exit $code)"
if ($code -eq 2) { throw "Phase 5 stopped at the correctness gate; preserved output: $output" }
if ($code -ne 0) { throw "Phase 5 failed with exit $code; preserved output: $output" }

# 5. Suites
Write-Output "-- regression suites"
docker run --rm --volume $mount --entrypoint python3 $Image -m unittest `
    benchmark.test_harness phase2.test_phase2 phase3.test_phase3 `
    phase4a.test_phase4a phase4b.test_phase4b phase5.test_phase5
if ($LASTEXITCODE -ne 0) { throw "Regression suites failed after the campaign" }

Write-Output "Phase 5 results: $output"
