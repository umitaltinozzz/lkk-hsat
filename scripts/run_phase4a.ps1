[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase4a:native",
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$output = Join-Path $repo "results\phase4a\$RunId"
if (Test-Path $output) { throw "Phase 4A output already exists: $output" }
$imageId = docker image inspect $Image --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw "Missing native image: $Image" }
docker run --rm --volume "${repo}:/work" --env "LKK_NATIVE_IMAGE_ID=$imageId" --entrypoint python3 $Image -m phase4a.run_phase4a --output "/work/results/phase4a/$RunId" --run-id $RunId
if ($LASTEXITCODE -ne 0) { throw "Phase 4A stopped; preserved output: $output" }
docker run --rm --volume "${repo}:/work" --entrypoint python3 $Image -m phase4a.finalize_phase4a --run "/work/results/phase4a/$RunId"
if ($LASTEXITCODE -ne 0) { throw "Phase 4A final audit failed; preserved output: $output" }
Write-Output "Phase 4A results: $output"
