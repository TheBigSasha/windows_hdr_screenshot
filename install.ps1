param(
    [string] $InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\HDRShot")
)

$ErrorActionPreference = "Stop"
$repo = "TheBigSasha/windows_hdr_screenshot"
$machine = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }

if ($machine -notmatch "(?i)ARM64") {
    throw "This installer is for native Windows ARM64. Use the x64 release on x64 Windows."
}

$release = Invoke-RestMethod -Headers @{ "User-Agent" = "HDRShot-installer" } `
    -Uri "https://api.github.com/repos/$repo/releases/latest"
$asset = $release.assets |
    Where-Object { $_.name -like "HDRShot-*-win-arm64.zip" } |
    Select-Object -First 1
if (-not $asset) {
    throw "No ARM64 release asset was found for $repo."
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("HDRShot-" + [guid]::NewGuid())
$zip = Join-Path $tempRoot $asset.name
$checksum = Join-Path $tempRoot ($asset.name + ".sha256")
$expanded = Join-Path $tempRoot "expanded"
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip
    $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
    $expected = $asset.digest
    if (-not $expected) {
        $checksumAsset = $release.assets |
            Where-Object { $_.name -eq ($asset.name + ".sha256") } |
            Select-Object -First 1
        if (-not $checksumAsset) {
            throw "The release has no SHA-256 checksum for $($asset.name)."
        }
        Invoke-WebRequest -Uri $checksumAsset.browser_download_url -OutFile $checksum
        $expected = (Get-Content -LiteralPath $checksum | Select-Object -First 1).Split()[0]
    }
    $expected = $expected.Replace("sha256:", "").ToLowerInvariant()
    if ($actual.ToLowerInvariant() -ne $expected) {
        throw "The downloaded release failed its SHA-256 check."
    }

    Expand-Archive -LiteralPath $zip -DestinationPath $expanded
    $bundle = Join-Path $expanded "HDRShot"
    if (-not (Test-Path -LiteralPath (Join-Path $bundle "HDRShot.exe"))) {
        throw "The release archive did not contain HDRShot\\HDRShot.exe."
    }
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Get-ChildItem -LiteralPath $bundle -Force | Copy-Item -Destination $InstallDir -Recurse -Force
}
finally {
    if (Test-Path $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

$exe = Join-Path $InstallDir "HDRShot.exe"
Write-Output "HDR Shot $($release.tag_name) installed to $InstallDir"
Write-Output "Launching $exe"
Start-Process -FilePath $exe
