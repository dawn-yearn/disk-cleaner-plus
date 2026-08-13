[CmdletBinding()]
param(
    [string]$DestinationRoot = (Join-Path $HOME ".agents\skills"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $DestinationRoot "disk-cleaner-plus"

$sourceResolved = (Resolve-Path -LiteralPath $source).Path
if (Test-Path -LiteralPath $dest) {
    $destResolved = (Resolve-Path -LiteralPath $dest).Path
    if ($sourceResolved -eq $destResolved) {
        Write-Host "disk-cleaner-plus 已位于: $dest"
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $DestinationRoot | Out-Null

if (Test-Path -LiteralPath $dest) {
    if (-not $Force) {
        throw "目标已存在: $dest`n如需覆盖，请运行: .\install-local.ps1 -Force"
    }
    Remove-Item -LiteralPath $dest -Recurse -Force
}

Copy-Item -LiteralPath $source -Destination $dest -Recurse -Force

# Never carry a local Git checkout into the installed skill.
$gitDir = Join-Path $dest ".git"
if (Test-Path -LiteralPath $gitDir) {
    Remove-Item -LiteralPath $gitDir -Recurse -Force
}

Write-Host "已安装 disk-cleaner-plus 到: $dest"
Write-Host "Codex 通常会自动检测；若 /skills 中暂未出现，请重启 Codex。"
