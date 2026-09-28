# Frees disk space held by regenerable scratch. Never touches source films,
# playback previews, keyframes, current evidence or the index's current data.
#
#   scripts\cleanup-storage.ps1          report what would be removed (default)
#   scripts\cleanup-storage.ps1 -Apply   remove it
#
# Removes:
#   1. Repository .tmp scratch: pytest temp folders (pt*, pytest-*) and anything
#      else untouched for 12 hours (old test runs, verification copies, pilot
#      outputs). Kept: service logs (.tmp\services), frozen experiment data
#      (framing-*, match-*) and everything touched in the last 12 hours, which
#      covers running jobs and their logs.
#   2. Evaluation renders under <assets>\lab\renders (eval-*, compare-*,
#      harness-*), keeping the latest editor comparison pairs (eval-*-v1 and
#      eval-*-v2-c) until -IncludeComparisons is passed.
#   3. Understanding proxy temp folders untouched for 6 hours (left by
#      interrupted runs).
#   4. Superseded evidence profiles (python -m pipeline.evidence prune) and
#      orphaned Lab job files (python -m pipeline.lab.cleanup).
#
# Database rollback history (about 10 GB) needs the API and workers stopped:
# run scripts\restart-scene-recall.ps1 -WaitForJobs -MaintainDatabase once no
# pipeline.evidence run is active.
#
# Windows PowerShell 5.1 and PowerShell 7.
param(
    [switch]$Apply,
    [switch]$IncludeComparisons
)

$ErrorActionPreference = 'Stop'
# Reuse the launcher's repository/python helpers; dot-sourcing starts nothing.
. (Join-Path $PSScriptRoot 'start-scene-recall.ps1')

function Get-SizeBytes([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return (Get-Item -LiteralPath $Path).Length }
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    if ($sum) { return [int64]$sum } else { return [int64]0 }
}

function Get-LastTouched([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    $latest = $item.LastWriteTime
    if ($item.PSIsContainer) {
        $newest = Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($newest -and $newest.LastWriteTime -gt $latest) { $latest = $newest.LastWriteTime }
    }
    return $latest
}

$script:Plan = New-Object 'System.Collections.Generic.List[object]'
function Add-Removal([string]$Group, [string]$Path) {
    $script:Plan.Add([pscustomobject]@{ Group = $Group; Path = $Path; Bytes = (Get-SizeBytes $Path) })
}

Push-Location $script:Repository
try {
    $paths = Invoke-LocalPython @'
import json
from pipeline.config import load_config
print(json.dumps({"assets": str(load_config().paths.assets_dir)}))
'@
    $assets = $paths.assets
    $now = Get-Date

    # 1. Repository scratch.
    $scratch = Join-Path $script:Repository '.tmp'
    if (Test-Path -LiteralPath $scratch) {
        foreach ($entry in Get-ChildItem -LiteralPath $scratch -Force) {
            $name = $entry.Name
            if ($name -eq 'services' -or $name -like 'framing-*' -or $name -like 'match-*') { continue }
            $pytest = $entry.PSIsContainer -and ($name -match '^pt\d*$' -or $name -like 'pytest-*')
            if ($pytest -or ($now - (Get-LastTouched $entry.FullName)).TotalHours -ge 12) {
                Add-Removal 'repository scratch (.tmp)' $entry.FullName
            }
        }
    }

    # 2. Evaluation renders.
    $renders = Join-Path $assets 'lab\renders'
    if (Test-Path -LiteralPath $renders) {
        foreach ($entry in Get-ChildItem -LiteralPath $renders -Directory -Force) {
            $name = $entry.Name
            if (-not ($name -like 'eval-*' -or $name -like 'compare-*' -or $name -like 'harness-*')) { continue }
            $comparison = $name -match '^eval-.+-(v1|v2-c)$'
            if ($comparison -and -not $IncludeComparisons) { continue }
            Add-Removal 'evaluation renders' $entry.FullName
        }
    }

    # 3. Interrupted understanding proxies.
    $proxies = Join-Path $assets '.tmp\understanding'
    if (Test-Path -LiteralPath $proxies) {
        foreach ($entry in Get-ChildItem -LiteralPath $proxies -Directory -Force) {
            if (($now - (Get-LastTouched $entry.FullName)).TotalHours -ge 6) {
                Add-Removal 'stale understanding proxies' $entry.FullName
            }
        }
    }

    $groups = $script:Plan | Group-Object Group
    Write-Host ($(if ($Apply) { 'Removing:' } else { 'Dry run - nothing is removed. A real run would remove:' }))
    foreach ($group in $groups) {
        $bytes = ($group.Group | Measure-Object Bytes -Sum).Sum
        Write-Host ("  {0,-30} {1,5} item(s)  {2,8:N2} GB" -f $group.Name, $group.Count, ($bytes / 1GB))
    }
    $total = ($script:Plan | Measure-Object Bytes -Sum).Sum
    Write-Host ("  {0,-30} {1,5} item(s)  {2,8:N2} GB" -f 'total', $script:Plan.Count, ($total / 1GB))

    if ($Apply) {
        $failed = 0
        foreach ($item in $script:Plan) {
            try { Remove-Item -LiteralPath $item.Path -Recurse -Force -ErrorAction Stop }
            catch { $failed++; Write-Warning "Could not remove $($item.Path): $($_.Exception.Message)" }
        }
        if ($failed) { Write-Warning "$failed item(s) could not be removed (in use?); rerun later." }
    }

    # 4. The project's own lifecycle commands (each is a dry run without -Apply).
    Write-Host ''
    Write-Host 'Superseded evidence profiles:'
    $pruneArgs = @('-m', 'pipeline.evidence', 'prune') + $(if ($Apply) { @('--apply') } else { @() })
    & $script:Python @pruneArgs
    Write-Host 'Orphaned Lab job files:'
    $labArgs = @('-m', 'pipeline.lab.cleanup') + $(if ($Apply) { @('--apply') } else { @() })
    & $script:Python @labArgs | Out-String | ForEach-Object {
        if ($_ -match '"bytes":\s*(\d+)') { Write-Host ("  {0:N2} GB eligible" -f ([int64]$Matches[1] / 1GB)) } else { Write-Host $_ }
    }
    Write-Host ''
    Write-Host 'Database history is separate: scripts\restart-scene-recall.ps1 -WaitForJobs -MaintainDatabase'
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
} finally { Pop-Location }
