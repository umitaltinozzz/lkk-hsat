[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase4b:native",
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ"),
    [int]$Limit = 0,
    [switch]$SkipFlowCampaign
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$output = Join-Path $repo "results\phase4b\$RunId"
if (Test-Path $output) { throw "Phase 4B output already exists: $output" }
$imageId = docker image inspect $Image --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw "Missing Phase 4B image: $Image" }
$extra = @()
if ($Limit -gt 0) { $extra += @("--limit", "$Limit") }
if ($SkipFlowCampaign) { $extra += "--skip-flow-campaign" }
docker run --rm --volume "${repo}:/work" --env "LKK_NATIVE_IMAGE_ID=$imageId" --entrypoint python3 $Image `
    -m phase4b.run_phase4b --output "/work/results/phase4b/$RunId" --run-id $RunId @extra
if ($LASTEXITCODE -ne 0) { throw "Phase 4B stopped; preserved output: $output" }
Write-Output "Phase 4B results: $output"
