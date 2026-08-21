param(
    [Parameter(Mandatory=$false)]
    [string]$OutputPath = ".\deep-audit.json"
)

$ErrorActionPreference = "SilentlyContinue"
$ProgressPreference = "SilentlyContinue"

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

function Get-SigInfo([string]$path) {
    if (-not $path -or -not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $sig = Get-AuthenticodeSignature -FilePath $path
        $vi = (Get-Item -LiteralPath $path).VersionInfo
        return [pscustomobject]@{
            Status = [string]$sig.Status
            Signer = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { $null }
            CompanyName = $vi.CompanyName
            ProductName = $vi.ProductName
            FileDescription = $vi.FileDescription
            OriginalFilename = $vi.OriginalFilename
        }
    } catch { return $null }
}

function Read-RunKey([string]$path, [string]$scope) {
    $out = @()
    try {
        $item = Get-ItemProperty -Path $path -ErrorAction Stop
        foreach ($prop in $item.PSObject.Properties) {
            if ($prop.Name -match '^PS(Path|ParentPath|ChildName|Drive|Provider)$') { continue }
            $out += [pscustomobject]@{
                Scope = $scope
                Key = $path
                Name = $prop.Name
                Value = [string]$prop.Value
            }
        }
    } catch {}
    return $out
}

function Get-InstalledApps {
    $paths = @(
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    $items = foreach ($p in $paths) {
        Get-ItemProperty $p -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName } | ForEach-Object {
            [pscustomobject]@{
                DisplayName = $_.DisplayName
                DisplayVersion = $_.DisplayVersion
                Publisher = $_.Publisher
                InstallLocation = $_.InstallLocation
                InstallSource = $_.InstallSource
                UninstallString = $_.UninstallString
                QuietUninstallString = $_.QuietUninstallString
                RegistryPath = $_.PSPath
            }
        }
    }
    return @($items | Sort-Object DisplayName -Unique)
}

function Get-Shortcuts {
    $roots = @(
        [Environment]::GetFolderPath('Desktop'),
        [Environment]::GetFolderPath('CommonDesktopDirectory'),
        [Environment]::GetFolderPath('StartMenu'),
        [Environment]::GetFolderPath('CommonStartMenu')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $result = @()
    try { $ws = New-Object -ComObject WScript.Shell } catch { return @() }
    foreach ($root in $roots) {
        Get-ChildItem -LiteralPath $root -Filter *.lnk -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $sc = $ws.CreateShortcut($_.FullName)
                $result += [pscustomobject]@{
                    Path = $_.FullName
                    TargetPath = $sc.TargetPath
                    Arguments = $sc.Arguments
                    WorkingDirectory = $sc.WorkingDirectory
                }
            } catch {}
        }
    }
    return $result
}

function Get-BrowserAssociation([string]$scheme) {
    $key = "HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\$scheme\UserChoice"
    try {
        $p = Get-ItemProperty -Path $key -ErrorAction Stop
        return [pscustomobject]@{ Scheme=$scheme; ProgId=$p.ProgId; Hash=$p.Hash }
    } catch { return [pscustomobject]@{ Scheme=$scheme; ProgId=$null; Hash=$null } }
}

function Get-DefenderAudit {
    $status = $null
    $prefs = $null
    try {
        $s = Get-MpComputerStatus
        $status = [pscustomobject]@{
            AntivirusEnabled = $s.AntivirusEnabled
            RealTimeProtectionEnabled = $s.RealTimeProtectionEnabled
            BehaviorMonitorEnabled = $s.BehaviorMonitorEnabled
            IoavProtectionEnabled = $s.IoavProtectionEnabled
            NISEnabled = $s.NISEnabled
            AntivirusSignatureVersion = $s.AntivirusSignatureVersion
            QuickScanAge = $s.QuickScanAge
            FullScanAge = $s.FullScanAge
        }
    } catch {}
    try {
        $p = Get-MpPreference
        $prefs = [pscustomobject]@{
            ExclusionPath = @($p.ExclusionPath)
            ExclusionProcess = @($p.ExclusionProcess)
            ExclusionExtension = @($p.ExclusionExtension)
            ExclusionIpAddress = @($p.ExclusionIpAddress)
        }
    } catch {}
    return [pscustomobject]@{ Status=$status; Exclusions=$prefs }
}

$admin = Is-Admin
$system = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem

$apps = Get-InstalledApps

$processes = @(Get-CimInstance Win32_Process | ForEach-Object {
    $path = $_.ExecutablePath
    $sig = if ($path) { Get-SigInfo $path } else { $null }
    [pscustomobject]@{
        Name = $_.Name
        ProcessId = $_.ProcessId
        ParentProcessId = $_.ParentProcessId
        ExecutablePath = $path
        CommandLine = $_.CommandLine
        Signature = $sig
    }
})

$services = @(Get-CimInstance Win32_Service | ForEach-Object {
    $exe = Get-ExeFromCommandLine $_.PathName
    $sig = if ($exe) { Get-SigInfo $exe } else { $null }
    $suspiciousLocation = $false
    if ($exe) {
        $lower = $exe.ToLowerInvariant()
        if ($lower -match '\\appdata\\local\\temp\\' -or $lower -match '\\temp\\') { $suspiciousLocation = $true }
    }
    [pscustomobject]@{
        Name = $_.Name
        DisplayName = $_.DisplayName
        State = $_.State
        StartMode = $_.StartMode
        StartName = $_.StartName
        PathName = $_.PathName
        ExecutablePath = $exe
        Signature = $sig
        SuspiciousTempLocation = $suspiciousLocation
    }
})

$tasks = @()
try {
    $tasks = @(Get-ScheduledTask | ForEach-Object {
        [pscustomobject]@{
            TaskName = $_.TaskName
            TaskPath = $_.TaskPath
            State = [string]$_.State
            Actions = @($_.Actions | ForEach-Object { [pscustomobject]@{ Execute=$_.Execute; Arguments=$_.Arguments; WorkingDirectory=$_.WorkingDirectory } })
        }
    })
} catch {}

$runItems = @()
$runItems += Read-RunKey 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' 'HKCU'
$runItems += Read-RunKey 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' 'HKCU'
$runItems += Read-RunKey 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' 'HKLM'
$runItems += Read-RunKey 'HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce' 'HKLM'
$runItems += Read-RunKey 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run' 'HKLM32'

$startup = @()
$startupRoots = @(
    [Environment]::GetFolderPath('Startup'),
    [Environment]::GetFolderPath('CommonStartup')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
foreach ($r in $startupRoots) {
    $startup += @(Get-ChildItem -LiteralPath $r -Force -ErrorAction SilentlyContinue | Select-Object FullName, Name, Length, LastWriteTime)
}

$advanced = [ordered]@{}
$advanced.WinlogonHKLM = try { Get-ItemProperty 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon' | Select-Object Shell,Userinit } catch { $null }
$advanced.WinlogonHKCU = try { Get-ItemProperty 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon' | Select-Object Shell,Userinit } catch { $null }
$advanced.AppInit = try { Get-ItemProperty 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\Windows' | Select-Object AppInit_DLLs,LoadAppInit_DLLs } catch { $null }
$advanced.IFEO = @()
try {
    $advanced.IFEO = @(Get-ChildItem 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\Image File Execution Options' | ForEach-Object {
        $v = Get-ItemProperty $_.PSPath
        if ($v.Debugger) { [pscustomobject]@{ Image=$_.PSChildName; Debugger=$v.Debugger } }
    })
} catch {}

$browser = [ordered]@{}
$browser.HTTP = Get-BrowserAssociation 'http'
$browser.HTTPS = Get-BrowserAssociation 'https'
$browser.EdgePolicyHKLM = try { Get-ItemProperty 'HKLM:\Software\Policies\Microsoft\Edge' } catch { $null }
$browser.EdgePolicyHKCU = try { Get-ItemProperty 'HKCU:\Software\Policies\Microsoft\Edge' } catch { $null }
$browser.ChromePolicyHKLM = try { Get-ItemProperty 'HKLM:\Software\Policies\Google\Chrome' } catch { $null }
$browser.ChromePolicyHKCU = try { Get-ItemProperty 'HKCU:\Software\Policies\Google\Chrome' } catch { $null }

$shortcuts = Get-Shortcuts
$defender = Get-DefenderAudit

$appx = @()
try {
    if ($admin) { $appx = @(Get-AppxPackage -AllUsers | Select-Object Name,PackageFullName,Publisher,InstallLocation) }
    else { $appx = @(Get-AppxPackage | Select-Object Name,PackageFullName,Publisher,InstallLocation) }
} catch {}

$vendor360Regex = '(?i)(360|qihoo|qihu|360安全中心|Beijing Qihu Technology|www\.360\.cn|\\360Safe\\|\\360Game5\\|\\360huabao\\|\\360SoftMgr\\|\\secoresdk\\360se6\\)'
$vendor360 = [ordered]@{}
$vendor360.Apps = @($apps | Where-Object { (($_.DisplayName + ' ' + $_.Publisher + ' ' + $_.InstallLocation) -match $vendor360Regex) })
$vendor360.Processes = @($processes | Where-Object { (($_.Name + ' ' + $_.ExecutablePath + ' ' + $_.Signature.CompanyName) -match $vendor360Regex) })
$vendor360.Services = @($services | Where-Object { (($_.Name + ' ' + $_.DisplayName + ' ' + $_.PathName + ' ' + $_.Signature.CompanyName) -match $vendor360Regex) })
$vendor360.Tasks = @($tasks | Where-Object { (($_.TaskName + ' ' + $_.TaskPath + ' ' + (($_.Actions | ConvertTo-Json -Compress))) -match $vendor360Regex) })
$vendor360.Run = @($runItems | Where-Object { (($_.Name + ' ' + $_.Value) -match $vendor360Regex) })
$vendor360.Shortcuts = @($shortcuts | Where-Object { (($_.Path + ' ' + $_.TargetPath + ' ' + $_.Arguments) -match $vendor360Regex) })


$pupHintRegex = '(?i)(2345|夸克|Quark|荐片|ToolBox|工具盒|屏保|壁纸|游戏大厅|软件管家|下载助手|桌面助手|资讯|推荐|广告)'
$protectedHintRegex = '(?i)(Autodesk|AutoCAD|CAD|Glodon|广联达|GWS|GCCP|GrandDog|GSCServer|Booway|博微|Senseshield|Virbox|工程造价|清标|定额|加密锁|许可证|License Service|Printer|打印机)'
$pupReviewHints = [ordered]@{}
$pupReviewHints.Apps = @($apps | Where-Object { (($_.DisplayName + ' ' + $_.Publisher + ' ' + $_.InstallLocation) -match $pupHintRegex) -and -not (($_.DisplayName + ' ' + $_.Publisher) -match $protectedHintRegex) })
$pupReviewHints.RunItems = @($runItems | Where-Object { (($_.Name + ' ' + $_.Value) -match $pupHintRegex) })
$pupReviewHints.Services = @($services | Where-Object { (($_.Name + ' ' + $_.DisplayName + ' ' + $_.PathName) -match $pupHintRegex) })
$protectedReview = @($apps | Where-Object { (($_.DisplayName + ' ' + $_.Publisher + ' ' + $_.InstallLocation) -match $protectedHintRegex) })

$suspiciousServices = @($services | Where-Object {
    $_.SuspiciousTempLocation -and (
        -not $_.Signature -or $_.Signature.Status -ne 'Valid'
    )
})

$result = [ordered]@{
    SchemaVersion = 3
    AuditTime = (Get-Date).ToString('o')
    IsAdmin = $admin
    Host = [ordered]@{
        ComputerName = $env:COMPUTERNAME
        UserName = $env:USERNAME
        Manufacturer = $system.Manufacturer
        Model = $system.Model
        OS = $os.Caption
        Version = $os.Version
        BuildNumber = $os.BuildNumber
        MemoryGB = [math]::Round($system.TotalPhysicalMemory / 1GB, 2)
    }
    InstalledApps = $apps
    AppX = $appx
    Processes = $processes
    Services = $services
    ScheduledTasks = $tasks
    RunItems = $runItems
    StartupItems = $startup
    AdvancedPersistence = $advanced
    Browser = $browser
    Shortcuts = $shortcuts
    Defender = $defender
    Vendor360 = $vendor360
    PupReviewHints = $pupReviewHints
    ProtectedReviewApps = $protectedReview
    SuspiciousUnsignedTempServices = $suspiciousServices
}

$parent = Split-Path -Parent $OutputPath
if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$result | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Output "Deep audit written to: $OutputPath"
