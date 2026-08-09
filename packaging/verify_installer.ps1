param(
    [Parameter(Mandatory = $true)][string] $Installer,
    [Parameter(Mandatory = $true)][string] $ExpectedVersion,
    [Parameter(Mandatory = $true)][ValidateSet("x64", "arm64")][string] $ExpectedArchitecture,
    [Parameter(Mandatory = $true)][string] $OutputDirectory
)

$ErrorActionPreference = "Stop"

function Get-PeMachine([string] $Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 64 -or $bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) {
        throw "$Path is not a PE executable"
    }
    $offset = [BitConverter]::ToInt32($bytes, 0x3c)
    if ($offset -lt 64 -or $offset + 6 -gt $bytes.Length -or
        [Text.Encoding]::ASCII.GetString($bytes, $offset, 4) -ne ('PE' + [char]0 + [char]0)) {
        throw "$Path has an invalid PE header"
    }
    return ("{0:x4}" -f [BitConverter]::ToUInt16($bytes, $offset + 4))
}

$expectedMachine = if ($ExpectedArchitecture -eq "arm64") { "aa64" } else { "8664" }
$suffix = if ($ExpectedArchitecture -eq "arm64") { "win-arm64" } else { "win64" }
$installerPath = [IO.Path]::GetFullPath($Installer)
$expectedName = "HDRShot-$ExpectedVersion-$suffix-setup.exe"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) { throw "installer does not exist" }
if ([IO.Path]::GetFileName($installerPath) -cne $expectedName) {
    throw "installer name is not exactly '$expectedName'"
}
if ((Get-PeMachine $installerPath) -cne $expectedMachine) {
    throw "installer has the wrong PE architecture"
}

$sidecar = "$installerPath.sha256"
if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) { throw "missing installer SHA-256 sidecar" }
$lines = @(Get-Content -LiteralPath $sidecar | Where-Object { $_.Trim() -ne "" })
if ($lines.Count -ne 1) { throw "installer sidecar must contain exactly one record" }
$match = [regex]::Match($lines[0].Trim(), '^(?<hash>[0-9A-Fa-f]{64})\s+\*?(?<name>\S+)$')
if (-not $match.Success -or $match.Groups["name"].Value -cne [IO.Path]::GetFileName($installerPath)) {
    throw "installer sidecar has an invalid record"
}
$actual = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($match.Groups["hash"].Value.ToLowerInvariant() -cne $actual) {
    throw "installer SHA-256 does not match its sidecar"
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$installDir = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) "installed"
& $installerPath --install-dir $installDir --no-launch
if ($LASTEXITCODE -ne 0) { throw "installer exited with code $LASTEXITCODE" }

$gui = Join-Path $installDir "HDRShot.exe"
$cli = Join-Path $installDir "hdrshot-cli.exe"
foreach ($required in @($gui, $cli)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "installed file is missing: $required" }
    if ((Get-PeMachine $required) -cne $expectedMachine) {
        throw "$([IO.Path]::GetFileName($required)) has the wrong PE architecture"
    }
}
$versionOutput = (& $cli --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $versionOutput -cne "hdrshot $ExpectedVersion") {
    throw "installed CLI version mismatch: $versionOutput"
}
$capabilityOutput = (& $cli capabilities --json 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "installed capabilities command failed: $capabilityOutput" }
$caps = $capabilityOutput | ConvertFrom-Json
if ([string]$caps.architecture -cne $ExpectedArchitecture -or @($caps.available_profiles).Count -eq 0) {
    throw "installed capability contract is invalid"
}
$smoke = Join-Path $OutputDirectory "selftest"
& $cli selftest --out $smoke
if ($LASTEXITCODE -ne 0) { throw "installed CLI selftest failed" }

$oldQtPlatform = $env:QT_QPA_PLATFORM
$guiProcess = $null
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    $guiProcess = Start-Process -FilePath $gui -WorkingDirectory $installDir -PassThru
    if ($guiProcess.WaitForExit(10000)) {
        throw "installed Qt GUI exited during smoke test with code $($guiProcess.ExitCode)"
    }
    Write-Output "installed Qt GUI remained alive in offscreen smoke test"
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

[pscustomobject]@{
    installer = [IO.Path]::GetFileName($installerPath)
    sha256 = $actual
    version = $ExpectedVersion
    architecture = $ExpectedArchitecture
    installed_gui = $gui
    installed_cli = $cli
} | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 (Join-Path $OutputDirectory "installer-verification.json")
Write-Output "verified installer $installerPath ($actual)"
