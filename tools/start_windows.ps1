# Explorer launches the shortcut in the user's desktop context. Direct children
# of an MSIX development host can inherit its redirected AppData view.
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$shortcut = Join-Path $projectRoot 'Модельная студия.lnk'
$executable = Join-Path $projectRoot 'dist\ModelStudio\ModelStudio.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw 'Build the application before launching it.'
}
if (-not (Test-Path -LiteralPath $shortcut -PathType Leaf)) {
    & (Join-Path $PSScriptRoot 'create_shortcuts.ps1')
}
$shell = New-Object -ComObject WScript.Shell
$target = $shell.CreateShortcut($shortcut).TargetPath
if (-not [string]::Equals($target, $executable, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The application shortcut targets a different executable.'
}
Start-Process -FilePath explorer.exe -ArgumentList ('"' + $shortcut + '"') -WindowStyle Hidden
