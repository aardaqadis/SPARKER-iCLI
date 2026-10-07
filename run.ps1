param([Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArgs)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$TaskRoot = (Get-Item -LiteralPath $PSScriptRoot).FullName
$TaskVenv = Join-Path $TaskRoot '.venv'
$TaskPython = Join-Path $TaskVenv 'Scripts\python.exe'
$TaskInstalled = Join-Path $TaskVenv 'sparker-installed.json'
$TaskStartupHelper = Join-Path $TaskRoot 'src\termatelier\startup.py'
$TaskLogo = Join-Path $TaskRoot 'src\termatelier\assets\logo.png'
$TaskStatePath = $null
$TaskDebugPath = $null
$TaskSplashProcess = $null
$TaskPreviousStartupState = $env:SPARKER_STARTUP_STATE
$TaskPreviousDebugState = $env:SPARKER_DEBUG_STATE
$TaskPreviousOverrides = $env:SPARKER_CONFIG_OVERRIDES
$TaskExitCode = 1
$TaskNoSplash = @($AppArgs | Where-Object { $_ -eq '--no-splash' }).Count -gt 0
$TaskNoDebug = @($AppArgs | Where-Object { $_ -eq '--no-debug' }).Count -gt 0
$TaskLaunchArgs = @($AppArgs | Where-Object { $_ -ne '--no-splash' })
$TaskSettingsArgs = @()
for ($TaskArgumentIndex = 0; $TaskArgumentIndex -lt $AppArgs.Count; $TaskArgumentIndex++) {
    if ($AppArgs[$TaskArgumentIndex] -eq '--set') {
        if ($TaskArgumentIndex + 1 -ge $AppArgs.Count) { throw '--set requires NAME=VALUE.' }
        $TaskArgumentIndex++
        $TaskSettingsArgs += @('--set', $AppArgs[$TaskArgumentIndex])
    } elseif ($AppArgs[$TaskArgumentIndex].StartsWith('--set=')) {
        $TaskSettingsArgs += @('--set', $AppArgs[$TaskArgumentIndex].Substring(6))
    }
}
if (@($AppArgs | Where-Object { $_ -eq '--debug' }).Count -gt 0) {
    $TaskSettingsArgs += @('--set', 'debug.enabled=true')
}
$TaskMinimumSeconds = 2.5
$TaskStartupClock = $null

function ConvertTo-TaskQuotedArgument([string]$Value) {
    # Windows native argument quoting, including trailing backslashes.
    return '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Set-TaskStartupPhase([string]$Phase, [string]$Status) {
    if (-not $TaskStatePath) { return }
    $TaskTemporaryState = $TaskStatePath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        # Preserve editor readiness and any launcher metadata between phases.
        $TaskState = @{}
        if (Test-Path -LiteralPath $TaskStatePath) {
            try {
                $TaskExistingState = Get-Content -LiteralPath $TaskStatePath -Raw | ConvertFrom-Json
                foreach ($TaskProperty in $TaskExistingState.PSObject.Properties) {
                    $TaskState[$TaskProperty.Name] = $TaskProperty.Value
                }
            } catch { Write-Verbose 'Replacing an incomplete startup state.' }
        }
        $TaskState.phase = $Phase
        $TaskState.status = $Status
        $TaskState.parent_pid = $PID
        $TaskState.program_root = $TaskRoot
        $TaskState.venv_python = $TaskPython
        $TaskJson = $TaskState | ConvertTo-Json -Depth 10 -Compress
        [System.IO.File]::WriteAllText($TaskTemporaryState, $TaskJson, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $TaskTemporaryState -Destination $TaskStatePath -Force
    } catch {
        # A startup display must never prevent the editor from running.
        Write-Verbose "Startup status unavailable: $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $TaskTemporaryState) {
            Remove-Item -LiteralPath $TaskTemporaryState -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    $TaskSystemPython = $null
    $TaskPyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($TaskPyLauncher) {
        try {
            $TaskPythonResult = & $TaskPyLauncher.Source -3 -c 'import sys; assert sys.version_info >= (3,11); print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $TaskSystemPython = [string]($TaskPythonResult | Select-Object -Last 1) }
        } catch { Write-Verbose 'Python launcher did not find a supported interpreter.' }
    }
    if (-not $TaskSystemPython) {
        $TaskFallbackPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($TaskFallbackPython) {
            try {
                $TaskPythonResult = & $TaskFallbackPython.Source -c 'import sys; assert sys.version_info >= (3,11); print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0) { $TaskSystemPython = [string]($TaskPythonResult | Select-Object -Last 1) }
            } catch { Write-Verbose 'Python command did not find a supported interpreter.' }
        }
    }
    if (-not $TaskSystemPython -and (Test-Path -LiteralPath $TaskPython)) {
        try {
            $TaskPythonResult = & $TaskPython -c 'import sys; assert sys.version_info >= (3,11); print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $TaskSystemPython = [string]($TaskPythonResult | Select-Object -Last 1) }
        } catch { Write-Verbose 'Existing environment requires repair.' }
    }
    if (-not $TaskSystemPython) {
        throw 'Python 3.11 or newer is required. Install Python, then run again.'
    }

    if (Test-Path -LiteralPath $TaskStartupHelper) {
        $TaskConfiguration = & $TaskSystemPython $TaskStartupHelper --configure @TaskSettingsArgs
        if ($LASTEXITCODE -ne 0) { throw 'Invalid startup settings. See the setting error above.' }
        $TaskConfiguration = ($TaskConfiguration | Select-Object -Last 1) | ConvertFrom-Json
        $TaskMinimumSeconds = [double]$TaskConfiguration.minimum_seconds
        if ($TaskSettingsArgs.Count -gt 0) {
            $env:SPARKER_CONFIG_OVERRIDES = $TaskConfiguration.overrides | ConvertTo-Json -Depth 10 -Compress
        }
        try {
            $TaskStateDirectory = Join-Path $TaskRoot 'work\startup'
            New-Item -ItemType Directory -Path $TaskStateDirectory -Force | Out-Null
            $TaskStatePath = Join-Path $TaskStateDirectory ('startup-' + [guid]::NewGuid().ToString('N') + '.json')
            $TaskDebugPath = Join-Path $TaskStateDirectory ('runtime-' + [guid]::NewGuid().ToString('N') + '.json')
            Set-TaskStartupPhase 'starting' 'Starting'
            $env:SPARKER_STARTUP_STATE = $TaskStatePath
            $env:SPARKER_DEBUG_STATE = $TaskDebugPath
            $TaskPythonWindowless = Join-Path (Split-Path -Parent $TaskSystemPython) 'pythonw.exe'
            $TaskSplashPython = $TaskSystemPython
            if (Test-Path -LiteralPath $TaskPythonWindowless) { $TaskSplashPython = $TaskPythonWindowless }
            $TaskHelperArgs = @($TaskStartupHelper, '--state', $TaskStatePath, '--parent-pid', [string]$PID,
                                '--logo', $TaskLogo, '--debug-state', $TaskDebugPath) + $TaskSettingsArgs
            if ($TaskNoSplash -or -not (Test-Path -LiteralPath $TaskLogo)) { $TaskHelperArgs += '--no-splash' }
            if ($TaskNoDebug) { $TaskHelperArgs += '--no-debug' }
            $TaskHelperCommandLine = ($TaskHelperArgs | ForEach-Object { ConvertTo-TaskQuotedArgument $_ }) -join ' '
            $TaskSplashProcess = Start-Process -FilePath $TaskSplashPython -ArgumentList $TaskHelperCommandLine -WindowStyle Hidden -PassThru
            $TaskStartupClock = [System.Diagnostics.Stopwatch]::StartNew()
        } catch {
            Write-Verbose "Startup window unavailable: $($_.Exception.Message)"
        }
    } else {
        Remove-Item Env:SPARKER_STARTUP_STATE -ErrorAction SilentlyContinue
        Remove-Item Env:SPARKER_DEBUG_STATE -ErrorAction SilentlyContinue
    }

    Set-TaskStartupPhase 'environment' 'Preparing workspace'
    $TaskEnvironmentWorks = $false
    if (Test-Path -LiteralPath $TaskPython) {
        try {
            & $TaskPython -c 'import sys; assert sys.version_info >= (3,11)' 2>$null
            $TaskEnvironmentWorks = $LASTEXITCODE -eq 0
        } catch { $TaskEnvironmentWorks = $false }
    }
    if (-not $TaskEnvironmentWorks) {
        & $TaskSystemPython -m venv $TaskVenv
        if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Python environment. Run again to retry.' }
    }

    $TaskHashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $TaskProjectBytes = [System.IO.File]::ReadAllBytes((Join-Path $TaskRoot 'pyproject.toml'))
        $TaskProjectHash = [BitConverter]::ToString($TaskHashAlgorithm.ComputeHash($TaskProjectBytes)).Replace('-', '')
    } finally { $TaskHashAlgorithm.Dispose() }
    $TaskNeedsInstall = $true
    if (Test-Path -LiteralPath $TaskInstalled) {
        try {
            $TaskStamp = Get-Content -LiteralPath $TaskInstalled -Raw | ConvertFrom-Json
            $TaskNeedsInstall = $TaskStamp.root -ne $TaskRoot -or $TaskStamp.project_hash -ne $TaskProjectHash -or $TaskStamp.brand -ne 'SPARKER iCLI 0.5'
        } catch { $TaskNeedsInstall = $true }
    }
    if (-not $TaskNeedsInstall) {
        # Catch damaged environments and copied editable installations.
        try {
            $TaskSourceCheck = "import pathlib,sys,PIL,textual,termatelier; expected=(pathlib.Path(sys.argv[1])/'src'/'termatelier').resolve(); assert pathlib.Path(termatelier.__file__).resolve().parent==expected"
            & $TaskPython -c $TaskSourceCheck $TaskRoot 2>$null
            if ($LASTEXITCODE -ne 0) { $TaskNeedsInstall = $true }
        } catch { $TaskNeedsInstall = $true }
    }
    if ($TaskNeedsInstall) {
        Set-TaskStartupPhase 'dependencies' 'Installing editor dependencies'
        & $TaskPython -m pip install -e $TaskRoot
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Run again to retry; installation details are in this terminal.' }
        $TaskStamp = @{ root = $TaskRoot; project_hash = $TaskProjectHash; brand = 'SPARKER iCLI 0.5' } | ConvertTo-Json
        [System.IO.File]::WriteAllText($TaskInstalled, $TaskStamp, [System.Text.UTF8Encoding]::new($false))
    }
    Set-TaskStartupPhase 'opening' 'Opening editor'
    if ($TaskStartupClock -and -not $TaskNoSplash) {
        # Preparation counts toward the minimum; quick command jobs see the logo too.
        $TaskRemainingMilliseconds = [int][Math]::Ceiling(($TaskMinimumSeconds - $TaskStartupClock.Elapsed.TotalSeconds) * 1000)
        if ($TaskRemainingMilliseconds -gt 0) { Start-Sleep -Milliseconds $TaskRemainingMilliseconds }
    }
    # Explicit quoting preserves command strings, embedded quotes and paths.
    $TaskEditorArguments = @('-m', 'termatelier') + $TaskLaunchArgs
    $TaskEditorCommandLine = ($TaskEditorArguments | ForEach-Object { ConvertTo-TaskQuotedArgument $_ }) -join ' '
    $TaskEditorProcess = Start-Process -FilePath $TaskPython -ArgumentList $TaskEditorCommandLine -NoNewWindow -PassThru -Wait
    $TaskEditorProcess.Refresh()
    $TaskExitCode = $TaskEditorProcess.ExitCode
    if ($TaskExitCode -ne 0) { Set-TaskStartupPhase 'failed' 'Editor could not start' }
} catch {
    Set-TaskStartupPhase 'failed' 'Startup failed'
    Write-Host "SPARKER iCLI: $($_.Exception.Message)" -ForegroundColor Red
    $TaskExitCode = 1
} finally {
    Set-TaskStartupPhase 'closed' 'Closed'
    if ($TaskSplashProcess) {
        try {
            if (-not $TaskSplashProcess.WaitForExit(1500)) {
                Stop-Process -Id $TaskSplashProcess.Id -ErrorAction SilentlyContinue
            }
        } catch { Write-Verbose 'Startup helper already stopped.' }
    }
    if ($TaskStatePath -and (Test-Path -LiteralPath $TaskStatePath)) {
        Remove-Item -LiteralPath $TaskStatePath -Force -ErrorAction SilentlyContinue
    }
    if ($TaskDebugPath -and (Test-Path -LiteralPath $TaskDebugPath)) {
        Remove-Item -LiteralPath $TaskDebugPath -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $TaskPreviousStartupState) {
        Remove-Item Env:SPARKER_STARTUP_STATE -ErrorAction SilentlyContinue
    } else {
        $env:SPARKER_STARTUP_STATE = $TaskPreviousStartupState
    }
    if ($null -eq $TaskPreviousDebugState) {
        Remove-Item Env:SPARKER_DEBUG_STATE -ErrorAction SilentlyContinue
    } else {
        $env:SPARKER_DEBUG_STATE = $TaskPreviousDebugState
    }
    if ($null -eq $TaskPreviousOverrides) {
        Remove-Item Env:SPARKER_CONFIG_OVERRIDES -ErrorAction SilentlyContinue
    } else {
        $env:SPARKER_CONFIG_OVERRIDES = $TaskPreviousOverrides
    }
}
exit $TaskExitCode
