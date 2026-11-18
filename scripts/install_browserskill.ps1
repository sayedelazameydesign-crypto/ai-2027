# install_browserskill.ps1 — fail-closed installer for pinned bsk CLI on Windows x64 (Stage 1 · Mode 1).
#
# Mirrors scripts/install_browserskill.sh:
#   * pins cli-v0.5.0 (Protocol 2.0) by default;
#   * requires a verified 64-hex SHA-256 from version.json before extraction;
#   * never requests elevation and keeps the binary outside Git ($HOME\.local\bin\bsk.exe).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_browserskill.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_browserskill.ps1 -Check
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_browserskill.ps1 -DryRun

param(
  [switch]$Check,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Version = if ($env:BSK_VERSION) { $env:BSK_VERSION.TrimStart('v') } else { '0.5.0' }
$Repo = if ($env:BSK_REPO) { $env:BSK_REPO } else { 'Tencent/BrowserSkill' }
$InstallDir = if ($env:BSK_INSTALL_DIR) { $env:BSK_INSTALL_DIR } else { Join-Path $HOME '.local\bin' }
$Tag = "cli-v$Version"
$BaseUrl = if ($env:BSK_RELEASE_BASE_URL) { $env:BSK_RELEASE_BASE_URL } else { "https://github.com/$Repo/releases/download/$Tag" }
$PlatformKey = 'windows-x64'
$Triple = 'x86_64-pc-windows-msvc'
$Archive = "bsk-v$Version-$Triple.zip"
$ManifestUrl = "$BaseUrl/version.json"
$ArchiveUrl = "$BaseUrl/$Archive"
$TargetBin = Join-Path $InstallDir 'bsk.exe'

if ($DryRun) {
  [ordered]@{
    mode            = 'dry-run'
    repo            = $Repo
    pinned_version  = $Version
    tag             = $Tag
    platform        = $PlatformKey
    triple          = $Triple
    manifest_url    = $ManifestUrl
    archive_url     = $ArchiveUrl
    target_bin      = $TargetBin
    checksum_policy = 'required (fail-closed)'
    uses_elevation  = $false
  } | ConvertTo-Json
  exit 0
}

if ($Check) {
  $bin = if (Test-Path $TargetBin) { $TargetBin } else { (Get-Command bsk -ErrorAction SilentlyContinue).Source }
  if (-not $bin) {
    Write-Output "SKIPPED: bsk.exe is not installed (expected $TargetBin for pinned v$Version)"
    exit 2
  }
  $prev = $env:BSK_AUTO_START
  try {
    $env:BSK_AUTO_START = '0'
    $out = & $bin --version 2>&1 | Out-String
  } finally {
    $env:BSK_AUTO_START = $prev
  }
  if ($out -match [regex]::Escape($Version)) {
    Write-Output "OK: $bin reports $($out.Trim()) (matches pinned $Version)"
    exit 0
  }
  Write-Error "DRIFT: $bin reports $($out.Trim()), expected pinned $Version"
  exit 1
}

$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("bsk-install-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Tmp | Out-Null
try {
  $ManifestPath = Join-Path $Tmp 'version.json'
  $ArchivePath = Join-Path $Tmp $Archive
  Invoke-WebRequest -UseBasicParsing -Uri $ManifestUrl -OutFile $ManifestPath
  $Manifest = Get-Content -Raw -Path $ManifestPath | ConvertFrom-Json
  if (($Manifest.version -replace '^v', '') -ne $Version) {
    throw "Manifest version mismatch: expected $Version, got $($Manifest.version)"
  }
  $ExpectedSha = ($Manifest.assets.$PlatformKey.sha256 | Out-String).Trim().ToLowerInvariant()
  if ($ExpectedSha -notmatch '^[0-9a-f]{64}$') {
    throw "Missing or invalid SHA-256 for $PlatformKey in version.json (fail-closed)"
  }

  Invoke-WebRequest -UseBasicParsing -Uri $ArchiveUrl -OutFile $ArchivePath
  $ActualSha = (Get-FileHash -Algorithm SHA256 -Path $ArchivePath).Hash.ToLowerInvariant()
  if ($ActualSha -ne $ExpectedSha) {
    throw "SHA-256 MISMATCH: expected $ExpectedSha, got $ActualSha — refusing install"
  }

  $ExtractDir = Join-Path $Tmp 'extracted'
  Expand-Archive -Path $ArchivePath -DestinationPath $ExtractDir -Force
  $ExtractedBin = Join-Path $ExtractDir 'bsk.exe'
  if (-not (Test-Path $ExtractedBin)) {
    throw "Archive did not contain bsk.exe at root"
  }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  Copy-Item -Force -Path $ExtractedBin -Destination $TargetBin

  $env:BSK_AUTO_START = '0'
  $VerOut = (& $TargetBin --version 2>&1 | Out-String).Trim()
  Write-Host "==> installed $TargetBin ($VerOut) [SHA-256 verified: $ActualSha]"
  Write-Host "==> next: bsk daemon start ; bsk daemon pair ; python browser_skill_adapter/probe.py"
} finally {
  Remove-Item -Recurse -Force -Path $Tmp -ErrorAction SilentlyContinue
}
