[CmdletBinding()]
param(
    [string]$Image = "lkk-hsat-phase1:locked",
    [string]$Config = "benchmark/config.phase1.json",
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$results = Join-Path $repo "results\phase1\$RunId"
New-Item -ItemType Directory -Force -Path $results | Out-Null

$computer = Get-CimInstance Win32_ComputerSystem
$processor = Get-CimInstance Win32_Processor | Select-Object -First 1
$os = Get-CimInstance Win32_OperatingSystem
$hostMetadata = [ordered]@{
    captured_utc = (Get-Date).ToUniversalTime().ToString("o")
    computer_name = $env:COMPUTERNAME
    manufacturer = $computer.Manufacturer
    model = $computer.Model
    cpu = $processor.Name
    physical_cores = $processor.NumberOfCores
    logical_processors = $processor.NumberOfLogicalProcessors
    memory_bytes = [int64]$computer.TotalPhysicalMemory
    os = $os.Caption
    os_version = $os.Version
    docker_version = (docker version --format '{{.Server.Version}}')
    docker_image = $Image
    docker_image_id = (docker image inspect $Image --format '{{.Id}}')
}
$hostMetadata | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 (Join-Path $results "host-metadata.json")

$mount = "$($repo):/work"
docker run --rm `
    --volume $mount `
    $Image `
    --config $Config `
    --output "results/phase1/$RunId"

if ($LASTEXITCODE -ne 0) {
    throw "Phase 1 benchmark failed with exit code $LASTEXITCODE; partial data remains in $results"
}

Write-Output "Phase 1 results: $results"

