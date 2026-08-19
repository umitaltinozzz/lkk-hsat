[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase1:locked",
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$output = Join-Path $repo "results\phase3\$RunId"
if (Test-Path $output) { throw "Calibration output already exists: $output" }
$imageId = docker image inspect $Image --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw "Missing locked solver image: $Image" }
$mount = "$($repo):/work"
docker run --rm `
    --volume $mount `
    --env "LKK_DOCKER_IMAGE_ID=$imageId" `
    --entrypoint python3 `
    $Image `
    -m phase3.calibrate `
    --config /work/phase3/config.json `
    --output "/work/results/phase3/$RunId" `
    --run-id $RunId
if ($LASTEXITCODE -ne 0) {
    throw "Calibration failed; preserved output: $output"
}
Write-Output "Calibration results: $output"
