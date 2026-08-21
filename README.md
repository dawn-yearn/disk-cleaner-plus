# disk-cleaner-plus v3 upgrade

> Windows 全盘深度审查 + 软件生态审计 + 垃圾/PUP/广告组件清理 + 顽固硬删除。

这是 `dawn-yearn/disk-cleaner-plus` 的 **v3 升级包（overlay）**：把本压缩包内容覆盖到现有仓库根目录即可。未包含的旧文件（例如原有 Dashboard、`scan.ps1`、`analyze.py`、HTML 模板、`install-local.ps1`）继续保留。

> 注意：这个 ZIP 是“升级补丁”，不要把它单独当作完整仓库安装；先覆盖到你现有的 `disk-cleaner-plus` 仓库，再运行仓库原有的 `install-local.ps1 -Force`。

## v3 新增什么

### 1. 软件生态深度审计

新增 `scripts/deep_audit_windows.ps1`，除磁盘大小外继续审计：

- 已安装程序 / AppX；
- 进程；
- 服务；
- 计划任务；
- Run / RunOnce / Startup；
- Winlogon / AppInit / IFEO；
- HTTP / HTTPS 默认浏览器；
- Edge / Chrome policy；
- 桌面和开始菜单快捷方式真实 Target + Arguments；
- Defender 状态与 exclusions（权限允许时）；
- Temp/AppData 中未签名服务等高风险驻留。

### 2. 360 / Qihoo 专项清理

新增 `scripts/cleanup_360.ps1`。

每次“深度软件审计”都检查 360/Qihoo 生态；只有用户已授权清理时才执行卸载。

```powershell
# Dry Run，仅生成 inventory，不修改
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup_360.ps1 -OutputDir .\cleanup-360

# 用户明确授权后，以管理员 PowerShell 执行
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup_360.ps1 -OutputDir .\cleanup-360 -Execute
```

处理顺序：

**广告/壁纸/自启 → 正规卸载 → 进程/服务/任务/Run → 快捷方式 → 明确残留 → 浏览器复核 → Zero Audit**。

`cleanup_360.ps1` 只在有 360/Qihoo 证据的 vendor scope 内工作，不扫描式删除其他软件，更不会碰工程软件保护区。

### 3. 更可靠的硬删除

`scripts/fast_delete.py` v3 不再只是 `rmdir /s /q` 的薄包装，而是固定了 Agent 常见失败的回退链：

- 默认 preview，必须 `--execute` 才删除；
- JSONL 落盘日志，不依赖 stdout；
- 路径通过参数传递，不用 `shell=True`；
- 可只结束“可执行文件位于目标目录内”的进程；
- 清只读/系统/隐藏属性；
- 对已确认目标可 `takeown + icacls`；
- 仍锁定时可 rename 为 `delete-pending-*` 并安排重启删除；
- 通用模式拒绝 Windows / Program Files / ProgramData 根和用户 Profile 根；
- Program Files 中的 360 残留只能通过 `--vendor-scope 360` 放行。

例：

```powershell
python .\scripts\fast_delete.py delete "E:\QQ\old-cache" --execute --kill-processes --log .\delete-log.jsonl
```

确认的 360 残留：

```powershell
python .\scripts\fast_delete.py delete "C:\Program Files (x86)\360\360Safe" `
  --execute --vendor-scope 360 --kill-processes --take-ownership --schedule-reboot
```

### 4. 可逆“待删除”模式

新增 `scripts/stage_for_delete.py`。

对聊天库、媒体、旧项目目录等，不必一上来永久删除：

```powershell
python .\scripts\stage_for_delete.py stage "E:\旧版微信" --staging "E:\待删除" --execute
```

同盘优先 rename，不触发普通删除/回收站钩子，并生成可追踪结果。

重复文件先 SHA-256：

```powershell
python .\scripts\stage_for_delete.py dedupe "E:\QQ" --staging "E:\待删除" --min-size-mb 100 --execute
```

每组保留一份，只有哈希一致的副本才移动。

### 5. 回收站核验

新增 `scripts/verify_recycle.py`：直接遍历 `<drive>:\$Recycle.Bin` 的 `$I*` 元数据，不使用容易被沙箱/策略拦截的 `Shell.Application` COM。

```powershell
python .\scripts\verify_recycle.py "E:\QQ\old-cache"
```

用于回答一个很现实的问题：**刚才到底真的永久删除了，还是只是被 safe-delete 钩子塞进了回收站？**

## 工作软件保护

v3 将 CAD、Autodesk、广联达/Glodon、博微/Booway、Virbox/Senseshield、GrandDog/GSCServer、许可证/加密狗/打印机驱动等默认放入 `WORK_PROTECTED / PROTECTED_REVIEW`。

保护关键词在：

```text
profiles/protected_software.json
```

关键词只是“保护提示”，不是判定软件安全的唯一证据。

## 推荐使用方式

### 只做报告

```text
$disk-cleaner-plus
深度审查一下这台 Windows：磁盘占用 + 已安装软件 + 自启/服务/计划任务 + 浏览器劫持，只生成报告，不删除。
```

### 深度清洁

```text
$disk-cleaner-plus
先做完整只读审计。保护工作软件和用户数据；把确认的广告/流氓式软件、更新残留和缓存整理成批次，等我确认后清理。
```

### 明确要求清理 360

```text
$disk-cleaner-plus
如果检测到 360/Qihoo 全家桶，按 v3 的 360 专项流程完整移除：先 Dry Run 清单，确认属于 360 后执行卸载、驻留和残留清理，最后做 Zero Audit。工作软件不要碰。
```

## 把 v3 覆盖到现有仓库

解压本包，把以下文件/目录复制到原 `disk-cleaner-plus` 根目录并覆盖同名文件：

```text
SKILL.md
README.md
agents/openai.yaml
scripts/fast_delete.py
scripts/deep_audit_windows.ps1
scripts/cleanup_360.ps1
scripts/stage_for_delete.py
scripts/verify_recycle.py
profiles/
docs/V3_CHANGELOG.md
.gitignore.v3-additions.txt
```

原仓库中这些文件继续保留，不要删：

```text
scripts/scan.ps1
scripts/analyze.py
scripts/build_report.py
scripts/server.py
assets/report_template.html
docs/demo-c-drive-report.html
docs/images/...
install-local.ps1
THIRD_PARTY_NOTICES.md
```

然后按 `GITHUB_UPDATE.md` 提交到 GitHub。
