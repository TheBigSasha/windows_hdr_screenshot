param(
    [string] $InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\HDRShot")
)

$ErrorActionPreference = "Stop"
$repo = "TheBigSasha/windows_hdr_screenshot"
$machine = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$assetSuffix = if ($machine -match "(?i)^ARM64$") { "win-arm64" } elseif ($machine -match "(?i)^(AMD64|x64)$") { "win64" } else { $null }
if (-not $assetSuffix) {
    throw "Unsupported Windows architecture '$machine'. HDR Shot releases support x64 and ARM64."
}

function Get-PeMachine([string] $Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 64 -or [Text.Encoding]::ASCII.GetString($bytes, 0, 2) -ne "MZ") {
        throw "$Path is not a PE executable"
    }
    $offset = [BitConverter]::ToInt32($bytes, 0x3c)
    if ($offset -lt 0 -or $offset + 6 -gt $bytes.Length -or
        [Text.Encoding]::ASCII.GetString($bytes, $offset, 4) -ne "PE`0`0") {
        throw "$Path has an invalid PE header"
    }
    return ("{0:x4}" -f [BitConverter]::ToUInt16($bytes, $offset + 4))
}

try {
    try {
        $release = Invoke-RestMethod -Headers @{ "User-Agent" = "HDRShot-installer" } `
            -Uri "https://api.github.com/repos/$repo/releases/latest"
    }
    catch {
        throw "No stable HDR Shot release is published yet (or GitHub could not be reached). " +
              "Use the source-development install until a verified release exists. Details: $($_.Exception.Message)"
    }
    $tag = [string]$release.tag_name
    if ($tag -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') { throw "Latest release has invalid tag '$tag'." }
    $version = $tag.Substring(1)
    $assetName = "HDRShot-$version-$assetSuffix.zip"
    $expectedArchitecture = if ($assetSuffix -eq "win-arm64") { "arm64" } else { "x64" }
    $asset = @($release.assets | Where-Object { $_.name -eq $assetName }) | Select-Object -First 1
    if (-not $asset) { throw "Release $tag has no exact asset '$assetName'." }

    $checksumAsset = @($release.assets | Where-Object { $_.name -eq "$assetName.sha256" }) | Select-Object -First 1
    $installPath = [IO.Path]::GetFullPath($InstallDir)
    $parent = Split-Path -Parent $installPath
    $leaf = Split-Path -Leaf $installPath
    if (-not $parent -or -not $leaf) { throw "InstallDir must be a concrete directory path." }

    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("HDRShot-" + [guid]::NewGuid())
    $zip = Join-Path $tempRoot $assetName
    $checksum = Join-Path $tempRoot "$assetName.sha256"
    $expanded = Join-Path $tempRoot "expanded"
    $stage = Join-Path $parent (".$leaf.new." + [guid]::NewGuid())
    $rollback = Join-Path $parent (".$leaf.rollback.$version." + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $parent -Force | Out-Null

    try {
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip
        $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        $expected = [string]$asset.digest
        if (-not $expected -and $checksumAsset) {
            Invoke-WebRequest -Uri $checksumAsset.browser_download_url -OutFile $checksum
            $expected = (Get-Content -LiteralPath $checksum | Select-Object -First 1).Split()[0]
        }
        if (-not $expected) { throw "Release $tag has no SHA-256 digest for $assetName." }
        $expected = $expected.Replace("sha256:", "").Trim().ToLowerInvariant()
        if ($actual -ne $expected) { throw "Downloaded $assetName failed SHA-256 verification." }

        Expand-Archive -LiteralPath $zip -DestinationPath $expanded
        $bundle = Join-Path $expanded "HDRShot"
        $gui = Join-Path $bundle "HDRShot.exe"
        $cli = Join-Path $bundle "hdrshot-cli.exe"
        foreach ($exe in @($gui, $cli)) {
            if (-not (Test-Path -LiteralPath $exe)) { throw "Archive is missing $([IO.Path]::GetFileName($exe))." }
            $expectedMachine = if ($assetSuffix -eq "win-arm64") { "aa64" } else { "8664" }
            if ((Get-PeMachine $exe) -ne $expectedMachine) {
                throw "$([IO.Path]::GetFileName($exe)) has the wrong PE architecture."
            }
        }
        $manifestPath = Join-Path $bundle "bundle-capabilities.json"
        if (-not (Test-Path -LiteralPath $manifestPath)) {
            throw "Archive is missing its capability contract."
        }
        $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
        if ($manifest.schema_version -ne 1 -or
            [string]$manifest.architecture -cne $expectedArchitecture -or
            @($manifest.expected_profiles).Count -eq 0) {
            throw "Archive has an invalid capability contract."
        }

        # Copy into a sibling directory. No existing install is touched until the
        # complete staged tree passes all executable and capability checks.
        Copy-Item -LiteralPath $bundle -Destination $stage -Recurse -Force
        $stageCli = Join-Path $stage "hdrshot-cli.exe"
        $reportedVersion = (& $stageCli --version | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $reportedVersion -cne "hdrshot $version") {
            throw "Staged CLI version mismatch: expected 'hdrshot $version', got '$reportedVersion'"
        }
        $caps = (& $stageCli capabilities --json | ConvertFrom-Json)
        if ($LASTEXITCODE -ne 0 -or -not $caps.available_profiles) {
            throw "Staged capability contract could not be read."
        }
        $manifestProfiles = @($manifest.expected_profiles | Sort-Object) -join ","
        $reportedProfiles = @($caps.expected_profiles | Sort-Object) -join ","
        $availableProfiles = @($caps.available_profiles | Sort-Object) -join ","
        if ($manifestProfiles -cne $reportedProfiles -or $reportedProfiles -cne $availableProfiles) {
            throw "Staged capability contract disagrees with the executable."
        }
        $smoke = Join-Path $tempRoot "selftest"
        & $stageCli selftest --out $smoke
        if ($LASTEXITCODE -ne 0) { throw "Staged bundle selftest failed." }

        $oldMoved = $false
        try {
            if (Test-Path -LiteralPath $installPath) {
                Move-Item -LiteralPath $installPath -Destination $rollback
                $oldMoved = $true
            }
            Move-Item -LiteralPath $stage -Destination $installPath
            $stage = $null
        }
        catch {
            if ($oldMoved -and -not (Test-Path -LiteralPath $installPath)) {
                Move-Item -LiteralPath $rollback -Destination $installPath
            }
            throw "Install swap failed; previous version was restored when possible. $($_.Exception.Message)"
        }

        $exe = Join-Path $installPath "HDRShot.exe"
        Write-Output "HDR Shot $tag installed to $installPath"
        if ($oldMoved) { Write-Output "Previous version retained at $rollback for rollback." }
        Start-Process -FilePath $exe
    }
    finally {
        if ($stage -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force }
        if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
    }
}
catch {
    throw $_
}
