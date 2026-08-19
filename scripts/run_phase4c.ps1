[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase4c:native",
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ"),
    [switch]$SkipFlowCampaign
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$output = Join-Path $repo "results\phase4c\$RunId"
if (Test-Path $output) { throw "Phase 4C output already exists: $output" }

$imageId = docker image inspect $Image --format '{{.Id}}' 2>$null
if ($LASTEXITCODE -ne 0) {
    docker build -f Dockerfile.phase4c -t $Image .
    if ($LASTEXITCODE -ne 0) { throw "Phase 4C image build failed" }
    $imageId = docker image inspect $Image --format '{{.Id}}'
}
$mount = "${repo}:/work"

Write-Output "-- core drift test (the shared core must still match the sealed engine)"
docker run --rm --volume $mount --entrypoint python3 $Image -m unittest native.core.test_core_drift
if ($LASTEXITCODE -ne 0) { throw "Core drift test failed; the shared core diverged from Phase 4A" }

Write-Output "-- campaign start $(Get-Date -Format o)"
$extra = @()
if ($SkipFlowCampaign) { $extra += "--skip-flow-campaign" }
docker run --rm --volume $mount --env "LKK_NATIVE_IMAGE_ID=$imageId" --entrypoint python3 $Image `
    -m phase4c.run_phase4c --output "/work/results/phase4c/$RunId" --run-id $RunId @extra
$code = $LASTEXITCODE
Write-Output "-- campaign end $(Get-Date -Format o) (exit $code)"
if ($code -eq 2) { throw "Phase 4C stopped at the correctness gate; preserved output: $output" }
if ($code -ne 0) { throw "Phase 4C failed with exit $code; preserved output: $output" }

Write-Output "-- regression suites"
docker run --rm --volume $mount --entrypoint python3 $Image -m unittest `
    benchmark.test_harness phase2.test_phase2 phase3.test_phase3 `
    phase4a.test_phase4a phase4b.test_phase4b phase4c.test_phase4c phase5.test_phase5
if ($LASTEXITCODE -ne 0) { throw "Regression suites failed after the campaign" }

Write-Output "Phase 4C results: $output"
