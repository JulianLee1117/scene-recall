param(
    [switch]$ReloadWorkers,
    [ValidateRange(10, 300)][int]$ReadyTimeoutSeconds = 120,
    [string]$ToolsDirectory = (Join-Path $env:LOCALAPPDATA 'SceneRecall\tools')
)

# Windows PowerShell 5.1 and PowerShell 7; dependencies must already be installed.
$ErrorActionPreference = 'Stop'
$script:Repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:Python = Join-Path $script:Repository '.venv\Scripts\python.exe'
$script:LogRoot = Join-Path $script:Repository '.tmp\services'
$script:Started = 0
$script:Reused = 0

function Invoke-LocalPython([string]$Code) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $script:Python
    $info.Arguments = '-u -'
    $info.WorkingDirectory = $script:Repository
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $outputTask = $process.StandardOutput.ReadToEndAsync()
        $errorTask = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.Write($Code)
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(20000)) {
            $process.Kill() # Only this launcher's short-lived local status probe.
            throw 'The local configuration/status check timed out; no service was stopped.'
        }
        $output = $outputTask.Result
        [void]$errorTask.Result
        if ($process.ExitCode -ne 0) {
            throw 'The local configuration/status check failed. Check config.yaml and the installed .venv dependencies; credentials were not printed.'
        }
        return ($output | ConvertFrom-Json)
    } finally { $process.Dispose() }
}

function Get-Preflight {
    if (-not (Test-Path -LiteralPath $script:Python -PathType Leaf)) {
        throw 'Missing .venv\Scripts\python.exe. Complete README Setup first; this launcher does not install packages.'
    }
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $node) { throw 'Node.js is not on PATH. Complete README Setup first.' }
    $next = Join-Path $script:Repository 'web\node_modules\next\dist\bin\next'
    if (-not (Test-Path -LiteralPath $next -PathType Leaf)) {
        throw 'Missing web/node_modules. Complete README Setup first; this launcher does not install packages.'
    }
    $result = Invoke-LocalPython @'
import json, shutil
from dotenv import load_dotenv
load_dotenv('.env')
from pipeline.config import load_config
from pipeline.acquisition.settings import load_settings
config, settings = load_config(), load_settings()
errors = []
for name in ('films_dir', 'incoming_dir', 'state_dir', 'assets_dir'):
    path = getattr(config.paths, name).resolve()
    if not path.is_dir():
        errors.append(f'{name} is missing or its drive is unmounted: {path}')
if config.paths.playback_dir is not None and not config.paths.playback_dir.is_dir():
    errors.append(f'playback_dir is missing or its drive is unmounted: {config.paths.playback_dir}')
for tool in ('ffmpeg', 'ffprobe'):
    if not shutil.which(tool):
        errors.append(f'{tool} is not on PATH')
print(json.dumps({'errors': errors, 'downloads': settings.downloads_enabled,
                 'search': settings.search_enabled}))
'@
    if (@($result.errors).Count) { throw (($result.errors -join "`n") + "`nNo services were started and no configured folders were created.") }
    if (($result.downloads -or $result.search) -and -not (Test-Path -LiteralPath (Join-Path $ToolsDirectory 'acquisition-services.json') -PathType Leaf)) {
        throw 'Portable acquisition helpers are not installed. Follow README Managed downloads before using this all-services launcher.'
    }
    return @{ Node = $node.Source; Next = $next; Downloads = $result.downloads; Search = $result.search }
}

function Get-LocalStatus {
    Invoke-LocalPython @'
import json, sqlite3, time
from dotenv import load_dotenv
load_dotenv('.env')
from pipeline.config import load_config
from pipeline.lab.store import LabStore
from pipeline.lab.worker_control import worker_status
from pipeline.lab.worker_locks import worker_lock
from filelock import FileLock, Timeout
config = load_config()
store = LabStore(config.paths.state_dir)
status = worker_status(store)
for worker in status['workers']:
    worker['locked'] = False
    if store.root.is_dir():
        try:
            with worker_lock(store.root, worker['role']):
                pass
        except Timeout:
            worker['locked'] = True
root = config.paths.state_dir / 'acquisition'
monitor = {'locked': False, 'running': False}
if root.is_dir():
    try:
        with FileLock(root / '.monitor.lock', timeout=0, preserve_lock_file=True):
            pass
    except Timeout:
        monitor['locked'] = True
    path = root / 'acquisition.sqlite3'
    if path.is_file():
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2) as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='monitor'").fetchone():
                row = con.execute("SELECT last_seen FROM monitor WHERE name='worker'").fetchone()
                monitor['running'] = bool(row and time.time() - row[0] < 90)
status['monitor'] = monitor
print(json.dumps(status))
'@
}

function Test-RepositoryPython($Process) {
    # A Windows venv redirector can own the base interpreter which listens on
    # the port. Follow only its bounded live ancestry, never infer the checkout
    # from the shared application module name or a reused parent PID.
    $current = $Process
    $seen = @{}
    for ($depth = 0; $depth -lt 8 -and $null -ne $current; $depth++) {
        if ([string]$current.ExecutablePath -ieq $script:Python) { return $true }
        $parentId = [int]$current.ParentProcessId
        if ($parentId -le 0 -or $seen.ContainsKey($parentId)) { return $false }
        $seen[$parentId] = $true
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$parentId" -OperationTimeoutSec 3 -ErrorAction Stop
        if ($null -eq $parent) { return $false }
        if ($null -ne $parent.CreationDate -and $null -ne $current.CreationDate -and $parent.CreationDate -gt $current.CreationDate) { return $false }
        $current = $parent
    }
    return $false
}

function Test-OwnedListener([int]$Port, [string]$Kind) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if (-not $listeners.Count) { return $false }
    foreach ($listener in $listeners) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -OperationTimeoutSec 3 -ErrorAction Stop
        $command = [string]$owner.CommandLine
        $owned = if ($Kind -eq 'api') {
            $command -match '(?i)\buvicorn\b' -and $command -match 'pipeline\.api\.main:app' -and (Test-RepositoryPython $owner)
        } else {
            $command.Replace('/', '\').IndexOf((Join-Path $script:Repository 'web\node_modules\next\'), [StringComparison]::OrdinalIgnoreCase) -ge 0
        }
        if (-not $owned) { throw "Port $Port belongs to an unrecognized process (PID $($listener.OwningProcess)); ownership by this checkout could not be verified. Close or reconfigure it yourself; nothing was stopped." }
    }
    return $true
}

function Test-WebReady([string]$Kind) {
    try {
        if ($Kind -eq 'api') {
            $response = Invoke-RestMethod 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 3
            return ($response.info.title -eq 'scene-recall' -and $null -ne $response.paths.'/lab/workers' -and $null -ne $response.paths.'/acquisition/status')
        }
        $response = Invoke-WebRequest 'http://127.0.0.1:3000/' -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200 -and $response.Content -match '<title>scene-recall</title>')
    } catch { return $false }
}

function Show-FailureLogs([string]$Name) {
    # Provider errors can contain bare credentials, not only labelled secrets.
    # Expose exception types and known startup states, never arbitrary messages.
    foreach ($suffix in @('stderr', 'stdout')) {
        $path = Join-Path $script:LogRoot "$Name.$suffix.log"
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Write-Host "Recent safe diagnostics ($path):"
            Get-Content -LiteralPath $path -Tail 12 | ForEach-Object {
                if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):') {
                    Write-Host ($Matches[1] + ': inspect the local log for details (message kept private).')
                } elseif ($_ -match '^INFO:\s+(Application startup complete|Waiting for application startup|Application shutdown complete)\.') {
                    Write-Host $Matches[1]
                } elseif ($_ -match '^\[(editor|ingest)\] Lab worker ready:') {
                    Write-Host ($Matches[1] + ' worker initialized.')
                } else { Write-Host '[diagnostic retained in local log]' }
            }
        }
    }
}

function Start-HiddenService([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory) {
    $quoted = @($Arguments | ForEach-Object {
        if ($_ -match '["\r\n]' -or $_.EndsWith('\')) { throw 'Invalid service argument.' }
        '"' + $_ + '"'
    })
    # Preserve the previous launch log. Reused processes keep their open logs.
    foreach ($suffix in @('stdout', 'stderr')) {
        $path = Join-Path $script:LogRoot "$Name.$suffix.log"
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination "$path.previous" -Force }
    }
    $process = Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $script:LogRoot "$Name.stdout.log") -RedirectStandardError (Join-Path $script:LogRoot "$Name.stderr.log")
    $script:Started++
    Write-Host "Starting $Name (PID $($process.Id))..."
    return $process
}

function Wait-Ready([string]$Name, [scriptblock]$Check, $Process = $null) {
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    do {
        if (& $Check) { return }
        if ($null -ne $Process) {
            $Process.Refresh()
            if ($Process.HasExited) { Show-FailureLogs $Name; throw "$Name exited before becoming ready (exit $($Process.ExitCode))." }
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    Show-FailureLogs $Name
    throw "$Name did not become ready within $ReadyTimeoutSeconds seconds. A held lock is never overridden; existing processes were left running. Logs: $script:LogRoot"
}

function Ensure-WebService([string]$Name, [int]$Port, [string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory) {
    $process = $null
    if (Test-OwnedListener $Port $Name) {
        $script:Reused++
        Write-Host "Reusing $Name on port $Port."
    } else { $process = Start-HiddenService $Name $Executable $Arguments $WorkingDirectory }
    Wait-Ready $Name { (Test-OwnedListener $Port $Name) -and (Test-WebReady $Name) } $process
}

function Ensure-Worker([string]$Role) {
    $worker = (Get-LocalStatus).workers | Where-Object role -eq $Role
    $process = $null
    if ($worker.state -eq 'stopping') { throw "$Role is deliberately draining. Wait for it to finish, then run the launcher again." }
    if ($worker.locked) {
        $script:Reused++
        Write-Host "Reusing the $Role worker; checking its heartbeat."
    } else {
        $arguments = @('-u', '-m', 'pipeline.lab.worker', '--role', $Role)
        if ($ReloadWorkers) { $arguments += '--reload' }
        $process = Start-HiddenService "worker-$Role" $script:Python $arguments $script:Repository
    }
    Wait-Ready "worker-$Role" {
        $current = (Get-LocalStatus).workers | Where-Object role -eq $Role
        $current.locked -and $current.online -and $current.state -ne 'stopping'
    } $process
}

function Ensure-AcquisitionMonitor {
    $monitor = (Get-LocalStatus).monitor
    $process = $null
    if ($monitor.locked) {
        $script:Reused++
        Write-Host 'Reusing the acquisition monitor; checking its heartbeat.'
    } else { $process = Start-HiddenService 'acquisition-monitor' $script:Python @('-u', '-m', 'pipeline.acquisition.worker') $script:Repository }
    Wait-Ready 'acquisition-monitor' {
        $current = (Get-LocalStatus).monitor
        $current.locked -and $current.running
    } $process
}

function Start-SceneRecall {
    $preflight = Get-Preflight
    [void][IO.Directory]::CreateDirectory($script:LogRoot)
    try { $guard = [IO.File]::Open((Join-Path $script:LogRoot '.startup.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another Scene Recall launcher is running. Let it finish before starting again.' }
    Push-Location $script:Repository
    try {
        # Reject unrelated port owners before starting any of our processes.
        [void](Test-OwnedListener 8000 'api')
        [void](Test-OwnedListener 3000 'web')
        Ensure-WebService 'api' 8000 $script:Python @('-u', '-m', 'uvicorn', 'pipeline.api.main:app', '--host', '127.0.0.1', '--port', '8000') $script:Repository
        Ensure-WebService 'web' 3000 $preflight.Node @($preflight.Next, 'dev', '--hostname', '127.0.0.1', '--port', '3000') (Join-Path $script:Repository 'web')
        Ensure-Worker 'editor'
        Ensure-Worker 'ingest'
        if ($preflight.Downloads -or $preflight.Search) {
            & (Join-Path $PSScriptRoot 'start-acquisition-services.ps1') -ToolsDirectory $ToolsDirectory
            if ($preflight.Downloads) {
                Wait-Ready 'acquisition-connection' {
                    try { (Invoke-RestMethod 'http://127.0.0.1:8000/acquisition/status' -TimeoutSec 5).downloader.available }
                    catch { $false }
                }
                Ensure-AcquisitionMonitor
            }
        } else { Write-Host 'Managed downloads are not configured; acquisition services were skipped.' }
        Write-Host "Scene Recall is ready: http://localhost:3000 ($script:Started started, $script:Reused reused; helper status above)."
        Write-Host "Service logs: $script:LogRoot"
    } finally { Pop-Location; $guard.Dispose() }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Start-SceneRecall }
    catch { Write-Error $_ -ErrorAction Continue; exit 1 }
}
