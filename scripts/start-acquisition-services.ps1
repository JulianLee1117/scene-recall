param(
    [string]$ToolsDirectory = (Join-Path $env:LOCALAPPDATA 'SceneRecall\tools')
)

$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $ToolsDirectory 'acquisition-services.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'Portable acquisition helpers are not installed. Follow the acquisition setup instructions in README.md.'
}

$toolRoot = [System.IO.Path]::GetFullPath($ToolsDirectory).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
# Windows PowerShell 5.1 returns a JSON array as one pipeline object. Wrapping
# that pipeline in @() nests the array and makes service.name an array too.
$services = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
foreach ($service in $services) {
    if ($service.name -notin @('qbittorrent', 'prowlarr')) {
        throw 'The acquisition service manifest contains an unknown helper.'
    }
    $executable = [System.IO.Path]::GetFullPath($service.executable)
    if (-not $executable.StartsWith($toolRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $executable -PathType Leaf) -or
        -not (Test-Path -LiteralPath $service.profile -PathType Container)) {
        throw "The portable $($service.name) installation or isolated profile is missing."
    }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $service.port -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        foreach ($listener in $listeners) {
            $owner = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
            if ($owner.Path -ne $executable -or $listener.LocalAddress -ne '127.0.0.1') {
                throw "Port $($service.port) belongs to another process or is not bound to loopback."
            }
        }
        Write-Output "$($service.name) is already running on 127.0.0.1:$($service.port)."
        continue
    }
    if ($service.profile -match '["\r\n]') {
        throw 'The helper profile path is invalid.'
    }
    $arguments = if ($service.name -eq 'qbittorrent') {
        @('--profile="' + $service.profile + '"', '--webui-port=' + $service.port, '--no-splash', '--confirm-legal-notice')
    } else {
        @('-data="' + $service.profile + '"', '-nobrowser')
    }
    $started = Start-Process -FilePath $executable -ArgumentList $arguments -WorkingDirectory (Split-Path -Parent $executable) -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $listening = $false
    do {
        $started.Refresh()
        if ($started.HasExited) { throw "$($service.name) exited before its API started." }
        $listening = [bool](Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort $service.port -ErrorAction SilentlyContinue |
            Where-Object { $_.OwningProcess -eq $started.Id })
        if (-not $listening) { Start-Sleep -Milliseconds 300 }
    } while (-not $listening -and [DateTime]::UtcNow -lt $deadline)
    if (-not $listening) { throw "$($service.name) did not start its loopback API within 30 seconds." }
    Write-Output "$($service.name) started on 127.0.0.1:$($service.port)."
}

Write-Output 'Acquisition helpers are ready. Start the separate acquisition worker to process queued films.'
