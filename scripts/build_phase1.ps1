[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase1:locked"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$lock = Get-Content (Join-Path $repo "solvers.lock.json") -Raw | ConvertFrom-Json

docker build `
    --file (Join-Path $repo "Dockerfile.phase1") `
    --build-arg "CADICAL_COMMIT=$($lock.cadical.commit)" `
    --build-arg "KISSAT_COMMIT=$($lock.kissat.commit)" `
    --tag $Image `
    $repo

if ($LASTEXITCODE -ne 0) {
    throw "Docker solver build failed with exit code $LASTEXITCODE"
}

docker run --rm --entrypoint /bin/sh $Image -c `
    "/opt/solvers/bin/cadical --version && /opt/solvers/bin/kissat --version && cat /opt/solvers/BUILD-METADATA.txt"

if ($LASTEXITCODE -ne 0) {
    throw "Built solver verification failed with exit code $LASTEXITCODE"
}

