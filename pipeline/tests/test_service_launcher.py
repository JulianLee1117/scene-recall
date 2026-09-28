"""Run launcher logic in both Windows shells with isolated, inert service mocks."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "start-scene-recall.ps1"
SHELLS = [path for name in ("powershell.exe", "pwsh.exe") if (path := shutil.which(name))]


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(params=SHELLS or [None], ids=lambda value: Path(value).name if value else "no-windows-shell")
def shell(request):
    if request.param is None:
        pytest.skip("Windows PowerShell is unavailable")
    return request.param


def run(shell, code):
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
        f"$ErrorActionPreference='Stop'; . {literal(LAUNCHER)}; " + code],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_missing_storage_stops_before_starting_services(shell, tmp_path):
    output = run(shell, f"""
        $script:LogRoot = {literal(tmp_path / 'not-created')}
        function Get-Preflight {{ throw 'state_dir is missing or its drive is unmounted' }}
        function Start-HiddenService {{ throw 'UNEXPECTED START' }}
        try {{ Start-SceneRecall; throw 'EXPECTED FAILURE' }} catch {{
            if ($_.ToString() -notmatch 'drive is unmounted') {{ throw }}
        }}
        if (Test-Path -LiteralPath $script:LogRoot) {{ throw 'Created storage before preflight' }}
        'PASS'
    """)
    assert "PASS" in output


def test_unrelated_port_owner_is_rejected_without_stopping_it(shell):
    output = run(shell, """
        function Get-NetTCPConnection { [pscustomobject]@{ OwningProcess = 555 } }
        function Get-CimInstance { [pscustomobject]@{ CommandLine = 'python unrelated_app.py' } }
        function Stop-Process { throw 'UNEXPECTED STOP' }
        try { [void](Test-OwnedListener 8000 'api'); throw 'EXPECTED FAILURE' } catch {
            if ($_.ToString() -notmatch 'unrecognized process') { throw }
        }
        'PASS'
    """)
    assert "PASS" in output


def test_api_ownership_requires_this_checkout_in_live_parent_chain(shell):
    output = run(shell, """
        $script:ExpectedPython = $script:Python
        $script:ParentPython = 'C:\\other-checkout\\.venv\\Scripts\\python.exe'
        function Get-NetTCPConnection { [pscustomobject]@{ OwningProcess=555 } }
        function Get-CimInstance($ClassName, $Filter) {
            if ($Filter -eq 'ProcessId=555') {
                [pscustomobject]@{ CommandLine='python -m uvicorn pipeline.api.main:app'; ExecutablePath='C:\\base\\python.exe'; ParentProcessId=444 }
            } elseif ($Filter -eq 'ProcessId=444') {
                [pscustomobject]@{ ExecutablePath=$script:ParentPython; ParentProcessId=0 }
            } else { throw 'Unexpected ancestry query' }
        }
        try { [void](Test-OwnedListener 8000 'api'); throw 'EXPECTED FAILURE' } catch {
            if ($_.ToString() -notmatch 'ownership by this checkout could not be verified') { throw }
        }
        $script:ParentPython=$script:ExpectedPython
        if (-not (Test-OwnedListener 8000 'api')) { throw 'Venv redirector parent was not recognized' }
        'PASS'
    """)
    assert "PASS" in output


def test_partial_workers_start_only_missing_role(shell):
    output = run(shell, """
        $script:Launches = @()
        $script:State = [pscustomobject]@{ workers = @(
            [pscustomobject]@{ role='editor'; state='busy'; online=$true; locked=$true },
            [pscustomobject]@{ role='ingest'; state='offline'; online=$false; locked=$false }
        ) }
        function Get-LocalStatus { $script:State }
        function Start-HiddenService($Name, $Executable, $Arguments, $WorkingDirectory) {
            $script:Launches += $Name
            $script:State.workers[1].locked = $true
            $script:State.workers[1].online = $true
            $script:State.workers[1].state = 'idle'
            [pscustomobject]@{ Id=123 }
        }
        function Wait-Ready($Name, $Check, $Process) { if (-not (& $Check)) { throw 'Not ready' } }
        Ensure-Worker 'editor'
        Ensure-Worker 'ingest'
        Ensure-Worker 'editor'
        Ensure-Worker 'ingest'
        if ($script:Launches.Count -ne 1 -or $script:Launches[0] -ne 'worker-ingest') { throw 'Duplicate or wrong-role launch' }
        'PASS'
    """)
    assert "PASS" in output


def test_held_worker_lock_with_stale_heartbeat_never_starts_duplicate(shell):
    output = run(shell, """
        function Get-LocalStatus { [pscustomobject]@{ workers=@(
            [pscustomobject]@{ role='editor'; state='offline'; online=$false; locked=$true }) } }
        function Start-HiddenService { throw 'UNEXPECTED START' }
        function Wait-Ready($Name, $Check, $Process) {
            if (& $Check) { throw 'Stale heartbeat accepted as ready' }
            throw 'WAITING FOR OWNER'
        }
        try { Ensure-Worker 'editor'; throw 'EXPECTED FAILURE' } catch {
            if ($_.ToString() -ne 'WAITING FOR OWNER') { throw }
        }
        'PASS'
    """)
    assert "PASS" in output


def test_delayed_monitor_heartbeat_reuses_lock_and_stale_unlocked_monitor_starts(shell):
    output = run(shell, """
        $script:Launches = 0
        $script:Monitor = [pscustomobject]@{ locked=$true; running=$false }
        function Get-LocalStatus { [pscustomobject]@{ monitor=$script:Monitor } }
        function Start-HiddenService {
            $script:Launches++
            $script:Monitor.locked=$true
            [pscustomobject]@{ Id=123 }
        }
        function Wait-Ready($Name, $Check, $Process) {
            $script:Monitor.running=$true
            if (-not (& $Check)) { throw 'Not ready after heartbeat' }
        }
        Ensure-AcquisitionMonitor
        if ($script:Launches -ne 0) { throw 'Duplicated delayed monitor' }
        $script:Monitor.locked=$false
        Ensure-AcquisitionMonitor
        Ensure-AcquisitionMonitor
        if ($script:Launches -ne 1) { throw 'Did not start exactly one missing monitor' }
        'PASS'
    """)
    assert "PASS" in output


def test_draining_worker_is_not_restarted(shell):
    output = run(shell, """
        function Get-LocalStatus { [pscustomobject]@{ workers=@(
            [pscustomobject]@{ role='editor'; state='stopping'; online=$true; locked=$true }) } }
        function Start-HiddenService { throw 'UNEXPECTED START' }
        try { Ensure-Worker 'editor'; throw 'EXPECTED FAILURE' } catch {
            if ($_.ToString() -notmatch 'deliberately draining') { throw }
        }
        'PASS'
    """)
    assert "PASS" in output


def test_processes_start_hidden_with_quoted_arguments_and_separate_logs(shell, tmp_path):
    output = run(shell, f"""
        $script:LogRoot = {literal(tmp_path)}
        function Start-Process {{
            param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru,
                  $RedirectStandardOutput, $RedirectStandardError)
            if ($WindowStyle -ne 'Hidden') {{ throw 'Visible helper' }}
            if ($ArgumentList[0] -ne '"path with spaces"') {{ throw 'Unquoted path' }}
            if ($RedirectStandardOutput -eq $RedirectStandardError) {{ throw 'Merged redirection' }}
            [pscustomobject]@{{ Id=123 }}
        }}
        [void](Start-HiddenService 'test-service' 'python.exe' @('path with spaces', '-m') {literal(ROOT)})
        'PASS'
    """)
    assert "PASS" in output


def test_failure_tail_never_prints_bare_credentials_or_provider_messages(shell, tmp_path):
    secret = "test-bare-secret-unique-99401"
    (tmp_path / "test.stderr.log").write_text(f"ValueError: {secret}\n{secret}\nINFO: Application startup complete.\n")
    output = run(shell, f"$script:LogRoot={literal(tmp_path)}; Show-FailureLogs 'test'")
    assert secret not in output
    assert "ValueError" in output and "Application startup complete" in output
