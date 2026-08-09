param(
    [Parameter(Mandatory = $true)][string] $Archive,
    [Parameter(Mandatory = $true)][string] $ExpectedVersion,
    [Parameter(Mandatory = $true)][ValidateSet("x64", "arm64")][string] $ExpectedArchitecture,
    [Parameter(Mandatory = $true)][string] $OutputDirectory
)

$ErrorActionPreference = "Stop"

function Get-PeMachine([string] $Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 64 -or [Text.Encoding]::ASCII.GetString($bytes, 0, 2) -ne "MZ") {
        throw "$Path is not a PE file"
    }
    $offset = [BitConverter]::ToInt32($bytes, 0x3c)
    if ($offset -lt 0 -or $offset + 6 -gt $bytes.Length -or
        [Text.Encoding]::ASCII.GetString($bytes, $offset, 4) -ne "PE`0`0") {
        throw "$Path has an invalid PE header"
    }
    return ("{0:x4}" -f [BitConverter]::ToUInt16($bytes, $offset + 4))
}

$actualHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
$temp = Join-Path ([IO.Path]::GetTempPath()) ("HDRShot-verify-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp -Force | Out-Null
try {
    Expand-Archive -LiteralPath $Archive -DestinationPath $temp
    $bundle = Join-Path $temp "HDRShot"
    $gui = Join-Path $bundle "HDRShot.exe"
    $cli = Join-Path $bundle "hdrshot-cli.exe"
    foreach ($required in @($gui, $cli)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "missing $required" }
        $machine = Get-PeMachine $required
        $expectedMachine = if ($ExpectedArchitecture -eq "arm64") { "aa64" } else { "8664" }
        if ($machine -ne $expectedMachine) { throw "$required is PE $machine, expected $expectedMachine" }
    }
    foreach ($excluded in @("OpenEXR", "imagecodecs", "pillow_avif", "pillow_heif")) {
        if (Get-ChildItem -LiteralPath $bundle -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "$excluded*" } | Select-Object -First 1) {
            throw "excluded provider $excluded is present in the frozen bundle"
        }
    }
    $manifestPath = Join-Path $bundle "bundle-capabilities.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw "missing bundle capability manifest" }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $version = (& $cli --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch [regex]::Escape($ExpectedVersion)) {
        throw "CLI version check failed: $version"
    }
    $caps = (& $cli capabilities --json | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw "capabilities command failed" }
    $expected = @($caps.expected_profiles | Sort-Object)
    $available = @($caps.available_profiles | Sort-Object)
    $manifestExpected = @($manifest.expected_profiles | Sort-Object)
    if ((ConvertTo-Json $manifestExpected -Compress) -ne (ConvertTo-Json $expected -Compress)) {
        throw "executable and archive capability manifests disagree"
    }
    if ((ConvertTo-Json $expected -Compress) -ne (ConvertTo-Json $available -Compress)) {
        throw "capability contract drift: expected=$expected available=$available"
    }
    $smoke = Join-Path $temp "smoke"
    & $cli selftest --out $smoke
    if ($LASTEXITCODE -ne 0) { throw "extracted CLI selftest failed" }
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $caps | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 (Join-Path $OutputDirectory "capabilities.json")
    Write-Output "verified $Archive ($actualHash)"
}
finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
