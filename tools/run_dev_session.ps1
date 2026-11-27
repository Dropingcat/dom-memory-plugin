#requires -Version 5.1
<#
.SYNOPSIS
    Run an OpenCode live session against the project DOM plugin with an isolated dev config.

.DESCRIPTION
    Sets OPENCODE_CONFIG to <repo>/opencode/opencode.dev.json for the duration of one
    `opencode-cli run` call. The dev config loads ONLY the project plugin and the project
    dom-memory MCP server, so the globally installed DOM plugin (same id: dom-memory.hooks)
    cannot win plugin de-duplication.

    OPENCODE_CONFIG is restored (or removed, if it was unset) after the run.

.PARAMETER Model
    Model in provider/model form. Default: polza/deepseek/deepseek-v4.1-flash-1017

.PARAMETER Prompt
    Message to send. Extra positional arguments are appended after it and passed through
    to `opencode-cli run`.

.EXAMPLE
    tools/run_dev_session.ps1 -Prompt "Reply with exactly: ok"

.EXAMPLE
    tools/run_dev_session.ps1 -Model polza/deepseek/deepseek-v4.1-pro -Prompt "Check DOM hooks" --agent explore
#>
[CmdletBinding()]
param(
    [string]$Model = "polza/deepseek/deepseek-v4.1-flash-1017",
    [string]$Prompt,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

# Quote one argument the way Windows/MSVCRT expects it (same algorithm as .NET).
function ConvertTo-ProcessArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq [char]'\') {
            $backslashes++
            continue
        }
        if ($ch -eq [char]'"') {
            [void]$builder.Append([string]::new([char]'\', ($backslashes * 2) + 1))
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append([string]::new([char]'\', $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($ch)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append([string]::new([char]'\', $backslashes * 2))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

# $PSScriptRoot is <repo>/tools
$repoRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $repoRoot "opencode\opencode.dev.json"

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    Write-Error "Dev config not found: $configPath"
    exit 1
}

$cli = "C:\Users\Arhys\AppData\Local\Programs\ai.opencode.desktop\resources\opencode-cli.exe"
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    $cli = (Get-Command opencode -ErrorAction SilentlyContinue).Source
}
if (-not $cli) {
    Write-Error "opencode CLI not found. Looked for 'C:\Users\Arhys\AppData\Local\Programs\ai.opencode.desktop\resources\opencode-cli.exe' and 'opencode' on PATH."
    exit 1
}

Write-Host "OPENCODE_CONFIG = $configPath"
Write-Host "CLI             = $cli"
Write-Host "Workdir         = $repoRoot"
Write-Warning "Global DOM plugin is BYPASSED: this dev config loads only the project plugin (id: dom-memory.hooks)."

$promptArgs = @()
if ($Prompt) { $promptArgs += $Prompt }
if ($Rest) { $promptArgs += $Rest }

$runArgs = @(
    "run",
    "--standalone",
    "--print-logs",
    "--auto",
    "--format", "json",
    "--model", $Model
) + $promptArgs

$previousConfig = $env:OPENCODE_CONFIG
$env:OPENCODE_CONFIG = $configPath
$exitCode = 1
$exitCode = 1

Push-Location $repoRoot
try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $cli
    $psi.WorkingDirectory = $repoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.Arguments = (($runArgs | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " ")

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    if (-not $process.Start()) {
        throw "Failed to start: $cli"
    }

    # Read both pipes asynchronously so neither can fill and block the child.
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()

    # Write raw text straight to the underlying streams: PowerShell 5.1 would re-wrap
    # native stderr at the console width and split long marker lines.
    [Console]::Out.Write($stdoutTask.Result)
    [Console]::Out.Flush()
    [Console]::Error.Write($stderrTask.Result)
    [Console]::Error.Flush()

    $exitCode = $process.ExitCode
}
finally {
    Pop-Location
    if ($null -eq $previousConfig) {
        Remove-Item Env:OPENCODE_CONFIG -ErrorAction SilentlyContinue
    }
    else {
        $env:OPENCODE_CONFIG = $previousConfig
    }
}

exit $exitCode
