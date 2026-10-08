<#
.SYNOPSIS
  安裝 codex-pet：讓每個 Claude Code 對話都載入這個 mod。
  Installs codex-pet so every Claude Code session loads this mod.

.DESCRIPTION
  1. 檢查 Python 啟動器（py）和 Pillow，缺 Pillow 時詢問要不要用 pip 安裝。
  2. 把這個資料夾加進 ~/.claude/settings.json 的 env.CLAUDE_CODE_PLUGIN_DIRS
     （保留其他設定，修改前先備份成 settings.json.bak-codex-pet）。
  3. 檢查 ~/.codex/pets 裡有沒有寵物。

  在 repo 資料夾裡執行：
    powershell -ExecutionPolicy Bypass -File .\install.ps1
  移除：
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall

.PARAMETER Uninstall
  從 CLAUDE_CODE_PLUGIN_DIRS 移除這個資料夾。 Removes this folder from CLAUDE_CODE_PLUGIN_DIRS.

.PARAMETER SettingsPath
  要修改的 settings.json；預設是 ~/.claude/settings.json。 The settings file to change.
#>
param(
    [switch]$Uninstall,
    [string]$SettingsPath = (Join-Path $env:USERPROFILE '.claude\settings.json')
)

$ErrorActionPreference = 'Stop'
$ModDir = $PSScriptRoot

function Say($zh, $en) { Write-Host "$zh  ($en)" }
function Ok($zh, $en) { Write-Host "[OK] $zh  ($en)" -ForegroundColor Green }
function Warn($zh, $en) { Write-Host "[!]  $zh  ($en)" -ForegroundColor Yellow }
function Fail($zh, $en) { Write-Host "[X]  $zh  ($en)" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $ModDir '.claude-plugin\plugin.json'))) {
    Fail '請在 codex-pet 的 repo 資料夾裡執行這支腳本' 'Run this script from the codex-pet repository folder'
}

# --- Python ---------------------------------------------------------------
# The mod bakes sprites with `py -3` and starts the desktop pet with `pyw -3`.
if (-not (Get-Command py -ErrorAction SilentlyContinue) -or -not (Get-Command pyw -ErrorAction SilentlyContinue)) {
    Fail '找不到 Python 啟動器（py / pyw）。請從 https://www.python.org/downloads/ 安裝 Python 3，安裝時保留「py launcher」選項' `
        'Python launcher (py / pyw) not found. Install Python 3 from python.org with the py launcher option'
}
$pyVersion = (& py -3 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
if ($LASTEXITCODE -ne 0) { Fail '`py -3` 執行失敗，請確認有安裝 Python 3' '`py -3` failed; check that Python 3 is installed' }
Ok "Python $pyVersion" 'Python found'

& py -3 -c "import PIL, tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    & py -3 -c "import tkinter" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail '這個 Python 沒有 tkinter。請用 python.org 的安裝程式重新安裝，勾選「tcl/tk and IDLE」' `
            'This Python has no tkinter; reinstall from python.org with "tcl/tk and IDLE"'
    }
    Warn '缺少 Pillow（桌寵要用它畫圖）' 'Pillow is missing (the pet is drawn with it)'
    $answer = Read-Host '要現在用 pip 安裝 Pillow 嗎？Install Pillow with pip now? [Y/n]'
    if ($answer -match '^(n|no)$') { Fail '請先安裝 Pillow：py -3 -m pip install --user pillow' 'Install Pillow first: py -3 -m pip install --user pillow' }
    & py -3 -m pip install --user pillow
    if ($LASTEXITCODE -ne 0) { Fail 'Pillow 安裝失敗' 'Installing Pillow failed' }
}
Ok 'Pillow 和 tkinter 都有' 'Pillow and tkinter are installed'

# --- settings.json ----------------------------------------------------------
# Python edits the JSON: Windows PowerShell 5.1's ConvertTo-Json reflows the whole file
# and escapes non-ASCII text, which would rewrite the person's other settings.
$settingsDir = Split-Path $SettingsPath
if (-not (Test-Path $settingsDir)) { New-Item -ItemType Directory -Force $settingsDir | Out-Null }
if (Test-Path $SettingsPath) { Copy-Item $SettingsPath "$SettingsPath.bak-codex-pet" -Force }

$edit = @'
import json, os, sys
path, folder, remove = sys.argv[1], os.path.normpath(sys.argv[2]), sys.argv[3] == "1"
try:
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    settings = json.loads(text) if text.strip() else {}
except FileNotFoundError:
    settings = {}
except ValueError as err:
    sys.exit(f"settings.json is not valid JSON, not changed: {err}")
env = settings.setdefault("env", {})
dirs = [d for d in env.get("CLAUDE_CODE_PLUGIN_DIRS", "").split(";") if d.strip()]
others = [d for d in dirs if os.path.normcase(os.path.normpath(d)) != os.path.normcase(folder)]
dirs = others if remove else others + [folder]
if dirs:
    env["CLAUDE_CODE_PLUGIN_DIRS"] = ";".join(dirs)
else:
    env.pop("CLAUDE_CODE_PLUGIN_DIRS", None)
    if not env:
        settings.pop("env")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(settings, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
# The console's code page may lack some characters of a path; the file is already written.
sys.stdout.reconfigure(errors="replace")
print(env.get("CLAUDE_CODE_PLUGIN_DIRS", ""))
'@
$script = Join-Path ([IO.Path]::GetTempPath()) 'codex-pet-settings.py'
Set-Content -Path $script -Value $edit -Encoding UTF8
$result = & py -3 $script $SettingsPath $ModDir ($(if ($Uninstall) { '1' } else { '0' }))
$code = $LASTEXITCODE
Remove-Item $script -ErrorAction SilentlyContinue
if ($code -ne 0) { Fail "沒有修改 $SettingsPath" "Did not change $SettingsPath" }

if ($Uninstall) {
    Ok "已從 CLAUDE_CODE_PLUGIN_DIRS 移除 $ModDir" 'Removed from CLAUDE_CODE_PLUGIN_DIRS'
    Say '開新的 Claude Code 對話後就不會再載入；桌寵在右鍵選單「關閉桌寵」' 'New sessions no longer load it; close the pet from its right-click menu'
    exit 0
}
Ok "CLAUDE_CODE_PLUGIN_DIRS = $result" 'Claude Code will load the mod'
if (Test-Path "$SettingsPath.bak-codex-pet") { Say "原本的設定備份在 $SettingsPath.bak-codex-pet" 'Your previous settings were backed up' }

# --- pets ---------------------------------------------------------------------
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$pets = @(Get-ChildItem (Join-Path $codexHome 'pets') -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path (Join-Path $_.FullName 'pet.json') })
if ($pets.Count -eq 0) {
    Warn "在 $codexHome\pets 找不到寵物" 'No pets found'
    Say '可以在 Codex 裡建立寵物，或從 https://codex-pets.net/ 安裝，例如：npx codex-pets add nino' `
        'Create one in Codex, or install one from codex-pets.net, e.g. npx codex-pets add nino'
} else {
    Ok ("找到 {0} 隻寵物：{1}" -f $pets.Count, (($pets | ForEach-Object Name) -join ', ')) 'Pets found'
}

Write-Host ''
Say '完成！開一個新的 Claude Code 對話（桌面版的 Code 分頁或終端機都可以），桌寵就會出現。' `
    'Done. Start a new Claude Code session (desktop Code tab or terminal) and the pet appears.'
