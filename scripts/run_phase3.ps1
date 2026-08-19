[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase1:locked",
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$output = Join-Path $repo "results\phase3\$RunId"
if (Test-Path $output) { throw "Phase 3 output already exists: $output" }
$imageId = docker image inspect $Image --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw "Missing locked solver image: $Image" }
$dockerVersion = docker version --format '{{.Server.Version}}'
$mount = "$($repo):/work"
docker run --rm `
    --volume $mount `
    --env "LKK_DOCKER_IMAGE_ID=$imageId" `
    --env "LKK_DOCKER_SERVER_VERSION=$dockerVersion" `
    --entrypoint python3 `
    $Image `
    -m phase3.run_phase3 `
    --config /work/phase3/config.json `
    --output "/work/results/phase3/$RunId" `
    --run-id $RunId
if ($LASTEXITCODE -ne 0) {
    throw "Phase 3 failed; preserved output: $output"
}
Write-Output "Phase 3 results: $output"
