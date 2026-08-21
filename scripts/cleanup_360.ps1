param(
    [Parameter(Mandatory=$false)]
    [string]$OutputDir = ".\cleanup-360",
    [switch]$Execute
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $OutputDir $timestamp
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$logPath = Join-Path $runDir 'actions.jsonl'
$inventoryPath = Join-Path $runDir 'inventory.json'
$summaryPath = Join-Path $runDir 'summary.json'

function Log([string]$Event, $Data=$null) {
    $row = [ordered]@{ time=(Get-Date).ToString('o'); event=$Event; data=$Data }
    ($row | ConvertTo-Json -Depth 8 -Compress) | Add-Content -LiteralPath $logPath -Encoding UTF8
}

function Is-Admin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $p = New-Object Security.Principal.WindowsPrincipal($id)
        return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Get-ExeFromCommandLine([string]$cmd) {
    if ([string]::IsNullOrWhiteSpace($cmd)) { return $null }
    $s = $cmd.Trim()
    if ($s.StartsWith('"')) {
        $end = $s.IndexOf('"', 1)
        if ($end -gt 1) { return $s.Substring(1, $end - 1) }
    }
    $m = [regex]::Match($s, '^[^ ]+?\.exe', 'IgnoreCase')
    if ($m.Success) { return $m.Value }
    return ($s -split '\s+')[0]
}

function Is-360Text([string]$text) {
    if (-not $text) { return $false }
    return $text -match '(?i)(360安全|360浏览器|360游戏|360看图|360壁纸|360画报|360huabao|360Safe|360Game5|360SoftMgr|secoresdk\\360se6|Qihoo|Qihu|Beijing Qihu Technology|360安全中心|www\.360\.cn|\\360\\)'
}

function Get-Installed360Apps {
    $paths = @(
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    $items = foreach ($p in $paths) {
        Get-ItemProperty $p -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName } | ForEach-Object {
            $evidence = ($_.DisplayName + ' ' + $_.Publisher + ' ' + $_.InstallLocation + ' ' + $_.UninstallString)
            if (Is-360Text $evidence) {
                [pscustomobject]@{
                    DisplayName=$_.DisplayName
                    DisplayVersion=$_.DisplayVersion
                    Publisher=$_.Publisher
                    InstallLocation=$_.InstallLocation
                    UninstallString=$_.UninstallString
                    QuietUninstallString=$_.QuietUninstallString
                    RegistryPath=$_.PSPath
                }
            }
        }
    }
    return @($items | Sort-Object DisplayName -Unique)
}

function Get-360Processes {
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $path = $_.ExecutablePath
        $company = $null
        if ($path -and (Test-Path -LiteralPath $path)) {
            try { $company = (Get-Item -LiteralPath $path).VersionInfo.CompanyName } catch {}
        }
        if (Is-360Text ($_.Name + ' ' + $path + ' ' + $company)) {
            [pscustomobject]@{ Name=$_.Name; ProcessId=$_.ProcessId; ExecutablePath=$path; CompanyName=$company }
        }
    })
}

function Get-360Services {
    return @(Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | ForEach-Object {
        $exe = Get-ExeFromCommandLine $_.PathName
        $company = $null
        if ($exe -and (Test-Path -LiteralPath $exe)) {
            try { $company = (Get-Item -LiteralPath $exe).VersionInfo.CompanyName } catch {}
        }
        if (Is-360Text ($_.Name + ' ' + $_.DisplayName + ' ' + $_.PathName + ' ' + $company)) {
            [pscustomobject]@{
                Name=$_.Name; DisplayName=$_.DisplayName; State=$_.State; StartMode=$_.StartMode;
                PathName=$_.PathName; ExecutablePath=$exe; CompanyName=$company
            }
        }
    })
}

function Get-360Tasks {
    $out = @()
    try {
        Get-ScheduledTask | ForEach-Object {
            $actionsText = ($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' '
            if (Is-360Text ($_.TaskName + ' ' + $_.TaskPath + ' ' + $actionsText)) {
                $out += [pscustomobject]@{ TaskName=$_.TaskName; TaskPath=$_.TaskPath; Actions=$actionsText }
            }
        }
    } catch {}
    return $out
}

function Get-360RunItems {
    $keys = @(
        @{Path='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'; Scope='HKCU'},
        @{Path='HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'; Scope='HKCU'},
        @{Path='HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'; Scope='HKLM'},
        @{Path='HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'; Scope='HKLM32'}
    )
    $out = @()
    foreach ($k in $keys) {
        try {
            $obj = Get-ItemProperty -Path $k.Path -ErrorAction Stop
            foreach ($p in $obj.PSObject.Properties) {
                if ($p.Name -match '^PS(Path|ParentPath|ChildName|Drive|Provider)$') { continue }
                if (Is-360Text ($p.Name + ' ' + [string]$p.Value)) {
                    $out += [pscustomobject]@{ Scope=$k.Scope; Key=$k.Path; Name=$p.Name; Value=[string]$p.Value }
                }
            }
        } catch {}
    }
    return $out
}

function Get-360Shortcuts {
    $roots = @(
        [Environment]::GetFolderPath('Desktop'),
        [Environment]::GetFolderPath('CommonDesktopDirectory'),
        [Environment]::GetFolderPath('StartMenu'),
        [Environment]::GetFolderPath('CommonStartMenu')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $out = @()
    try { $ws = New-Object -ComObject WScript.Shell } catch { return @() }
    foreach ($root in $roots) {
        Get-ChildItem -LiteralPath $root -Filter *.lnk -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $sc = $ws.CreateShortcut($_.FullName)
                if (Is-360Text ($_.FullName + ' ' + $sc.TargetPath + ' ' + $sc.Arguments)) {
                    $out += [pscustomobject]@{ Path=$_.FullName; TargetPath=$sc.TargetPath; Arguments=$sc.Arguments }
                }
            } catch {}
        }
    }
    return $out
}

function Get-UrlAssoc([string]$scheme) {
    $key = "HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\$scheme\UserChoice"
    try {
        $v = Get-ItemProperty $key -ErrorAction Stop
        return [pscustomobject]@{ Scheme=$scheme; ProgId=$v.ProgId; Hash=$v.Hash }
    } catch { return [pscustomobject]@{ Scheme=$scheme; ProgId=$null; Hash=$null } }
}

function Get-ResidualDirs {
    $candidates = @(
        "$env:ProgramFiles\360",
        "${env:ProgramFiles(x86)}\360",
        "$env:APPDATA\360Safe",
        "$env:APPDATA\360Game5",
        "$env:APPDATA\360huabao",
        "$env:APPDATA\360SoftMgr",
        "$env:APPDATA\secoresdk\360se6",
        "$env:LOCALAPPDATA\360Safe",
        "$env:LOCALAPPDATA\360Browser",
        "$env:LOCALAPPDATA\360Chrome",
        "$env:ProgramData\360safe",
        "$env:ProgramData\360SD"
    )
    return @($candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Sort-Object -Unique)
}

function Collect-Inventory {
    return [ordered]@{
        Time=(Get-Date).ToString('o')
        IsAdmin=(Is-Admin)
        Apps=(Get-Installed360Apps)
        Processes=(Get-360Processes)
        Services=(Get-360Services)
        Tasks=(Get-360Tasks)
        RunItems=(Get-360RunItems)
        Shortcuts=(Get-360Shortcuts)
        HTTP=(Get-UrlAssoc 'http')
        HTTPS=(Get-UrlAssoc 'https')
        ResidualDirs=(Get-ResidualDirs)
    }
}

function Backup-State($inventory) {
    $backup = Join-Path $runDir 'backup'
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    $inventory | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $backup 'inventory-before.json') -Encoding UTF8
    $exports = @(
        @{Key='HKCU\Software\Microsoft\Windows\CurrentVersion\Run'; Name='hkcu-run.reg'},
        @{Key='HKLM\Software\Microsoft\Windows\CurrentVersion\Run'; Name='hklm-run.reg'},
        @{Key='HKLM\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'; Name='hklm32-run.reg'}
    )
    foreach ($e in $exports) {
        & reg.exe export $e.Key (Join-Path $backup $e.Name) /y *> $null
    }
    Log 'backup_complete' $backup
}

function Remove-RunItems($items) {
    foreach ($item in $items) {
        try {
            Remove-ItemProperty -Path $item.Key -Name $item.Name -Force -ErrorAction Stop
            Log 'run_removed' $item
        } catch { Log 'run_remove_failed' @{item=$item; error=$_.Exception.Message} }
    }
}

function Stop-360Processes($items) {
    foreach ($p in $items) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Log 'process_stopped' $p
        } catch { Log 'process_stop_failed' @{process=$p; error=$_.Exception.Message} }
    }
}

function Remove-360Tasks($items) {
    foreach ($t in $items) {
        try {
            Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false -ErrorAction Stop
            Log 'task_removed' $t
        } catch { Log 'task_remove_failed' @{task=$t; error=$_.Exception.Message} }
    }
}

function Remove-360Services($items) {
    foreach ($s in $items) {
        try { Stop-Service -Name $s.Name -Force -ErrorAction SilentlyContinue } catch {}
        try {
            & sc.exe delete $s.Name | Out-Null
            Log 'service_delete_requested' $s
        } catch { Log 'service_delete_failed' @{service=$s; error=$_.Exception.Message} }
    }
}

function Split-Command([string]$cmd) {
    if ([string]::IsNullOrWhiteSpace($cmd)) { return $null }
    $s = $cmd.Trim()
    if ($s.StartsWith('"')) {
        $end = $s.IndexOf('"', 1)
        if ($end -lt 1) { return $null }
        return [pscustomobject]@{ File=$s.Substring(1,$end-1); Args=$s.Substring($end+1).Trim() }
    }
    $space = $s.IndexOf(' ')
    if ($space -lt 0) { return [pscustomobject]@{ File=$s; Args='' } }
    return [pscustomobject]@{ File=$s.Substring(0,$space); Args=$s.Substring($space+1) }
}

function Invoke-AppUninstall($app) {
    $cmd = if ($app.QuietUninstallString) { $app.QuietUninstallString } else { $app.UninstallString }
    if (-not $cmd) { Log 'uninstall_missing' $app; return }
    $parts = Split-Command $cmd
    if (-not $parts) { Log 'uninstall_parse_failed' @{app=$app; cmd=$cmd}; return }
    $file = $parts.File
    $args = $parts.Args
    if ($file -match '(?i)msiexec(\.exe)?$') {
        $args = [regex]::Replace($args, '(?i)(^|\s)/I(?=\s|\{)', '$1/X')
        if ($args -notmatch '(?i)/(quiet|qn|passive)') { $args += ' /passive /norestart' }
    }
    try {
        Log 'uninstall_start' @{app=$app.DisplayName; file=$file; args=$args}
        $proc = Start-Process -FilePath $file -ArgumentList $args -Wait -PassThru -ErrorAction Stop
        Log 'uninstall_end' @{app=$app.DisplayName; exitCode=$proc.ExitCode}
    } catch { Log 'uninstall_failed' @{app=$app.DisplayName; error=$_.Exception.Message; command=$cmd} }
}

function App-Rank([string]$name) {
    if ($name -match '游戏') { return 10 }
    if ($name -match '看图|壁纸|画报') { return 20 }
    if ($name -match '浏览器') { return 30 }
    if ($name -match '软件管家') { return 40 }
    if ($name -match '安全卫士') { return 90 }
    return 50
}

function Remove-Shortcuts($items) {
    foreach ($s in $items) {
        try {
            Remove-Item -LiteralPath $s.Path -Force -ErrorAction Stop
            Log 'shortcut_removed' $s
        } catch { Log 'shortcut_remove_failed' @{shortcut=$s; error=$_.Exception.Message} }
    }
}

function Remove-360UrlHandlers {
    foreach ($path in @('HKCU:\Software\Classes\360seURL','HKLM:\Software\Classes\360seURL')) {
        if (Test-Path $path) {
            try { Remove-Item -Path $path -Recurse -Force -ErrorAction Stop; Log 'url_handler_removed' $path }
            catch { Log 'url_handler_remove_failed' @{path=$path; error=$_.Exception.Message} }
        }
    }
}

function HardDelete-Residual([string]$path) {
    $fast = Join-Path $PSScriptRoot 'fast_delete.py'
    if (-not (Test-Path -LiteralPath $fast)) { Log 'fast_delete_missing' $fast; return }
    $outLog = Join-Path $runDir ("fast-delete-" + ([IO.Path]::GetFileName($path).Replace(' ','_')) + '.jsonl')
    $args = @($fast,'delete',$path,'--execute','--vendor-scope','360','--kill-processes','--take-ownership','--schedule-reboot','--log',$outLog)
    try {
        & python.exe @args
        $code = $LASTEXITCODE
        Log 'residual_hard_delete' @{path=$path; exitCode=$code; log=$outLog}
    } catch { Log 'residual_hard_delete_failed' @{path=$path; error=$_.Exception.Message} }
}

$before = Collect-Inventory
$before | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath $inventoryPath -Encoding UTF8
Log 'inventory_complete' @{apps=$before.Apps.Count; processes=$before.Processes.Count; services=$before.Services.Count; tasks=$before.Tasks.Count; run=$before.RunItems.Count; dirs=$before.ResidualDirs.Count}

if (-not $Execute) {
    $summary = [ordered]@{
        Mode='DRY_RUN'
        RunDirectory=$runDir
        Inventory=$inventoryPath
        Message='No changes made. Re-run with -Execute only after user authorization.'
    }
    $summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Output ($summary | ConvertTo-Json -Depth 4)
    exit 0
}

if (-not (Is-Admin)) {
    Log 'blocked_not_admin'
    Write-Error 'Stage 360 Execute requires an elevated Administrator PowerShell session.'
    exit 5
}

Backup-State $before

# First remove ad/wallpaper/autostart experience, then uninstall applications.
Remove-RunItems $before.RunItems
$adProcesses = @($before.Processes | Where-Object { $_.Name -match '(?i)(360huabao|huabao|wallpaper|screen|game|sesvc|360se)' })
Stop-360Processes $adProcesses
Remove-360Tasks $before.Tasks

$orderedApps = @($before.Apps | Sort-Object @{Expression={ App-Rank $_.DisplayName }})
foreach ($app in $orderedApps) { Invoke-AppUninstall $app }

Start-Sleep -Seconds 3
$mid = Collect-Inventory
Stop-360Processes $mid.Processes
Remove-360Services $mid.Services
Remove-360Tasks $mid.Tasks
Remove-RunItems $mid.RunItems
Remove-Shortcuts $mid.Shortcuts
Remove-360UrlHandlers

foreach ($dir in $mid.ResidualDirs) { HardDelete-Residual $dir }

$after = Collect-Inventory
$afterPath = Join-Path $runDir 'inventory-after.json'
$after | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath $afterPath -Encoding UTF8

$needsDefaultBrowserRepair = ($after.HTTP.ProgId -match '(?i)360') -or ($after.HTTPS.ProgId -match '(?i)360')
if ($needsDefaultBrowserRepair) {
    Log 'default_browser_user_action_required' @{HTTP=$after.HTTP.ProgId; HTTPS=$after.HTTPS.ProgId}
    try { Start-Process 'ms-settings:defaultapps' } catch {}
}

$summary = [ordered]@{
    Mode='EXECUTE'
    RunDirectory=$runDir
    Before=[ordered]@{ Apps=$before.Apps.Count; Processes=$before.Processes.Count; Services=$before.Services.Count; Tasks=$before.Tasks.Count; RunItems=$before.RunItems.Count; ResidualDirs=$before.ResidualDirs.Count }
    After=[ordered]@{ Apps=$after.Apps.Count; Processes=$after.Processes.Count; Services=$after.Services.Count; Tasks=$after.Tasks.Count; RunItems=$after.RunItems.Count; ResidualDirs=$after.ResidualDirs.Count }
    HTTP=$after.HTTP
    HTTPS=$after.HTTPS
    DefaultBrowserUserActionRequired=$needsDefaultBrowserRepair
    RebootRecommended=(($after.Processes.Count + $after.Services.Count + $after.ResidualDirs.Count) -gt 0)
    ProtectedEngineeringSoftware='Not targeted by this vendor-scoped script.'
}
$summary | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
Write-Output ($summary | ConvertTo-Json -Depth 7)
