# Restarts the Scene Recall API and both Lab workers so they load current code
# and configuration. The web server, acquisition monitor, torrent/indexer
# helpers and unrelated processes (for example `pipeline.evidence` batch runs)
# are never stopped. Jobs are preserved: workers finish their active job first,
# and API restarts do not affect the saved queue.
#
#   scripts\restart-scene-recall.ps1               restart; refuse if a job is running
#   scripts\restart-scene-recall.ps1 -DryRun       show what would happen; change nothing
#   scripts\restart-scene-recall.ps1 -WaitForJobs  let running jobs finish, then restart
#   scripts\restart-scene-recall.ps1 -ReloadWorkers  start workers with --reload (development)
#   scripts\restart-scene-recall.ps1 -MaintainDatabase  also remove database rollback history while
#                                                   everything is stopped (skipped while pipeline.evidence runs)
#
# Windows PowerShell 5.1 and PowerShell 7.
param(
    [switch]$DryRun,
    [switch]$WaitForJobs,
    [switch]$ReloadWorkers,
    [switch]$MaintainDatabase,
    [ValidateRange(10, 300)][int]$ReadyTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
# Reuse the launcher's checkout-ownership and status helpers. Dot-sourcing does
# not start anything: the launcher only runs when invoked directly.
. (Join-Path $PSScriptRoot 'start-scene-recall.ps1') -ReadyTimeoutSeconds $ReadyTimeoutSeconds

function Get-LabWorkerProcesses {
    # Repository worker processes only; the short-lived --status/--stop probes are excluded.
    @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -match '^python' -and
        $_.CommandLine -match 'pipeline\.lab\.worker' -and
        $_.CommandLine -notmatch '--(status|stop)\b' -and
        (Test-RepositoryPython $_)
    })
}

function Get-ApiProcessIds {
    # The port owner plus any venv redirector parent that is also this checkout's API.
    $ids = New-Object 'System.Collections.Generic.List[int]'
    foreach ($listener in @(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction Stop
        for ($depth = 0; $depth -lt 4 -and $null -ne $current; $depth++) {
            $command = [string]$current.CommandLine
            if (-not ($command -match '(?i)\buvicorn\b' -and $command -match 'pipeline\.api\.main:app')) { break }
            if (-not $ids.Contains([int]$current.ProcessId)) { $ids.Add([int]$current.ProcessId) }
            $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($current.ParentProcessId)" -ErrorAction SilentlyContinue
        }
    }
    return , $ids.ToArray()
}

function Get-EvidenceRuns {
    # Library evidence passes and their finish chain read the index; history cleanup waits for them.
    @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        ($_.Name -match '^python' -and $_.CommandLine -match 'pipeline\.evidence') -or
        ($_.Name -match '^bash' -and $_.CommandLine -match 'finish-library-v\d+\.sh' -and $_.CommandLine -notmatch ' -c ')
    })
}

function Show-Workers($workers) {
    foreach ($worker in $workers) {
        $line = "  {0,-7} {1,-8} {2} queued" -f $worker.role, $worker.state, $worker.queued_count
        if ($worker.current_job) { $line += " | running $($worker.current_job.kind): $($worker.current_job.progress)" }
        Write-Host $line
    }
}

Push-Location $script:Repository
try {
    Write-Host 'Checking current state...'
    $status = Get-LocalStatus
    Show-Workers $status.workers
    $busy = @($status.workers | Where-Object { $_.current_job })
    if ($busy.Count -and -not $WaitForJobs -and -not $DryRun) {
        throw ("A Lab worker is running a job. Nothing was stopped. Wait for it, or rerun with -WaitForJobs " +
               "to let it finish and then restart.")
    }

    # Refuse before stopping anything if the API port belongs to something unrecognized.
    $apiRunning = Test-OwnedListener 8000 'api'
    $apiIds = if ($apiRunning) { Get-ApiProcessIds } else { @() }
    $evidenceRuns = if ($MaintainDatabase) { Get-EvidenceRuns } else { @() }

    if ($DryRun) {
        Write-Host ''
        Write-Host 'Dry run - nothing was changed. A real run would:'
        Write-Host ("  1. Ask the Lab workers to stop after their current job" + $(if ($busy.Count) { " (waiting for $($busy.Count) running job(s))" } else { ' (none running)' }))
        Write-Host ("  2. Stop the API" + $(if ($apiIds.Count) { " (PID $($apiIds -join ', '))" } else { ' (not running)' }))
        if ($MaintainDatabase) {
            Write-Host ("  -. Remove database rollback history" + $(if ($evidenceRuns.Count) { " - SKIPPED: pipeline.evidence work still running (PID $(($evidenceRuns | ForEach-Object ProcessId) -join ', '))" } else { '' }))
        }
        Write-Host ("  3. Start the API and both workers via scripts\start-scene-recall.ps1" + $(if ($ReloadWorkers) { ' -ReloadWorkers' } else { '' }))
        Write-Host '  Left alone: web server, acquisition monitor, helpers, pipeline.evidence runs.'
        return
    }

    # 1. Drain the workers (each finishes its active job; queued jobs are kept).
    Write-Host ''
    Write-Host 'Stopping Lab workers after their current job...'
    $stopOutput = & $script:Python -m pipeline.lab.worker --stop 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Could not request a worker stop: $($stopOutput -join ' ')" }
    $deadline = if ($WaitForJobs -and $busy.Count) { [DateTime]::MaxValue } else { [DateTime]::UtcNow.AddSeconds(90) }
    $lastReport = [DateTime]::MinValue
    while ($true) {
        $status = Get-LocalStatus
        if (-not @($status.workers | Where-Object { $_.online }).Count) { break }
        if ([DateTime]::UtcNow -gt $deadline) {
            throw 'The workers did not stop in time. A stop is still requested, so they will exit after their current job; rerun this script then.'
        }
        if (([DateTime]::UtcNow - $lastReport).TotalSeconds -ge 30) {
            Show-Workers @($status.workers | Where-Object { $_.online })
            $lastReport = [DateTime]::UtcNow
        }
        Start-Sleep -Seconds 2
    }
    Write-Host 'Workers stopped.'

    # Any repository worker process still alive could take a role back with old code.
    $strayDeadline = [DateTime]::UtcNow.AddSeconds(20)
    do { $stray = Get-LabWorkerProcesses; if ($stray.Count) { Start-Sleep -Seconds 1 } }
    while ($stray.Count -and [DateTime]::UtcNow -lt $strayDeadline)
    if ($stray.Count) {
        Write-Warning ("Other Lab worker process(es) are still running (PID $(($stray | ForEach-Object ProcessId) -join ', ')); " +
                       "they were not stopped and may take over a role. Close them yourself if unwanted.")
    }

    # 2. Restart the API. Saved jobs are unaffected.
    if ($apiIds.Count) {
        Write-Host "Stopping the API (PID $($apiIds -join ', '))..."
        Stop-Process -Id $apiIds -Force -ErrorAction SilentlyContinue
        $portDeadline = [DateTime]::UtcNow.AddSeconds(30)
        while (@(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue).Count -and [DateTime]::UtcNow -lt $portDeadline) {
            Start-Sleep -Milliseconds 500
        }
        if (@(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue).Count) {
            throw 'Port 8000 is still in use after stopping the API. Check it manually before starting again.'
        }
    } else { Write-Host 'The API was not running.' }

    # Optional: nothing reads the index now, so its rollback history can go (current rows and indexes stay).
    if ($MaintainDatabase) {
        $evidenceRuns = Get-EvidenceRuns
        if ($evidenceRuns.Count) {
            Write-Warning ("Database maintenance skipped: pipeline.evidence work is still running (PID " +
                           "$(($evidenceRuns | ForEach-Object ProcessId) -join ', ')). Rerun with -MaintainDatabase after it finishes.")
        } else {
            Write-Host 'Removing database rollback history (current rows and indexes are kept)...'
            & uv run --group maintenance python -m pipeline.index.maintenance --retain-versions 1 --apply | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Warning 'Database maintenance did not complete; the services start anyway.' }
            else { Write-Host 'Database history removed.' }
        }
    }

    # 3. Start everything through the normal launcher (reuses web and downloads).
    Write-Host ''
    & (Join-Path $PSScriptRoot 'start-scene-recall.ps1') -ReloadWorkers:$ReloadWorkers -ReadyTimeoutSeconds $ReadyTimeoutSeconds
    if ($LASTEXITCODE -ne 0) { throw 'The launcher failed; see its message above. The workers and API may need starting manually.' }
    Write-Host ''
    Write-Host 'Restart complete:'
    Show-Workers (Get-LocalStatus).workers
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
} finally { Pop-Location }
