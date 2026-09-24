param(
    [switch]$SkipSmoke
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$distRoot = Join-Path $projectRoot 'dist'
$distTarget = [IO.Path]::GetFullPath((Join-Path $distRoot 'ModelStudio'))
$buildRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build\pyinstaller'))
$analysisTarget = [IO.Path]::GetFullPath((Join-Path $buildRoot 'ModelStudio'))
$specRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'packaging'))
$projectPrefix = $projectRoot.TrimEnd('\') + '\'

if (-not $distTarget.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not $buildRoot.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not $analysisTarget.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not $specRoot.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build paths escaped the project directory.'
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Create the project .venv and install the build requirements first.'
}

Push-Location $projectRoot
$previousPath = $env:PATH
$previousPythonPath = $env:PYTHONPATH
$previousQtPluginPath = $env:QT_PLUGIN_PATH
$previousQmlImportPath = $env:QML_IMPORT_PATH
try {
    & $python -c "import PyInstaller, PySide6; assert PyInstaller.__version__ == '6.22.3'; assert PySide6.__version__ == '6.11.2'"
    if ($LASTEXITCODE -ne 0) {
        throw 'Expected PyInstaller 6.22.3 and PySide6 6.11.2 in .venv.'
    }
    $pythonBase = (& $python -c 'import sys; print(sys.base_prefix)').Trim()
    # Desktop hosts may prepend unrelated native DLLs to PATH. PyInstaller
    # otherwise bundles those in preference to the matching Windows/Qt DLLs.
    $env:PATH = @((Join-Path $projectRoot '.venv\Scripts'), $pythonBase,
                  (Join-Path $pythonBase 'Scripts'), (Join-Path $env:WINDIR 'System32'),
                  $env:WINDIR) -join ';'
    $env:PYTHONPATH = $null
    $env:QT_PLUGIN_PATH = $null
    $env:QML_IMPORT_PATH = $null
    if (Test-Path -LiteralPath $distTarget) {
        Remove-Item -LiteralPath $distTarget -Recurse -Force
    }
    if (Test-Path -LiteralPath $analysisTarget) {
        Remove-Item -LiteralPath $analysisTarget -Recurse -Force
    }
    $arguments = @(
        '-m', 'PyInstaller', '--noconfirm',
        '--distpath', $distRoot, '--workpath', $buildRoot,
        (Join-Path $specRoot 'ModelStudio.spec')
    )
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    $exe = Join-Path $distTarget 'ModelStudio.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Missing output: $exe"
    }
    if (-not $SkipSmoke) {
        $smokeRoot = Join-Path $buildRoot ('smoke-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $smokeRoot | Out-Null
        $capture = Join-Path $smokeRoot 'capture.png'
        $previousQpa = $env:QT_QPA_PLATFORM
        $previousBackend = $env:QT_QUICK_BACKEND
        try {
            $env:QT_QPA_PLATFORM = 'offscreen'
            $env:QT_QUICK_BACKEND = 'software'
            $smokeArgs = @('--data-dir', ('"' + (Join-Path $smokeRoot 'data') + '"'),
                           '--capture', ('"' + $capture + '"'), '--demo', '--page', '0')
            $process = Start-Process -FilePath $exe -ArgumentList $smokeArgs -PassThru `
                -WorkingDirectory $smokeRoot -WindowStyle Hidden
            if (-not $process.WaitForExit(30000)) {
                $process.Kill()
                throw 'Packaged smoke launch did not exit within 30 seconds.'
            }
            if ($process.ExitCode -ne 0) {
                throw "Packaged smoke launch failed with exit code $($process.ExitCode)."
            }
        }
        finally {
            $env:QT_QPA_PLATFORM = $previousQpa
            $env:QT_QUICK_BACKEND = $previousBackend
        }
        if (-not (Test-Path -LiteralPath $capture -PathType Leaf) -or
            (Get-Item -LiteralPath $capture).Length -lt 1000) {
            throw "Packaged smoke capture missing or empty: $capture"
        }
        Write-Host "Smoke capture: $capture"
    }
    Write-Host "Build ready: $exe"
}
finally {
    $env:PATH = $previousPath
    $env:PYTHONPATH = $previousPythonPath
    $env:QT_PLUGIN_PATH = $previousQtPluginPath
    $env:QML_IMPORT_PATH = $previousQmlImportPath
    Pop-Location
}
