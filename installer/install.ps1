# Installer for the "AI Usage" tray icon (Windows 10/11).
# Legacy names are kept on purpose (%LOCALAPPDATA%\claude-usage, claude-usage.py, claude-usage.conf,
# the Startup shortcut "Claude Code usage.lnk"): installs from before the rename self-update in
# place, and a new shortcut name would start a second tray icon.
# Double-click install.cmd next to this file, or run:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 [-Lang vi|en]
# Language: -Lang, else the CLAUDE_USAGE_LANG environment variable, else a prompt.
# build.sh replaces the plugin marker near the end of this file with the base64 of
# plugin/claude-usage.1m.py, so no content of the plugin can break the PowerShell syntax.
param([string]$Lang = '')

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$PythonVersion = '3.12.10'  # last 3.12 release with Windows installers on python.org
$InstallDir = Join-Path $env:LOCALAPPDATA 'claude-usage'
$PluginPath = Join-Path $InstallDir 'claude-usage.py'
$ConfPath = Join-Path (Join-Path $env:USERPROFILE '.config') 'claude-usage.conf'
$LogPath = Join-Path (Join-Path (Join-Path $env:USERPROFILE '.cache') 'claude-usage') 'last-run.log'
$ShortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Code usage.lnk'  # legacy file name, kept on purpose

# ---------- messages (one table per language) ----------

$Messages = @{
    vi = @{
        title         = 'Cài đặt "AI Usage" (biểu tượng ở khay hệ thống)'
        step1         = '1/4  Kiểm tra Python (cần bản 3.9 trở lên)'
        py_found      = 'OK: Python {0} ({1})'
        py_missing    = 'Máy chưa có Python 3.9+. Đang cài Python 3.12 cho riêng tài khoản này (không cần quyền admin)...'
        py_winget     = 'Đang cài bằng winget, chờ khoảng 1-2 phút...'
        py_no_winget  = 'Không cài được bằng winget, tải bộ cài từ python.org...'
        py_download   = 'Đang tải {0}'
        py_installer  = 'Đang chạy bộ cài Python (chạy ngầm, chờ khoảng 1-2 phút)...'
        py_failed     = 'Không cài được Python. Cài tay từ https://www.python.org/downloads/ (tích "Add python.exe to PATH") rồi chạy lại file này.'
        pyw_missing   = 'Không tìm thấy pythonw.exe cạnh {0}. Cài lại Python từ python.org rồi chạy lại file này.'
        step2         = '2/4  Cài plugin AI Usage'
        installed     = 'Đã cài: {0}'
        lang_saved    = 'Đã chọn tiếng Anh (dòng "language = en" trong {0}).'
        test_run      = 'Đang chạy thử...'
        test_result   = 'Di chuột lên biểu tượng sẽ thấy: {0}'
        test_none     = 'Chưa tìm thấy tài khoản Claude Code, Codex hay Antigravity nào. Xem mục "Nếu thiếu tài khoản" bên dưới.'
        test_failed   = 'Chạy thử bị lỗi (mã {0}): {1}'
        step3         = '3/4  Cho tự chạy khi đăng nhập Windows và mở biểu tượng'
        stopped       = 'Đã tắt bản cũ đang chạy.'
        shortcut      = 'Đã tạo lối tắt tự chạy: {0}'
        started       = 'Đã mở biểu tượng ở khay hệ thống (góc phải thanh taskbar, cạnh đồng hồ).'
        step4         = '4/4  Cách dùng'
        guide         = @(
            'Biểu tượng hiện số % cao nhất trong các giới hạn, nền cam hoặc đỏ khi sắp chạm giới hạn.',
            'Di chuột lên biểu tượng để thấy dạng "C 13/8/1  Cx 78/73  Ag 0/0": mỗi tài khoản Claude là một chữ cái',
            '(% giới hạn 5 giờ / tuần / Fable tuần), Cx là Codex (5 giờ / tuần), Ag là Antigravity (tuần, theo nhóm model).',
            'Bấm vào biểu tượng để mở menu:',
            'tên tài khoản, còn bao lâu nữa thì reset, "Làm mới ngay", và "Cập nhật ngay" khi có bản mới.',
            '',
            'Không thấy biểu tượng? Windows hay giấu biểu tượng mới vào khu vực tràn (mũi tên ^ cạnh đồng hồ).',
            'Kéo nó ra thanh taskbar để luôn hiện. Hoặc: Settings > Personalization > Taskbar >',
            '"Other system tray icons" (Windows 11) hoặc "Select which icons appear on the taskbar" (Windows 10),',
            'rồi bật mục Python.',
            '',
            'Nếu hiện "?", bấm vào để xem dòng "Lỗi: ...", hoặc xem file {0}',
            '',
            'Nếu thiếu tài khoản:',
            '- Claude Code, đăng nhập trình duyệt: chạy `claude` một lần để đăng nhập.',
            '- Codex: cài codex CLI rồi chạy `codex login` (đăng nhập ChatGPT). Antigravity: cài agy CLI rồi đăng nhập.',
            '  Hai loại này đọc hạn mức không tốn quota.',
            '- Token (claude setup-token): plugin tự tìm CLAUDE_CODE_OAUTH_TOKEN trong biến môi trường và',
            '  PowerShell profile. Nếu token để chỗ khác, thêm một dòng "tên = C:\đường\dẫn\file-token"',
            '  vào {1} rồi bấm "Làm mới ngay" trong menu.',
            '- Muốn ẩn tài khoản đăng nhập trình duyệt: thêm dòng "keychain = off" vào file trên.',
            '',
            'Gỡ cài đặt: bấm "Thoát" trong menu, rồi xoá lối tắt {2}',
            'và thư mục {3}'
        )
        done          = 'Xong.'
        error         = 'Lỗi: {0}'
    }
    en = @{
        title         = 'Installing "AI Usage" (notification area icon)'
        step1         = '1/4  Checking Python (3.9 or newer)'
        py_found      = 'OK: Python {0} ({1})'
        py_missing    = 'Python 3.9+ was not found. Installing Python 3.12 for this account only (no admin rights needed)...'
        py_winget     = 'Installing with winget, this takes 1-2 minutes...'
        py_no_winget  = 'winget could not install it, downloading the installer from python.org...'
        py_download   = 'Downloading {0}'
        py_installer  = 'Running the Python installer (silently, 1-2 minutes)...'
        py_failed     = 'Could not install Python. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this file again.'
        pyw_missing   = 'pythonw.exe was not found next to {0}. Reinstall Python from python.org, then run this file again.'
        step2         = '2/4  Installing the AI Usage plugin'
        installed     = 'Installed: {0}'
        lang_saved    = 'English selected (line "language = en" in {0}).'
        test_run      = 'Test run...'
        test_result   = 'Hovering the icon will show: {0}'
        test_none     = 'No Claude Code, Codex or Antigravity account found yet. See "If an account is missing" below.'
        test_failed   = 'The test run failed (exit code {0}): {1}'
        step3         = '3/4  Starting with Windows and showing the icon'
        stopped       = 'Stopped the copy that was already running.'
        shortcut      = 'Created the startup shortcut: {0}'
        started       = 'The icon is now in the notification area (right end of the taskbar, next to the clock).'
        step4         = '4/4  How to use it'
        guide         = @(
            'The icon shows the highest % among your limits, on orange or red when a limit is close.',
            'Hover it to see something like "C 13/8/1  Cx 78/73  Ag 0/0": a letter per Claude account (% of the',
            '5-hour / weekly / Fable weekly limit), Cx for Codex (5-hour / weekly), Ag for Antigravity (weekly',
            'per model group). Click the icon for the',
            'menu: account names, time until each reset, "Refresh now", and "Update now" when a new version is out.',
            '',
            'No icon? Windows often hides new icons in the overflow area (the ^ arrow next to the clock).',
            'Drag it onto the taskbar to keep it visible. Or: Settings > Personalization > Taskbar >',
            '"Other system tray icons" (Windows 11) or "Select which icons appear on the taskbar" (Windows 10),',
            'then turn on Python.',
            '',
            'If it shows "?", click it and read the "Error: ..." line, or open {0}',
            '',
            'If an account is missing:',
            '- Claude Code browser login: run `claude` once and log in.',
            '- Codex: install the codex CLI and run `codex login` (ChatGPT login). Antigravity: install the agy CLI',
            '  and log in. Their checks cost no quota.',
            '- Token (claude setup-token): the plugin looks for CLAUDE_CODE_OAUTH_TOKEN in environment',
            '  variables and PowerShell profiles. If the token lives elsewhere, add a line',
            '  "name = C:\path\to\token-file" to {1}, then click "Refresh now" in the menu.',
            '- To hide browser logins: add the line "keychain = off" to that file.',
            '',
            'Uninstall: click "Quit" in the menu, then delete the shortcut {2}',
            'and the folder {3}'
        )
        done          = 'Done.'
        error         = 'Error: {0}'
    }
}
$UiLang = 'vi'

function M {
    # The message for $Key in the chosen language; extra arguments fill {0}, {1}...
    param([string]$Key)
    $text = $Messages[$script:UiLang][$Key]
    $values = $args  # inside ForEach-Object, $args would be the script block's own
    if ($values.Count -eq 0) { return $text }
    return @($text | ForEach-Object { $_ -f $values })
}

function Say([string]$Text) { Write-Host ''; Write-Host $Text -ForegroundColor White }
function Info([string]$Text) { Write-Host "  $Text" }

# ---------- language ----------

function Get-LanguageCode([string]$Value) {
    $v = "$Value".Trim().ToLowerInvariant()
    if ($v.StartsWith('en')) { return 'en' }
    if ($v.StartsWith('vi') -or $v.StartsWith('ti')) { return 'vi' }
    return ''
}

function Select-Language {
    $code = Get-LanguageCode $Lang
    if (-not $code) { $code = Get-LanguageCode $env:CLAUDE_USAGE_LANG }
    if ($code) { return $code }
    Write-Host 'Chọn ngôn ngữ / Choose language: [1] Tiếng Việt (mặc định) [2] English'
    $answer = Read-Host '>'
    if ("$answer".Trim() -eq '2' -or (Get-LanguageCode $answer) -eq 'en') { return 'en' }
    return 'vi'
}

function Set-ConfLanguage([string]$Code) {
    # Write "language = <code>" into the conf file, replacing an existing language line and
    # keeping every other line.
    $dir = Split-Path -Parent $ConfPath
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $ConfPath) {
        $lines.AddRange([string[]][IO.File]::ReadAllLines($ConfPath))
    }
    $wanted = "language = $Code"
    $replaced = $false
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        if ($lines[$i] -match '^\s*language\s*=') {
            if ($replaced) { $lines.RemoveAt($i) } else { $lines[$i] = $wanted; $replaced = $true }
        }
    }
    if (-not $replaced) { $lines.Add($wanted) }
    $text = ($lines -join "`n") + "`n"
    [IO.File]::WriteAllText($ConfPath, $text, (New-Object System.Text.UTF8Encoding $false))
}

# ---------- Python ----------

function Test-InWindowsApps([string]$Path) {
    # The Microsoft Store "python" there is a stub that opens the Store: never run it.
    return "$Path".ToLowerInvariant().Contains('\microsoft\windowsapps\')
}

function Get-PythonInfo([string]$Exe, [string[]]$PrefixArgs) {
    # @{Exe = sys.executable; Version = '3.x.y'} when $Exe runs a Python >= 3.9, else $null.
    if (-not $Exe -or (Test-InWindowsApps $Exe)) { return $null }
    $code = 'import sys; print(sys.executable); print(sys.version.split()[0]); print(int(sys.version_info >= (3, 9)))'
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = @(& $Exe @PrefixArgs -c $code 2>$null)
        if ($LASTEXITCODE -ne 0 -or $out.Count -lt 3 -or "$($out[2])".Trim() -ne '1') { return $null }
        $exe = "$($out[0])".Trim()
        if (-not (Test-Path -LiteralPath $exe) -or (Test-InWindowsApps $exe)) { return $null }
        return @{ Exe = $exe; Version = "$($out[1])".Trim() }
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $saved
    }
}

function Update-SessionPath {
    # Installers change PATH in the registry; this session still has the old value.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Find-Python {
    $candidates = @()
    foreach ($name in @('py', 'python')) {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { -not (Test-InWindowsApps $_.Source) } | Select-Object -First 1
        if ($cmd) {
            $prefix = @()
            if ($name -eq 'py') { $prefix = @('-3') }
            $candidates += , @($cmd.Source, $prefix)
        }
    }
    $roots = @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles)
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        Get-ChildItem -LiteralPath $root -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path -LiteralPath $exe) { $candidates += , @($exe, @()) }
            }
    }
    foreach ($c in $candidates) {
        $info = Get-PythonInfo $c[0] $c[1]
        if ($info) { return $info }
    }
    return $null
}

function Install-PythonWithWinget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return }
    Info (M 'py_winget')
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Host
    } catch {
    } finally {
        $ErrorActionPreference = $saved
    }
}

function Install-PythonFromPythonOrg {
    Info (M 'py_no_winget')
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    $suffix = '-amd64'
    if ($arch -eq 'ARM64') { $suffix = '-arm64' } elseif ($arch -eq 'x86') { $suffix = '' }
    $file = "python-$PythonVersion$suffix.exe"
    $url = "https://www.python.org/ftp/python/$PythonVersion/$file"
    $setup = Join-Path $env:TEMP $file
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Info (M 'py_download' $url)
    Invoke-WebRequest -Uri $url -OutFile $setup -UseBasicParsing
    Info (M 'py_installer')
    $proc = Start-Process -FilePath $setup -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1' -Wait -PassThru
    Remove-Item -LiteralPath $setup -Force -ErrorAction SilentlyContinue
    if ($proc.ExitCode -ne 0) { throw (M 'py_failed') }
}

function Get-Python {
    $info = Find-Python
    if ($info) { return $info }
    Info (M 'py_missing')
    Install-PythonWithWinget
    Update-SessionPath
    $info = Find-Python
    if ($info) { return $info }
    Install-PythonFromPythonOrg
    Update-SessionPath
    $info = Find-Python
    if (-not $info) { throw (M 'py_failed') }
    return $info
}

# ---------- plugin and tray ----------

function Write-Plugin([string]$Base64) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    # Raw bytes: UTF-8 without BOM, LF line endings kept.
    [IO.File]::WriteAllBytes($PluginPath, [Convert]::FromBase64String($Base64))
}

function Test-Plugin([string]$Python) {
    # Print what the tooltip will show, from one normal (SwiftBar-format) run.
    Info (M 'test_run')
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $env:PYTHONIOENCODING = 'utf-8'
    try {
        $out = @(& $Python $PluginPath 2>&1 | ForEach-Object { "$_" })
        if ($LASTEXITCODE -ne 0) {
            Info (M 'test_failed' $LASTEXITCODE ($out | Select-Object -Last 1))
            return
        }
        $first = ''
        if ($out.Count -gt 0) { $first = ($out[0] -split ' \|')[0] }
        Info (M 'test_result' $first)
        if ($first -eq 'AI ?') { Info (M 'test_none') }
    } catch {
        Info (M 'test_failed' '?' $_.Exception.Message)
    } finally {
        $ErrorActionPreference = $saved
        Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
    }
}

function Stop-RunningTray {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue)
    $stopped = $false
    foreach ($p in $procs) {
        $line = $p.CommandLine
        if ($line -and $line.IndexOf($PluginPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and $p.ProcessId -ne $PID) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
    }
    if ($stopped) {
        Start-Sleep -Seconds 1
        Info (M 'stopped')
    }
}

function New-StartupShortcut([string]$Pythonw) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($ShortcutPath)
    $link.TargetPath = $Pythonw
    $link.Arguments = '"' + $PluginPath + '" --tray'
    $link.WorkingDirectory = $InstallDir
    $link.Description = 'AI Usage'
    $link.Save()
    Info (M 'shortcut' $ShortcutPath)
}

function Start-Tray([string]$Pythonw) {
    Start-Process -FilePath $Pythonw -ArgumentList ('"' + $PluginPath + '" --tray') -WorkingDirectory $InstallDir
    Info (M 'started')
}

# ---------- main ----------

function Install-ClaudeUsage([string]$Base64) {
    $script:UiLang = Select-Language
    Say (M 'title')

    Say (M 'step1')
    $python = Get-Python
    Info (M 'py_found' $python.Version $python.Exe)
    $pythonw = Join-Path (Split-Path -Parent $python.Exe) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { throw (M 'pyw_missing' $python.Exe) }

    Say (M 'step2')
    Write-Plugin $Base64
    Info (M 'installed' $PluginPath)
    if ($script:UiLang -eq 'en') {
        Set-ConfLanguage 'en'
        Info (M 'lang_saved' $ConfPath)
    } elseif ((Test-Path -LiteralPath $ConfPath) -and
              (Select-String -LiteralPath $ConfPath -Pattern '^\s*language\s*=' -Quiet)) {
        Set-ConfLanguage 'vi'  # an earlier English install must not win over this choice
    }
    # Stop the old tray first so it is not writing the cache while the test run does.
    Stop-RunningTray
    Test-Plugin $python.Exe

    Say (M 'step3')
    New-StartupShortcut $pythonw
    Start-Tray $pythonw

    Say (M 'step4')
    foreach ($line in (M 'guide' $LogPath $ConfPath $ShortcutPath $InstallDir)) { Info $line }
    Say (M 'done')
}

$PluginBase64 = '__PLUGIN_B64__'

try {
    Install-ClaudeUsage $PluginBase64
} catch {
    Write-Host ''
    Write-Host (M 'error' $_.Exception.Message) -ForegroundColor Red
    exit 1
}
