[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase1:locked",
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$outputRelative = "results/phase2/$RunId"
$output = Join-Path $repo "results\phase2\$RunId"
if (Test-Path $output) {
    throw "Phase 2 output already exists and will not be overwritten: $output"
}

$imageId = docker image inspect $Image --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw "Missing locked Phase 1 image: $Image" }
$dockerVersion = docker version --format '{{.Server.Version}}'
$mount = "$($repo):/work"

docker run --rm `
    --volume $mount `
    --env "LKK_DOCKER_IMAGE_ID=$imageId" `
    --env "LKK_DOCKER_SERVER_VERSION=$dockerVersion" `
    --env "LKK_HOST_NAME=$env:COMPUTERNAME" `
    --entrypoint python3 `
    $Image `
    -m phase2.run_phase2 `
    --config /work/phase2/config.json `
    --output "/work/$outputRelative" `
    --run-id $RunId

if ($LASTEXITCODE -ne 0) {
    throw "Phase 2 validation failed with exit code $LASTEXITCODE; preserved output: $output"
}

Write-Output "Phase 2 results: $output"
