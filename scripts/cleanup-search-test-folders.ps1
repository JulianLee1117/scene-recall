# Removes only the identified temporary test directories from the
# September 20 search work. Preview is the default; deletion requires -Apply.
[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$searchTestAllowlist = @(
    'C:\Users\julia\.t\sfd8a89d17',
    'C:\Users\julia\.t\sf479f64fd',
    'C:\Users\julia\.t\sf332e1080',
    'C:\Users\julia\.t\sf2dd16a57',
    'C:\Users\julia\.t\sf16d38dc9',
    'C:\Users\julia\.t\sfe8604fdd',
    'C:\Users\julia\.t\srd7903dff',
    'C:\Users\julia\.t\st85cb8861',
    'C:\Users\julia\.t\ir9dcf2eff',
    'C:\Users\julia\.t\ir356032c6',
    'C:\Users\julia\.t\ir28da3e80',
    'C:\Users\julia\.t\irb02be651',
    'C:\Users\julia\.t\ire19bd54b',
    'C:\Users\julia\dev\scene-recall\.tmp\search-foundation-tests-bacddea17a8e4819a96df9569c1adf0f'
)
$searchAllowedParents = @(
    'C:\Users\julia\.t',
    'C:\Users\julia\dev\scene-recall\.tmp'
)
$searchChecked = @()

# Inspect every target before deleting any. Walk one directory at a time so
# junctions and symbolic links are rejected before descending into them.
foreach ($searchCandidate in $searchTestAllowlist) {
    if (-not (Test-Path -LiteralPath $searchCandidate)) { continue }
    $searchItem = Get-Item -LiteralPath $searchCandidate -Force
    $searchResolved = [IO.Path]::GetFullPath($searchItem.FullName).TrimEnd('\')
    if ($searchResolved -cne $searchCandidate -or
        [IO.Path]::GetDirectoryName($searchResolved) -notin $searchAllowedParents -or
        -not $searchItem.PSIsContainer) {
        throw "Unexpected test directory: $searchCandidate"
    }
    $searchPending = [Collections.Generic.Stack[string]]::new()
    $searchPending.Push($searchResolved)
    [long]$searchBytes = 0
    while ($searchPending.Count -gt 0) {
        $searchDirectory = Get-Item -LiteralPath $searchPending.Pop() -Force
        if ($searchDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Link or junction needs manual inspection: $($searchDirectory.FullName)"
        }
        foreach ($searchChild in Get-ChildItem -LiteralPath $searchDirectory.FullName -Force) {
            if ($searchChild.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Link or junction needs manual inspection: $($searchChild.FullName)"
            }
            if ($searchChild.PSIsContainer) {
                $searchPending.Push($searchChild.FullName)
            } else {
                $searchBytes += $searchChild.Length
            }
        }
    }
    $searchChecked += [pscustomobject]@{ Path = $searchResolved; MiB = [Math]::Round($searchBytes / 1MB, 2) }
}

$searchChecked | Format-Table -AutoSize
if (-not $Apply) {
    Write-Host 'Preview only. Rerun with -Apply to remove exactly these test folders.'
    return
}

foreach ($searchEntry in $searchChecked) {
    $searchFinalPath = [IO.Path]::GetFullPath($searchEntry.Path)
    if ($searchFinalPath -notin $searchTestAllowlist -or
        [IO.Path]::GetDirectoryName($searchFinalPath) -notin $searchAllowedParents) {
        throw 'Deletion target is outside the verified allowlist.'
    }
    $searchFinalItem = Get-Item -LiteralPath $searchFinalPath -Force
    if ($searchFinalItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Target changed into a link: $searchFinalPath"
    }
    if ($PSCmdlet.ShouldProcess($searchFinalPath, 'Remove completed temporary test folder')) {
        # Windows PowerShell 5.1 cannot delete paths over 260 characters (the
        # long-path test fixtures), so use the extended-length prefix via cmd.
        cmd.exe /d /c rmdir /s /q "\\?\$searchFinalPath"
        if (Test-Path -LiteralPath $searchFinalPath) {
            throw "Failed to remove: $searchFinalPath"
        }
    }
}
