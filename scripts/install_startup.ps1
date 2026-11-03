$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "Echo virtual environment not found. Run the README setup steps first."
}
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Echo.lnk"
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m echo"
$shortcut.WorkingDirectory = $root
$shortcut.Description = "Start Echo voice assistant"
$shortcut.Save()
Write-Host "Echo will start when you sign in. Shortcut: $shortcutPath"
