[CmdletBinding()]
param(
    [string]$Version
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ASCII source keeps this script compatible with Windows PowerShell 5.1 without a BOM.
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$utf8 = [System.Text.UTF8Encoding]::new($false, $true)

try {
    $packagePath = Join-Path $projectRoot 'package.json'
    $package = [System.IO.File]::ReadAllText($packagePath, $utf8) | ConvertFrom-Json
    Write-Host "Current frontend version: $($package.version)"

    if (-not $PSBoundParameters.ContainsKey('Version')) {
        $Version = Read-Host 'Enter new version (e.g. 1.5.3 or v1.5.3; empty to cancel)'
    }
    $Version = $Version.Trim()
    if (-not $Version) {
        Write-Host 'Cancelled. No files changed.'
        exit 0
    }
    if ($Version -cnotmatch '^[vV]?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
        throw 'Invalid version. Use three numbers such as 1.5.3 or v1.5.3 (no leading zeros).'
    }
    $Version = $Version -replace '^[vV]', ''
    $displayVersion = "v$Version"

    # Only these current-version fields are changed; release history and dependencies stay intact.
    # Count = 0 means one or more matches (asset counts may grow as pages gain resources).
    $assetRule = @{
        Pattern = "(url_for\('static',\s*filename='[^'\r\n]+\.(?:css|js)',\s*v=')[^'\r\n]+(')"
        Value = $displayVersion
        Count = 0
    }
    $footerRule = @{
        Pattern = '(>v)[0-9]+\.[0-9]+\.[0-9]+( LTS\b)'
        Value = $Version
        Count = 1
    }
    $targets = @(
        @{
            Path = 'package.json'
            Rules = @(@{
                Pattern = '(?m)^([ \t]*"version"[ \t]*:[ \t]*")[^"\r\n]+(")'
                Value = $Version
                Count = 1
            })
        },
        @{
            Path = 'api/system.py'
            Rules = @(@{
                Pattern = '(?m)^(CURRENT_VERSION[ \t]*=[ \t]*")[^"\r\n]+(")'
                Value = $displayVersion
                Count = 1
            })
        },
        @{ Path = 'templates/agent_chat.html'; Rules = @($footerRule, $assetRule) },
        @{ Path = 'templates/login.html'; Rules = @($footerRule, $assetRule) },
        @{ Path = 'templates/admin.html'; Rules = @($assetRule) },
        @{ Path = 'templates/dashboard.html'; Rules = @($assetRule) }
    )

    # Validate every target before the first write, preserving unrelated text and JSON formatting.
    $changes = [System.Collections.Generic.List[object]]::new()
    foreach ($target in $targets) {
        $path = Join-Path $projectRoot $target.Path
        $originalBytes = [System.IO.File]::ReadAllBytes($path)
        $before = [System.IO.File]::ReadAllText($path, $utf8)
        $after = $before
        foreach ($rule in $target.Rules) {
            $count = [regex]::Matches($after, $rule.Pattern).Count
            if ($count -eq 0 -or ($rule.Count -gt 0 -and $count -ne $rule.Count)) {
                throw "Expected version field(s) not found in $($target.Path). No files changed."
            }
            $replacement = '${1}' + $rule.Value + '${2}'
            $after = [regex]::Replace($after, $rule.Pattern, $replacement)
        }
        $after = $after.Replace("`r`n", "`n").Replace("`r", "`n")
        if ($after -cne $before) {
            $changes.Add([pscustomobject]@{
                Path = $path
                Name = $target.Path
                Before = $before
                OriginalBytes = $originalBytes
                After = $after
            })
        }
    }

    $written = [System.Collections.Generic.List[object]]::new()
    try {
        foreach ($change in $changes) {
            if ([System.IO.File]::ReadAllText($change.Path, $utf8) -cne $change.Before) {
                throw "File changed during update: $($change.Name). Run the updater again."
            }
            # Include the attempted file so a partially failed write is restored too.
            $written.Add($change)
            [System.IO.File]::WriteAllText($change.Path, $change.After, $utf8)
        }
    } catch {
        foreach ($change in $written) {
            try {
                [System.IO.File]::WriteAllBytes($change.Path, $change.OriginalBytes)
            } catch {
                Write-Warning "Could not restore $($change.Name): $($_.Exception.Message)"
            }
        }
        throw
    }

    foreach ($change in $changes) {
        Write-Host "Updated: $($change.Name)"
    }
    Write-Host "Frontend version: $displayVersion. Files updated: $($changes.Count)."
    Write-Host 'Restart the app and refresh the browser to see the new version.'
    Write-Host 'Source files only. No build, packaging, commit, or push was performed.'
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
