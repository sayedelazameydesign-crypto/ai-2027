# AI-2027 — Phase 0 Windows Readiness Check for Ollama (4GB RAM Safe)
# Measures real hardware & daemon state without installing models or claiming VERIFIED prematurely.
param(
    [string]$OllamaBaseUrl = "http://127.0.0.1:11434",
    [switch]$RunLiveInference
)

$ErrorActionPreference = "Stop"

Write-Host "=== AI-2027 Ollama Phase-0 Windows Readiness ===" -ForegroundColor Cyan

$os = Get-CimInstance Win32_OperatingSystem
$totalRamGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
$freeRamGb = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
$sysDrive = (Get-Location).Drive.Name
$disk = Get-PSDrive -Name $sysDrive
$freeDiskGb = [math]::Round($disk.Free / 1GB, 2)

Write-Host ("OS           : {0} ({1})" -f $os.Caption, $os.Version)
Write-Host ("Total RAM    : {0} GB" -f $totalRamGb)
Write-Host ("Free RAM     : {0} GB" -f $freeRamGb)
Write-Host ("Free Disk ({0}): {1} GB" -f $sysDrive, $freeDiskGb)

if ($totalRamGb -le 5.5) {
    Write-Host "[Profile 4GB] Use qwen3.5:0.8b (chat), qwen2.5-coder:0.5b (code), embeddinggemma (RAG), num_ctx=2048." -ForegroundColor Yellow
    Write-Host "[Guard]       Do NOT pull 7B+ or load chat + embedding models concurrently on 4GB RAM." -ForegroundColor Yellow
}

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($null -eq $ollamaCmd) {
    Write-Host "[CLI]         ollama binary not found in PATH." -ForegroundColor Red
    exit 1
}
Write-Host ("[CLI]         {0}" -f (ollama --version))

try {
    $tags = Invoke-RestMethod -Uri ("{0}/api/tags" -f $OllamaBaseUrl.TrimEnd('/')) -TimeoutSec 4
    $modelNames = @($tags.models | ForEach-Object { $_.name })
    Write-Host ("[API /tags]   Reachable ({0} local models): {1}" -f $modelNames.Count, ($modelNames -join ", "))
} catch {
    Write-Host ("[API /tags]   Unreachable at {0}: {1}" -f $OllamaBaseUrl, $_.Exception.Message) -ForegroundColor Red
    exit 1
}

Write-Host "[CLI ps]      Currently loaded models:"
ollama ps

$pyCheck = Join-Path $PSScriptRoot "..\.venv\Scripts\ai2027-py27.exe"
if (Test-Path $pyCheck) {
    $env:OLLAMA_BASE_URL = $OllamaBaseUrl
    if ($RunLiveInference) {
        & $pyCheck ollama-check --live --json
    } else {
        & $pyCheck ollama-check --json
    }
}
