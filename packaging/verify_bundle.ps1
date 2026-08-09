param(
    [Parameter(Mandatory = $true)][string] $Archive,
    [Parameter(Mandatory = $true)][string] $ExpectedVersion,
    [Parameter(Mandatory = $true)][ValidateSet("x64", "arm64")][string] $ExpectedArchitecture,
    [Parameter(Mandatory = $true)][string] $OutputDirectory
)

$ErrorActionPreference = "Stop"

function Get-PeMachine([string] $Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 2) { return $null }
    if ($bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) { return $null }
    if ($bytes.Length -lt 64) { throw "$Path has a truncated DOS header" }

    $offset = [BitConverter]::ToInt32($bytes, 0x3c)
    if ($offset -lt 64 -or $offset + 6 -gt $bytes.Length) {
        throw "$Path has an invalid PE header offset"
    }
    if ([Text.Encoding]::ASCII.GetString($bytes, $offset, 4) -ne "PE`0`0") {
        throw "$Path has an invalid PE signature"
    }
    return ("{0:x4}" -f [BitConverter]::ToUInt16($bytes, $offset + 4))
}

function Get-ProfileSet($Value, [string] $FieldName) {
    if ($null -eq $Value -or $Value -is [string]) {
        throw "$FieldName must be a non-empty JSON array"
    }
    $items = @($Value)
    if ($items.Count -eq 0) { throw "$FieldName must not be empty" }
    $known = @("uhdr-jpeg", "uhdr-avif", "uhdr-heic", "pq-avif", "pq-heic", "exr", "png", "jpeg", "avif-sdr")
    $names = foreach ($item in $items) {
        if ($item -isnot [string] -or [string]::IsNullOrWhiteSpace($item)) {
            throw "$FieldName contains a non-string or empty profile"
        }
        $name = ([string]$item).Trim()
        if ($known -notcontains $name) { throw "$FieldName contains unknown profile '$name'" }
        $name
    }
    $unique = @($names | Sort-Object -Unique)
    if ($unique.Count -ne $items.Count) { throw "$FieldName contains duplicate profiles" }
    return $unique
}

function Assert-ProfileSetEqual($Left, $Right, [string] $Description) {
    $leftText = (@($Left | Sort-Object) -join "`n")
    $rightText = (@($Right | Sort-Object) -join "`n")
    if ($leftText -cne $rightText) { throw "${Description}: [$leftText] != [$rightText]" }
}

$expectedMachine = if ($ExpectedArchitecture -eq "arm64") { "aa64" } else { "8664" }
$expectedSuffix = if ($ExpectedArchitecture -eq "arm64") { "win-arm64" } else { "win64" }
$archivePath = [IO.Path]::GetFullPath($Archive)
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    throw "archive does not exist: $archivePath"
}
$expectedArchiveName = "HDRShot-$ExpectedVersion-$expectedSuffix.zip"
if ([IO.Path]::GetFileName($archivePath) -cne $expectedArchiveName) {
    throw "archive name '$([IO.Path]::GetFileName($archivePath))' is not exactly '$expectedArchiveName'"
}

$sidecarPath = "$archivePath.sha256"
if (-not (Test-Path -LiteralPath $sidecarPath -PathType Leaf)) {
    throw "missing SHA-256 sidecar: $sidecarPath"
}
$sidecarLines = @(Get-Content -LiteralPath $sidecarPath | Where-Object { $_.Trim() -ne "" })
if ($sidecarLines.Count -ne 1) { throw "SHA-256 sidecar must contain exactly one non-empty line" }
$sidecarMatch = [regex]::Match($sidecarLines[0].Trim(), '^(?<hash>[0-9A-Fa-f]{64})\s+\*?(?<name>\S+)$')
if (-not $sidecarMatch.Success -or $sidecarMatch.Groups["name"].Value -cne [IO.Path]::GetFileName($archivePath)) {
    throw "SHA-256 sidecar has an invalid filename or digest record"
}
$actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sidecarMatch.Groups["hash"].Value.ToLowerInvariant() -cne $actualHash) {
    throw "archive SHA-256 does not match its sidecar"
}

$temp = Join-Path ([IO.Path]::GetTempPath()) ("HDRShot-verify-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp -Force | Out-Null
try {
    Expand-Archive -LiteralPath $archivePath -DestinationPath $temp
    $bundle = Join-Path $temp "HDRShot"
    if (-not (Test-Path -LiteralPath $bundle -PathType Container)) {
        throw "archive must contain a top-level HDRShot directory"
    }
    $gui = Join-Path $bundle "HDRShot.exe"
    $cli = Join-Path $bundle "hdrshot-cli.exe"

    $peFiles = @()
    foreach ($file in (Get-ChildItem -LiteralPath $bundle -Recurse -File -Force)) {
        $machine = Get-PeMachine $file.FullName
        if ($null -ne $machine) {
            $peFiles += [pscustomobject]@{ Path = $file.FullName; Machine = $machine }
        }
    }
    if ($peFiles.Count -eq 0) { throw "bundle contains no PE-bearing files" }
    foreach ($pe in $peFiles) {
        if ($pe.Machine -cne $expectedMachine) {
            throw "$($pe.Path) is PE $($pe.Machine), expected $expectedMachine for $ExpectedArchitecture"
        }
    }
    foreach ($required in @($gui, $cli)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "missing $required" }
        if (-not ($peFiles | Where-Object { $_.Path -eq $required })) {
            throw "$required is not a recognized PE file"
        }
    }

    foreach ($excluded in @("OpenEXR", "imagecodecs", "pillow_avif", "pillow_heif")) {
        if (Get-ChildItem -LiteralPath $bundle -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "$excluded*" } | Select-Object -First 1) {
            throw "excluded provider $excluded is present in the frozen bundle"
        }
    }

    $manifestPath = Join-Path $bundle "bundle-capabilities.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "missing bundle capability manifest"
    }
    try {
        $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    } catch {
        throw "bundle capability manifest is invalid JSON: $($_.Exception.Message)"
    }
    if ($null -eq $manifest -or $manifest -is [string]) {
        throw "bundle capability manifest must be a JSON object"
    }
    if ($manifest.PSObject.Properties.Name -contains "manifest_error") {
        throw "bundle capability manifest reports an error: $($manifest.manifest_error)"
    }
    if ($manifest.schema_version -ne 1) { throw "unsupported capability manifest schema" }
    if ([string]$manifest.architecture -cne $ExpectedArchitecture) {
        throw "manifest architecture '$($manifest.architecture)' != '$ExpectedArchitecture'"
    }
    $manifestExpected = @(Get-ProfileSet $manifest.expected_profiles "manifest.expected_profiles")
    $baseline = @("uhdr-jpeg", "png", "jpeg")
    foreach ($profile in $baseline) {
        if ($manifestExpected -notcontains $profile) {
            throw "manifest baseline is incomplete; missing required profile '$profile'"
        }
    }

    $versionOutput = (& $cli --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $versionOutput -cne "hdrshot $ExpectedVersion") {
        throw "CLI version check failed: expected exactly 'hdrshot $ExpectedVersion', got '$versionOutput'"
    }
    $capabilityOutput = (& $cli capabilities --json 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "capabilities command failed: $capabilityOutput" }
    try {
        $caps = $capabilityOutput | ConvertFrom-Json
    } catch {
        throw "capabilities command returned invalid JSON: $($_.Exception.Message)"
    }
    if ($null -eq $caps -or $caps.schema_version -ne 2) {
        throw "capabilities response has an invalid schema"
    }
    if ([string]$caps.architecture -cne $ExpectedArchitecture) {
        throw "CLI architecture '$($caps.architecture)' != '$ExpectedArchitecture'"
    }
    $capExpected = @(Get-ProfileSet $caps.expected_profiles "capabilities.expected_profiles")
    $available = @(Get-ProfileSet $caps.available_profiles "capabilities.available_profiles")
    Assert-ProfileSetEqual $manifestExpected $capExpected "manifest and executable capability baselines disagree"
    Assert-ProfileSetEqual $capExpected $available "capability contract drift"

    $smoke = Join-Path $temp "smoke"
    & $cli selftest --out $smoke
    if ($LASTEXITCODE -ne 0) { throw "extracted CLI selftest failed" }

    $oldQtPlatform = $env:QT_QPA_PLATFORM
    $guiProcess = $null
    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        $guiProcess = Start-Process -FilePath $gui -WorkingDirectory $bundle -PassThru
        if ($guiProcess.WaitForExit(10000)) {
            throw "packaged Qt GUI exited during smoke test with code $($guiProcess.ExitCode)"
        }
        Write-Output "packaged Qt GUI remained alive in offscreen smoke test"
    } finally {
        if ($null -ne $guiProcess -and -not $guiProcess.HasExited) {
            Stop-Process -Id $guiProcess.Id -Force -ErrorAction SilentlyContinue
            $guiProcess.WaitForExit(5000)
        }
        if ($null -eq $oldQtPlatform) {
            Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        } else {
            $env:QT_QPA_PLATFORM = $oldQtPlatform
        }
    }

    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $caps | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 (Join-Path $OutputDirectory "capabilities.json")
    [pscustomobject]@{
        archive = [IO.Path]::GetFileName($archivePath)
        sha256 = $actualHash
        version = $ExpectedVersion
        architecture = $ExpectedArchitecture
        pe_files = $peFiles.Count
        expected_profiles = $manifestExpected
    } | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 (Join-Path $OutputDirectory "verification.json")
    Write-Output "verified $archivePath ($actualHash); inspected $($peFiles.Count) PE files"
}
finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
