<#
.SYNOPSIS
  Registers a Windows Task Scheduler job to run Equity Pulse Stage 1 every weekday at 07:30 AM.
.DESCRIPTION
  Runs grade-ledger, ingest, and screen from the project .venv.
  For full multi-agent runs (Stage 1 -> Stage 2 -> Stage 3), use Antigravity's /automation command
  with schedule '30 7 * * 1-5' (America/New_York) pointing at workflows/daily_scan.py.
#>
param(
    [string]$TaskName = "EquityPulseDailyScan",
    [switch]$DryRun
)

$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExe = Join-Path $RepoDir ".venv\Scripts\python.exe"
$Cmd = "Set-Location '$RepoDir'; & '$PythonExe' -m src.cli grade-ledger; & '$PythonExe' -m src.cli ingest; & '$PythonExe' -m src.cli screen"

if ($DryRun) {
    Write-Host "TaskName: $TaskName"
    Write-Host "Schedule: Weekdays at 07:30 local time"
    Write-Host "Command : pwsh -NoProfile -Command `"$Cmd`""
    exit 0
}

$Action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-NoProfile -WindowStyle Hidden -Command `"$Cmd`""
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 7:30AM
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Description "Equity Pulse Daily Research Scan (research-only)" -Force
Write-Host "Registered Windows Scheduled Task '$TaskName' for weekdays at 07:30."
