# disk-cleaner-plus

> Windows 全盘深度审查 + 软件生态审计 + AI 辅助深度清洁 Skill。  
> 先把空间占用、软件驻留和用户数据看明白，再决定哪些内容迁移、正规卸载、暂存、进回收站，哪些可以彻底硬删。

![C 盘深度审查报告示例](docs/images/c-drive-audit-preview.png)

`disk-cleaner-plus` 面向 Codex / 本地 AI Agent，用于处理 Windows 长期使用后常见的两类混乱：

- **磁盘空间混乱**：C 盘 AppData 膨胀、聊天软件媒体与缓存、开发环境缓存、安装包、重复大文件、更新残留等；
- **软件生态混乱**：广告/壁纸/屏保组件、浏览器接管、软件管家、PUP（Potentially Unwanted Program，潜在不需要程序）、顽固自启、服务、计划任务和卸载残留。

它不是传统的一键“系统优化工具”，也不是用目录名猜垃圾的暴力删除器。核心流程是：

**只读扫描 → 深度审查 → AI 分类与保护 → 用户确认 → 卸载 / 迁移 / 暂存 / 清理 → 复扫核验。**

当前版本：`v3.0.0`

---

## v3.0 主要更新

v3 在原有磁盘清理能力上加入了完整的软件生态审查与顽固删除工作流。

### 软件生态深度审计

新增 `scripts/deep_audit_windows.ps1`，可只读检查：

- 已安装程序 / AppX；
- 进程；
- Windows 服务；
- 计划任务；
- Run / RunOnce / Startup；
- Winlogon / AppInit / IFEO 等关键驻留入口；
- HTTP / HTTPS 默认浏览器；
- Edge / Chrome policy；
- 桌面、公共桌面、开始菜单快捷方式的真实 `Target + Arguments`；
- Microsoft Defender 状态及有权限读取的 exclusions；
- Temp / AppData 中作为服务运行的可执行文件；
- Publisher、CompanyName 与数字签名等来源信息。

### 360 / Qihoo 专项清理

新增 `scripts/cleanup_360.ps1`。

深度软件审计时会自动检查 360 / Qihoo 组件；**只有用户已明确授权清理时才执行卸载和删除**。

处理链：

**广告/壁纸/自启 → 正规卸载 → 进程/服务/任务/Run → 快捷方式 → 明确残留 → 浏览器复核 → Zero Audit**

默认 Dry Run：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup_360.ps1 -OutputDir .\cleanup-360
```

明确授权后，在管理员 PowerShell 中：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup_360.ps1 -OutputDir .\cleanup-360 -Execute
```

脚本只处理具有 360 / Qihoo 证据的 vendor scope，不把其他软件按名称相似度批量删除。

### 更可靠的顽固硬删除

`scripts/fast_delete.py` v3 增加了 Agent 常见失败场景的 fallback（失败回退）：

- 默认 preview，必须显式 `--execute` 才永久删除；
- JSONL 日志落盘，不依赖可能被吞掉的 stdout；
- 用户路径通过参数传递，避免中文、空格、`$` 等 shell 解析问题；
- 可只结束“可执行文件确实位于目标目录内部”的锁定进程；
- 清除只读 / System / Hidden 属性；
- 对已确认目标可执行 `takeown + icacls`；
- 普通删除失败时使用 `cmd.exe rmdir /s /q` / `del /f /q`；
- 仍被锁定时可重命名为 `delete-pending-*`，并安排重启删除；
- 删除后再次检查目标是否真正消失。

### 可逆“待删除”模式

新增 `scripts/stage_for_delete.py`。

对聊天库、旧项目、媒体、来源不完全确定的数据，优先同盘移动到：

```text
<盘符>:\待删除\
```

例如：

```powershell
python .\scripts\stage_for_delete.py stage "E:\旧版微信" --staging "E:\待删除" --execute
```

这种方式比立即永久删除更适合需要人工复核的数据，同时可绕开部分 safe-delete 钩子的回收站行为。

重复文件支持先 SHA-256 核验，再每组保留至少一份：

```powershell
python .\scripts\stage_for_delete.py dedupe "E:\QQ" --staging "E:\待删除" --min-size-mb 100 --execute
```

### 回收站真实性核验

新增 `scripts/verify_recycle.py`，直接读取 `<drive>:\$Recycle.Bin` 下的 `$I*` 元数据，用来判断：

> “刚才是真的永久删除了，还是被 Agent / safe-delete 机制偷偷塞进了回收站？”

```powershell
python .\scripts\verify_recycle.py "E:\QQ\old-cache"
```

---

## 主要能力

### 1. 全盘空间扫描

覆盖 C、D、E、F 等固定磁盘，统计：

- 磁盘容量与剩余空间；
- 常见用户目录；
- 大文件；
- installer / archive / 媒体文件；
- 重复大文件候选；
- 已安装软件；
- AppData 中常见聊天、浏览器与开发缓存。

### 2. C 盘深度审查

当系统盘明显偏满时，继续下钻：

- `Users / AppData`
- `Windows`
- `Program Files`
- `Program Files (x86)`
- `ProgramData`
- pagefile / hiberfil / swapfile
- Conda / Python / AI Agent 缓存
- updater、日志和持续增长目录

目标不是“看到大目录就删”，而是解释：

**到底是什么在占空间、哪些是软件本体、哪些是缓存、哪些应该迁移。**

### 3. AI 分级决策

| 分类 | 含义 | 默认处理 |
|---|---|---|
| `NORMAL` | Windows / OEM / 正常应用 | 保留 |
| `WORK_PROTECTED` | CAD、造价、许可证、加密狗、驱动 | 禁止自动删除 |
| `USER_DATA` | 文档、照片、聊天库、项目文件 | 迁移 / 暂存 |
| `CLEANABLE` | 明确缓存、installer、updater 残留 | 可清理 |
| `UNWANTED_CONFIRMED` | 用户明确不要的软件/广告组件 | 授权后卸载 |
| `PUP_SUSPECT` | 推广工具盒、广告驻留等 | 先审查 |
| `MALWARE_SUSPECT` | Temp 未签名服务等高风险驻留 | Defender / 取证优先 |
| `UNKNOWN_REVIEW` | 来源不明 | 不删 |

### 4. 工作软件保护区

v3 默认保护：

- Autodesk / AutoCAD / CAD；
- Glodon / 广联达 / GWS / GCCP；
- GrandDog / GSCServer；
- Booway / 博微；
- Senseshield / Virbox；
- 工程计价 / 清标 / 定额 / 许可证 / 加密锁；
- 打印机、扫描仪、签章、加密狗驱动；
- 用户明确说明属于工作的软件。

关键词配置位于：

```text
profiles/protected_software.json
```

**有后台服务或自启并不等于垃圾软件。**

### 5. 交互式 HTML Dashboard

原有 Dashboard 继续保留：

```powershell
python .\scripts\server.py .\analysis.json
```

支持查看分类、路径与候选项目，并执行经过授权的清理操作。

### 6. 清理日志与复扫

每一轮结束后都应重新统计：

- 清理前 / 后剩余空间；
- 实际释放量；
- 已执行项目；
- 失败 / 锁定 / 权限不足项目；
- 移入“待删除”但尚未真正释放的空间；
- 新发现但尚未授权的候选项。

---

## 项目结构

```text
disk-cleaner-plus/
├── SKILL.md
├── README.md
├── THIRD_PARTY_NOTICES.md
├── install-local.ps1
│
├── agents/
│   └── openai.yaml
│
├── profiles/
│   ├── protected_software.json
│   ├── pup_hints.json
│   └── vendor_360.json
│
├── scripts/
│   ├── scan.ps1
│   ├── analyze.py
│   ├── build_report.py
│   ├── server.py
│   ├── fast_delete.py
│   ├── deep_audit_windows.ps1
│   ├── cleanup_360.ps1
│   ├── stage_for_delete.py
│   └── verify_recycle.py
│
├── assets/
│   └── report_template.html
│
└── docs/
    ├── V3_CHANGELOG.md
    ├── demo-c-drive-report.html
    └── images/
        └── c-drive-audit-preview.png
```

运行时产生的扫描 JSON、清理日志、真实审计报告、360 inventory 等由 `.gitignore` 排除，不应提交到公开仓库。

---

## 安装

### 方法 1：复制 Skill 文件夹

不同本地 Agent 的 Skill 目录可能不同，例如：

```text
C:\Users\<用户名>\.agents\skills\
C:\Users\<用户名>\.codex\skills\
C:\Users\<用户名>\.workbuddy\skills\
```

将整个仓库复制为：

```text
...\skills\disk-cleaner-plus\
```

最终确保：

```text
...\skills\disk-cleaner-plus\SKILL.md
```

而不是多套一层目录。

### 方法 2：仓库自带安装脚本

在仓库根目录：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-local.ps1
```

覆盖已有安装：

```powershell
.\install-local.ps1 -Force
```

当前 `install-local.ps1` 默认安装到：

```text
$HOME\.agents\skills\disk-cleaner-plus
```

如果你的 Agent 使用其他目录，可手动复制整个仓库。

### 方法 3：从 GitHub 安装

支持 Skill installer 的 Agent 可直接使用仓库地址：

```text
https://github.com/dawn-yearn/disk-cleaner-plus
```

---

## 推荐使用方式

这个 Skill 包含不可恢复的硬删除能力，因此 `agents/openai.yaml` 默认关闭隐式触发，建议显式调用。

### 只做磁盘报告

```text
$disk-cleaner-plus
深度审查一下我的 C 盘，只生成报告，不删除任何东西。
```

### 磁盘 + 软件生态深度审查

```text
$disk-cleaner-plus
深度审查这台 Windows：磁盘占用、已安装软件、自启、服务、计划任务、浏览器劫持和可疑驻留。
先只生成报告，不删除。
```

### 深度清洁

```text
$disk-cleaner-plus
先完整只读审计。保护工作软件和用户数据，把确认的缓存、广告/PUP 软件和残留整理成清理批次，等我确认后执行。
```

### 专项移除 360

```text
$disk-cleaner-plus
如果检测到 360/Qihoo 组件，先做 Dry Run inventory。
确认属于 360 后，按 v3 专项流程完整移除，并做 Zero Audit。
工作软件不要碰。
```

### 清理 QQ / 微信等大目录

```text
$disk-cleaner-plus
检查 QQ / 微信的数据占用。
区分数据库、聊天记录、媒体、重复文件、缓存和旧组件。
不确定的数据先放到同盘“待删除”，不要直接永久删除。
```

---

## 手动运行核心流程

通常直接让 Agent 使用 Skill 即可；下面用于手动调试。

### 1. 磁盘扫描

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\scan.ps1 -OutputPath .\scan.json
```

### 2. 分类分析

```powershell
python .\scripts\analyze.py .\scan.json .\analysis.json
```

### 3. 软件生态审计

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deep_audit_windows.ps1 -OutputPath .\deep-audit.json
```

### 4. 启动 Dashboard

```powershell
python .\scripts\server.py .\analysis.json
```

服务只绑定 `127.0.0.1`。

### 5. 生成静态 HTML 报告

```powershell
python .\scripts\build_report.py .\analysis.json .\disk-cleaner-report.html
```

静态报告不包含本机删除 API，适合留存。

---

## 顽固硬删

### CLI：推荐给 Agent 的 v3 方式

默认只 preview：

```powershell
python .\scripts\fast_delete.py delete "E:\AppCache\old-cache"
```

真正执行：

```powershell
python .\scripts\fast_delete.py delete "E:\AppCache\old-cache" `
  --execute `
  --kill-processes `
  --log .\delete-log.jsonl
```

对于已确认的 360 Program Files 残留：

```powershell
python .\scripts\fast_delete.py delete "C:\Program Files (x86)\360\360Safe" `
  --execute `
  --vendor-scope 360 `
  --kill-processes `
  --take-ownership `
  --schedule-reboot `
  --log .\delete-360.jsonl
```

### 兼容原 Dashboard 的 Python API

v3 保留原接口：

```python
import fast_delete

ok, msg = fast_delete.fast_delete_folder_windows(
    r"D:\AppCache\old-cache"
)

deleted, failed, errors = fast_delete.fast_delete_files_multithreaded(
    file_paths,
    max_workers=32,
)
```

旧 Dashboard / `server.py` 无需因为 v3 升级而重写调用方式。

---

## 关于 Microsoft Defender

本 Skill 可以读取 Defender 状态并把异常 exclusions 作为审计线索，但**不应为了普通清理随意关闭 Defender**。

对于真正的 `MALWARE_SUSPECT`：

1. 先做静态来源与驻留检查；
2. 使用 Defender 定向 / 完整扫描；
3. 需要时再做离线扫描；
4. 不应因为“未报毒”就自动认定未知文件安全。

---

## 安全边界

即使这是一个偏激进的清理 Skill，也有明确边界：

- 扫描 / 报告请求必须保持只读；
- 不直接硬删磁盘根目录；
- 不通用硬删 `C:\Windows`；
- 不通用硬删 Program Files / ProgramData 中的未知目录；
- 不直接删除 `pagefile.sys` / `hiberfil.sys` / `swapfile.sys`；
- 不把整个 QQ / 微信 / WPS 用户数据根目录当缓存；
- 数据库、聊天记录、照片、视频、项目文件默认不硬删；
- 不跟随未知 symbolic link / junction 做递归删除；
- 工程软件、许可证、加密狗与驱动默认保护；
- 新发现目标不能因为用户批准了另一批项目就自动扩大删除范围；
- 永久删除前必须有明确授权；
- 360 专项 vendor scope 只适用于已确认属于 360 / Qihoo 的组件。

---

## 环境要求

- Windows 10 / 11
- Windows PowerShell 5.1+ 或 PowerShell 7
- Python 3
- 核心 Python 脚本仅依赖标准库

---

## Changelog

v3 详细变化见：

[`docs/V3_CHANGELOG.md`](docs/V3_CHANGELOG.md)

---

## 来源与致谢

本项目由已有 Windows 磁盘清理工作流与公开项目 [`onebtcdesign/ai-storage-cleaner`](https://github.com/onebtcdesign/ai-storage-cleaner) 的设计思路整合而来，尤其参考了“只读扫描 → 分类 → HTML 报告 → 再决定清理”的流程。

详细第三方说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

> 注意：截至本版本整理时，上游仓库未声明明确的开源许可证。如果未来要进行大范围再分发，尤其是存在直接复用第三方源码片段时，请先确认授权边界或将相关实现独立重写后再添加正式许可证。

---

`disk-cleaner-plus` 的定位不是“尽量多删”，而是：

**让 AI 先把 Windows 的空间、软件与数据关系看明白，然后在明确授权后，把真正该清的东西干净、可核验地处理掉。**
