"""Windows 原生文件 / 目录选择框。

用 PowerShell 调 System.Windows.Forms，避免打包 tkinter。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_OWNER = """
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'Huzhmi'
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'None'
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0.01
$owner.Show()
"""

_FILTERS = {
    "ffmpeg": "Executable (*.exe)|*.exe|All files (*.*)|*.*",
    "ncm": "NCM (*.ncm)|*.ncm|All files (*.*)|*.*",
    "audio": ("Audio (*.mp3;*.wav;*.flac;*.m4a;*.aac;*.ogg;*.opus;*.wma;*.ape;*.aiff)"
              "|*.mp3;*.wav;*.flac;*.m4a;*.aac;*.ogg;*.opus;*.wma;*.ape;*.aiff"
              "|All files (*.*)|*.*"),
    "any": "All files (*.*)|*.*",
}


def _run(script: str) -> str:
    if sys.platform != "win32":
        return ""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def pick_folder(initial: str = "") -> str:
    safe = str(initial or "").replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
{_OWNER}
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择目录'
$dialog.SelectedPath = '{safe}'
$dialog.ShowNewFolderButton = $true
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}}
$owner.Close()
"""
    return _run(script)


def pick_file(initial: str = "", kind: str = "any") -> str:
    source = Path(str(initial or "")).expanduser()
    initial_dir = ""
    file_name = ""
    if source.is_file():
        initial_dir = str(source.parent)
        file_name = source.name
    elif source.is_dir():
        initial_dir = str(source)
    safe_dir = initial_dir.replace("'", "''")
    safe_name = file_name.replace("'", "''")
    filter_text = _FILTERS.get(kind, _FILTERS["any"]).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
{_OWNER}
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '选择文件'
$dialog.Filter = '{filter_text}'
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
$dialog.InitialDirectory = '{safe_dir}'
$dialog.FileName = '{safe_name}'
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.FileName
}}
$owner.Close()
"""
    return _run(script)


__all__ = ["pick_file", "pick_folder"]
