param(
    [string]$CloudIp = "127.0.0.1",
    [ValidateSet("fusion", "all-edge", "all-cloud")]
    [string]$Mode = "fusion",
    [switch]$SkipArduino
)

$Root = $PSScriptRoot
$VenvActivate = Join-Path $Root ".venv\Scripts\Activate.ps1"
$CloudUrl = "http://${CloudIp}:8000"

if (-not (Test-Path $VenvActivate)) {
    Write-Error "Virtual environment not found at $VenvActivate. Create .venv first."
    exit 1
}

# Archive previous metrics.csv so the live dashboard starts fresh for the demo
$MetricsFile = Join-Path $Root "metrics.csv"
if (Test-Path $MetricsFile) {
    $Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Move-Item $MetricsFile (Join-Path $Root "metrics_$Stamp.csv")
    Write-Host "Archived previous metrics.csv -> metrics_$Stamp.csv" -ForegroundColor DarkGray
}

$CmdScheduler = "& '$VenvActivate'; uvicorn scheduler.main:app --port 8001"
$CmdProxy     = "& '$VenvActivate'; `$env:CLOUD_WORKER_URL='$CloudUrl'; `$env:SCHEDULER_MODE='$Mode'; python control-plane\proxy.py"
$CmdArduino   = "& '$VenvActivate'; python edge-hardware\serial_bridge.py"
$CmdDashboard = "& '$VenvActivate'; streamlit run dashboard\app.py --server.headless true"

Write-Host "Launching Edge Fusion [Mode: $Mode | Cloud: $CloudUrl]..." -ForegroundColor Cyan

if (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    # Opens all services as tabs inside ONE Windows Terminal window
    $WtArgs = @(
        "-w", "0", "new-tab", "--title", "1-Scheduler", "-d", "$Root", "powershell", "-NoExit", "-Command", $CmdScheduler,
        ";", "new-tab", "--title", "2-ControlPlane", "-d", "$Root", "powershell", "-NoExit", "-Command", $CmdProxy,
        ";", "new-tab", "--title", "4-Dashboard", "-d", "$Root", "powershell", "-NoExit", "-Command", $CmdDashboard
    )
    if (-not $SkipArduino) {
        $WtArgs += @(";", "new-tab", "--title", "3-ArduinoBridge", "-d", "$Root", "powershell", "-NoExit", "-Command", $CmdArduino)
    }
    Start-Process wt.exe -ArgumentList $WtArgs
} else {
    # Fallback for systems without Windows Terminal
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $CmdScheduler"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $CmdProxy"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $CmdDashboard"
    if (-not $SkipArduino) {
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; $CmdArduino"
    }
}

Start-Sleep -Seconds 3
Start-Process "http://localhost:8501"

Write-Host "`nAll services started! Dashboard opened at http://localhost:8501" -ForegroundColor Green
Write-Host "Run this command here when ready for Stage 2 (Traffic Burst):" -ForegroundColor Yellow
Write-Host "  .venv\Scripts\Activate.ps1; python workloads\load_gen.py --rate 50 --duration 45`n" -ForegroundColor White