#Requires -Version 5.1
<#
.SYNOPSIS
    disk-cleaner-plus 全盘只读扫描脚本 (Windows)

.DESCRIPTION
    扫描所有本地磁盘，收集：
    - 各磁盘容量/已用/剩余
    - 常见用户目录大小
    - 大文件、安装包、压缩包、下载文件
    - 跨盘重复文件线索
    输出 JSON 到 stdout，供 analyze.py 分类和生成报告。
    扫描阶段只读，绝不删除或修改任何文件。

.PARAMETER MinFileSizeMB
    大文件/安装包/压缩包扫描阈值，默认 50 MB

.PARAMETER MaxFilesPerFolder
    每个扫描根最多保留的大文件条目数，防止极端目录卡死，默认 2000。

.PARAMETER OutputPath
    可选 JSON 输出路径。建议使用该参数，脚本会显式写入 UTF-8（无 BOM），
    避免 Windows PowerShell 5.1 使用 > 重定向时产生 UTF-16 编码。

.EXAMPLE
    .\scan.ps1 -OutputPath C:\tmp\disk_scan.json
#>

[CmdletBinding()]
param(
    [int]$MinFileSizeMB = 50,
    [int]$MaxFilesPerFolder = 2000,
    [string]$OutputPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$MIN_BYTES = [long]($MinFileSizeMB * 1MB)

$INSTALLER_EXTS = @(".exe", ".msi", ".msp", ".msu", ".iso", ".dmg", ".pkg", ".deb", ".rpm")
$ARCHIVE_EXTS = @(".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".lz4", ".zst")
$MEDIA_EXTS = @(".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mp3", ".flac", ".wav", ".aac", ".ogg", ".wma", ".m4a")
$DOC_EXTS = @(".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".md", ".epub", ".mobi")

# 安装包名称关键词（用于识别 setup/installer 等）
$INSTALLER_KEYWORDS = @("setup", "installer", "install", "package", "bundle", "dist", "release")

# 常见可扫描目录名（在全盘递归时额外关注）
$INTERESTING_FOLDERS = @("Downloads", "download", "下载", "Desktop", "桌面", "Documents", "文档", "Videos", "视频", "Pictures", "图片", "Music", "音乐", "Software", "software", "Apps", "apps", "Program", "program", "Tools", "tools", "Setup", "setup", "Installers", "installers", "压缩包", "Archive", "archive", "ISO", "iso", "Temp", "temp", "tmp")

function Write-Json {
    param($Object)
    $Object | ConvertTo-Json -Depth 12 -Compress:$false
}

function Format-Size {
    param([long]$Bytes)
    if ($Bytes -lt 1KB) { return "{0:N0} B" -f $Bytes }
    if ($Bytes -lt 1MB) { return "{0:N2} KB" -f ($Bytes / 1KB) }
    if ($Bytes -lt 1GB) { return "{0:N2} MB" -f ($Bytes / 1MB) }
    return "{0:N2} GB" -f ($Bytes / 1GB)
}

function Get-LocalDrives {
    $volumes = Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq "Fixed" }
    $result = @()
    foreach ($vol in $volumes) {
        $letter = "$($vol.DriveLetter):\".ToUpper()
        $total = if ($vol.Size) { $vol.Size } else { 0 }
        $free = if ($vol.SizeRemaining) { $vol.SizeRemaining } else { 0 }
        $used = $total - $free
        $result += [PSCustomObject]@{
            letter      = $letter
            mount       = $letter
            filesystem  = $vol.FileSystem
            total_bytes = [long]$total
            used_bytes  = [long]$used
            free_bytes  = [long]$free
            total_gb    = [math]::Round($total / 1GB, 2)
            used_gb     = [math]::Round($used / 1GB, 2)
            free_gb     = [math]::Round($free / 1GB, 2)
            free_pct    = if ($total -gt 0) { [math]::Round(100 * $free / $total, 1) } else { 0 }
            is_system   = ($letter -eq "C:\")
        }
    }
    return $result | Sort-Object letter
}

function Test-IsReparsePoint {
    param([string]$Path)
    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        return ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    }
    catch { return $false }
}

function Get-FolderSize {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    if (Test-IsReparsePoint -Path $Path) { return 0 }
    $total = [long]0
    try {
        Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object {
            try { $total += $_.Length } catch { }
        }
    }
    catch { }
    return $total
}

function Get-InterestingFolders {
    param([string[]]$Drives, [int]$MaxDepth = 3)
    $folders = @()
    foreach ($drive in $Drives) {
        if (-not (Test-Path $drive)) { continue }
        try {
            $top = Get-ChildItem -LiteralPath $drive -Directory -Force -ErrorAction SilentlyContinue | Select-Object -First 30
            foreach ($dir in $top) {
                $name = $dir.Name
                if ($INTERESTING_FOLDERS -contains $name) {
                    $folders += $dir.FullName
                }
                elseif ($MaxDepth -ge 2) {
                    try {
                        $sub = Get-ChildItem -LiteralPath $dir.FullName -Directory -Force -ErrorAction SilentlyContinue | Select-Object -First 20
                        foreach ($s in $sub) {
                            if ($INTERESTING_FOLDERS -contains $s.Name) {
                                $folders += $s.FullName
                            }
                        }
                    }
                    catch { }
                }
            }
        }
        catch { }
    }
    return $folders | Select-Object -Unique
}

function Get-InstalledPrograms {
    $programs = @()
    $regPaths = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($rp in $regPaths) {
        try {
            $items = Get-ItemProperty -Path $rp -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName }
            foreach ($item in $items) {
                $programs += [PSCustomObject]@{
                    name         = $item.DisplayName
                    publisher    = $item.Publisher
                    version      = $item.DisplayVersion
                    install_date = $item.InstallDate
                }
            }
        }
        catch { }
    }
    return $programs
}

function Test-IsInstallerName {
    param([string]$Name)
    $base = [IO.Path]::GetFileNameWithoutExtension($Name).ToLower()
    foreach ($kw in $INSTALLER_KEYWORDS) {
        if ($base -like "*$kw*") { return $true }
    }
    # 版本号模式：-x64-1.2.3、_1.2.3、1.2.3.exe 等
    if ($base -match "(v?\d+\.\d+([._]\d+)*|x64|x86|amd64|win32|setup|installer)") { return $true }
    return $false
}

function Get-FileKind {
    param([string]$Ext)
    $e = $Ext.ToLower()
    if ($INSTALLER_EXTS -contains $e) { return "installer" }
    if ($ARCHIVE_EXTS -contains $e) { return "archive" }
    if ($MEDIA_EXTS -contains $e) { return "media" }
    if ($DOC_EXTS -contains $e) { return "document" }
    return "other"
}

function Find-Files {
    param(
        [string]$Root,
        [long]$MinSize,
        [int]$MaxFiles
    )
    $files = @()
    if (-not (Test-Path -LiteralPath $Root)) { return $files }
    try {
        $items = Get-ChildItem -LiteralPath $Root -Recurse -Force -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -ge $MinSize }
        $c = 0
        foreach ($item in $items) {
            if ($c -ge $MaxFiles) { break }
            try {
                if (Test-IsReparsePoint -Path $item.FullName) { continue }
                $ext = $item.Extension
                $kind = Get-FileKind -Ext $ext
                $files += [PSCustomObject]@{
                    name          = $item.Name
                    path          = $item.FullName
                    drive         = ($item.FullName.Substring(0, 1).ToUpper() + ":")
                    size_bytes    = [long]$item.Length
                    size_h        = (Format-Size $item.Length)
                    ext           = $ext.TrimStart('.').ToLower()
                    kind          = $kind
                    is_installer_name = (Test-IsInstallerName -Name $item.Name)
                    modified      = $item.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
                    modified_days = [int]((Get-Date) - $item.LastWriteTime).TotalDays
                }
                $c++
            }
            catch { }
        }
    }
    catch { }
    return $files
}

function Find-DuplicateGroups {
    param([object[]]$Files)
    $bySize = @{}
    foreach ($f in $Files) {
        $key = $f.size_bytes
        if (-not $bySize.ContainsKey($key)) { $bySize[$key] = @() }
        $bySize[$key] += $f
    }
    $groups = @()
    foreach ($size in $bySize.Keys) {
        $candidates = $bySize[$size]
        if ($candidates.Count -lt 2) { continue }
        $byHash = @{}
        foreach ($c in $candidates) {
            try {
                $hash = (Get-FileHash -LiteralPath $c.path -Algorithm SHA256 -ErrorAction Stop).Hash
                if (-not $byHash.ContainsKey($hash)) { $byHash[$hash] = @() }
                $byHash[$hash] += $c
            }
            catch { }
        }
        foreach ($h in $byHash.Keys) {
            $matches = $byHash[$h]
            if ($matches.Count -lt 2) { continue }
            $sorted = $matches | Sort-Object modified -Descending
            $groups += [PSCustomObject]@{
                size_bytes  = [long]$size
                size_h      = (Format-Size $size)
                count       = $sorted.Count
                wasted_bytes = [long]($size * ($sorted.Count - 1))
                wasted_h     = (Format-Size ($size * ($sorted.Count - 1)))
                keep        = $sorted[0]
                duplicates  = $sorted[1..($sorted.Count - 1)]
                paths       = @($sorted | ForEach-Object { $_.path })
            }
        }
    }
    return $groups | Sort-Object wasted_bytes -Descending
}

function Get-CommonUserFolders {
    return @(
        @{ name = "下载"; path = (Join-Path $env:USERPROFILE "Downloads") },
        @{ name = "桌面"; path = (Join-Path $env:USERPROFILE "Desktop") },
        @{ name = "文档"; path = (Join-Path $env:USERPROFILE "Documents") },
        @{ name = "图片"; path = (Join-Path $env:USERPROFILE "Pictures") },
        @{ name = "视频"; path = (Join-Path $env:USERPROFILE "Videos") },
        @{ name = "音乐"; path = (Join-Path $env:USERPROFILE "Music") }
    )
}

function Get-AppDataFolders {
    $paths = @()
    $local = $env:LOCALAPPDATA
    $roaming = $env:APPDATA
    $targets = @(
        "Tencent\WeChat",
        "Tencent\QQ",
        "Microsoft\OneDrive",
        "npm-cache",
        "pip\Cache",
        "Yarn",
        "uv",
        "go-build",
        "Microsoft\Windows\Explorer\ThumbCacheToDelete",
        "Microsoft\Windows\INetCache",
        "Google\Chrome\User Data\Default\Cache",
        "Microsoft\Edge\User Data\Default\Cache",
        "Mozilla\Firefox\Profiles"
    )
    foreach ($base in @($local, $roaming)) {
        foreach ($t in $targets) {
            $p = Join-Path $base $t
            if (Test-Path -LiteralPath $p) { $paths += @{ name = $t; path = $p; base = $base } }
        }
    }
    return $paths
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

$started = Get-Date
$drives = Get-LocalDrives
$driveLetters = $drives | ForEach-Object { $_.letter }
$installedPrograms = Get-InstalledPrograms

# 扫描有趣的目录作为候选根目录
$interestingFolders = Get-InterestingFolders -Drives $driveLetters -MaxDepth 3

# 合并候选扫描根目录：每个盘根 + 有趣目录 + 用户目录
$scanRoots = New-Object System.Collections.Generic.List[string]
foreach ($d in $driveLetters) { $scanRoots.Add($d) }
foreach ($f in (Get-CommonUserFolders)) { if (Test-Path $f.path) { $scanRoots.Add($f.path) } }
foreach ($f in $interestingFolders) { $scanRoots.Add($f) }
$scanRoots = $scanRoots | Select-Object -Unique

# 文件扫描
$allFiles = @()
foreach ($root in $scanRoots) {
    $allFiles += Find-Files -Root $root -MinSize $MIN_BYTES -MaxFiles $MaxFilesPerFolder
}
# 去重
$seenPaths = @{}
$uniqueFiles = @()
foreach ($f in $allFiles) {
    if (-not $seenPaths.ContainsKey($f.path.ToLower())) {
        $seenPaths[$f.path.ToLower()] = $true
        $uniqueFiles += $f
    }
}
$allFiles = $uniqueFiles

# 分类
$installers = $allFiles | Where-Object { $_.kind -eq "installer" -or $_.is_installer_name }
$archives = $allFiles | Where-Object { $_.kind -eq "archive" }
$media = $allFiles | Where-Object { $_.kind -eq "media" }
$documents = $allFiles | Where-Object { $_.kind -eq "document" }
$bigFiles = $allFiles | Sort-Object size_bytes -Descending | Select-Object -First 200

# 重复文件（只计算 >= 100MB 的文件，避免太多小文件）
$dupCandidates = $allFiles | Where-Object { $_.size_bytes -ge (100MB) }
$duplicateGroups = Find-DuplicateGroups -Files $dupCandidates

# 常见用户目录大小
$commonFolders = @()
foreach ($f in (Get-CommonUserFolders)) {
    if (Test-Path -LiteralPath $f.path) {
        $sz = Get-FolderSize -Path $f.path
        if ($sz -ge (50MB)) {
            $commonFolders += [PSCustomObject]@{
                name = $f.name
                path = $f.path
                size_bytes = $sz
                size_h = (Format-Size $sz)
            }
        }
    }
}

# AppData 特殊目录大小
$appDataFolders = @()
foreach ($f in (Get-AppDataFolders)) {
    $sz = Get-FolderSize -Path $f.path
    if ($sz -ge (50MB)) {
        $appDataFolders += [PSCustomObject]@{
            name = $f.name
            path = $f.path
            base = $f.base
            size_bytes = $sz
            size_h = (Format-Size $sz)
        }
    }
}

# 输出 JSON
$result = [PSCustomObject]@{
    generated_at = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    scan_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    system = [PSCustomObject]@{
        computer_name = $env:COMPUTERNAME
        user          = $env:USERNAME
        os            = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
        min_file_size_mb = $MinFileSizeMB
    }
    drives = @($drives)
    installed_programs = @($installedPrograms)
    common_folders = @($commonFolders)
    appdata_folders = @($appDataFolders)
    files = [PSCustomObject]@{
        installers = @($installers)
        archives   = @($archives)
        media      = @($media)
        documents  = @($documents)
        all_big    = @($bigFiles)
    }
    duplicate_groups = @($duplicateGroups)
    scan_roots = @($scanRoots)
}

$json = Write-Json -Object $result
if ($OutputPath) {
    $fullOutputPath = [IO.Path]::GetFullPath($OutputPath)
    $parent = Split-Path -Parent $fullOutputPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($fullOutputPath, $json, $utf8NoBom)
    Write-Host "扫描完成，UTF-8 JSON 已写入: $fullOutputPath"
}
else {
    $json
}
