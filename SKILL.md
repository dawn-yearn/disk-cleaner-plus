---
name: disk-cleaner-plus
description: >
  Windows 磁盘与软件生态深度清洁 Skill。用于 C 盘/多盘空间审查、AppData/聊天软件/开发缓存膨胀、
  重复文件、安装残留、广告/壁纸/浏览器劫持、流氓式自启/PUP、顽固软件残留与 360/Qihoo 全家桶清理。
  默认先只读扫描、识别用户数据与工作软件保护区，再按授权执行迁移、正规卸载、可逆“待删除”暂存或硬删除。
  内置 360 专项发现与清理协议、safe-delete/进程锁/权限失败回退流程、回收站核验与清理账本。
  不用于未经授权的一键删除；用户只要求“看看/分析/报告”时必须保持只读。
---

# disk-cleaner-plus v3

Windows 全盘空间审查 + 软件生态审计 + AI 辅助深度清洁工作流。

v3 的目标不是“多删几个文件”，而是把 Windows 长期使用后常见的三类混乱统一处理：

1. **空间混乱**：缓存、安装包、重复文件、聊天软件媒体、开发环境与大目录膨胀；
2. **软件生态混乱**：广告软件、壁纸/屏保、软件管家、浏览器劫持、自启、服务、计划任务与残留；
3. **删除执行混乱**：safe-delete 钩子、回收站未释放、进程锁、权限不足、stdout 被吞、中文/`$` 路径导致命令失败。

---

# 一、核心原则

## 1. 先读现状，再下结论

每轮操作前必须重新读取当前状态，不依赖“上次好像清过”的记忆。

至少记录：

- 固定磁盘总量/剩余空间；
- 目标目录实际大小；
- 已安装软件；
- 相关进程/服务/计划任务/启动项；
- 浏览器默认关联与劫持点；
- 本轮将处理的具体路径与软件。

## 2. 用户数据与软件残留分开处理

必须区分：

- 软件本体/更新器/广告组件；
- 明确缓存/temp；
- 用户数据库/聊天记录；
- 图片/视频/附件；
- 工程项目文件/Office/PDF/CAD 数据；
- 许可证、加密狗、驱动与插件。

“目录很大”不是删除理由。

## 3. 不可逆内容优先进入“待删除”

对于用户数据、数据库、聊天记录、唯一副本、来源不明的目录，优先使用同盘 rename/move 到：

```text
<盘符>:\待删除\...
```

这通常比回收站稳定，也不会被 safe-delete 钩子拦截。

只有以下情况才直接硬删：

- 用户明确说“直接删/硬删/不进回收站”；
- 或目标是已经确认无价值的软件残留、缓存、installer/updater、重复副本；
- 且安全边界检查通过。

## 4. 去重先哈希再动

同名/同大小不等于同内容。

重复文件必须先用 SHA-256（旧流程中的 MD5 也可作为快速核验，但 v3 默认 SHA-256）分组，确认字节级一致后，每组至少保留 1 份。

## 5. 每轮都有账

最终必须报告：

- 清理前剩余空间；
- 清理后剩余空间；
- 实际释放量；
- 每个目标的动作与结果；
- 未能处理的锁定/权限/未知项目；
- 进入“待删除”但尚未真实释放的空间。

---

# 二、授权语义

## 只读授权

用户说：

- “看看”
- “检查一下”
- “分析一下”
- “给我报告”
- “哪些可以删”

→ **只读，不修改。**

## 普通清理授权

用户说：

- “按刚才方案清理”
- “这些垃圾软件删掉”
- “做一次深度清洁”

→ 可对已经审查并明确归类的目标执行：正规卸载、停用已确认垃圾自启、可逆暂存、缓存清理。

## 明确硬删授权

用户说：

- “直接删”
- “硬删”
- “不要回收站”
- “彻底删干净残留”

→ 对已经确认的目标可使用 `scripts/fast_delete.py`。

**不要因为用户批准 A/B/C，就顺带删除新发现的 D/E/F。**

---

# 三、Stage 1 — 空间与软件生态基线

先获取固定磁盘：

- 总容量；
- 已使用；
- 剩余；
- 系统盘剩余比例。

然后执行原有磁盘扫描：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\scan.ps1 -OutputPath scan.json
```

当用户要求“深度清洁/电脑很卡/广告很多/怀疑有垃圾软件/病毒残留”时，**额外自动执行软件生态只读审计**：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deep_audit_windows.ps1 -OutputPath deep-audit.json
```

深度审计至少覆盖：

- Installed Apps；
- AppX；
- 进程；
- Windows Services；
- Scheduled Tasks；
- Run/RunOnce/Startup；
- Winlogon/AppInit/IFEO 等关键驻留入口；
- HTTP/HTTPS 默认浏览器；
- Edge/Chrome policy；
- Desktop/Start Menu `.lnk` 的 Target + Arguments；
- Defender 状态与可读取的 exclusions；
- Temp/AppData 中的服务可执行文件；
- 数字签名/Publisher/CompanyName。

---

# 四、Stage 2 — 分类与保护区

至少形成以下类别：

| 分类 | 说明 | 默认动作 |
|---|---|---|
| NORMAL | Windows/OEM/常用正常软件 | 保留 |
| WORK_PROTECTED | CAD、造价、工程软件、许可证、加密狗、驱动 | 禁止自动删除 |
| USER_DATA | 文档、照片、聊天库、项目文件 | 只迁移/暂存 |
| CLEANABLE | 明确缓存、installer、updater 残留 | 可清理 |
| UNWANTED_CONFIRMED | 用户已明确不要的软件/广告组件 | 授权后卸载 |
| PUP_SUSPECT | 可疑推广/工具盒/播放器/广告驻留 | 先审查再处理 |
| MALWARE_SUSPECT | Temp 服务、随机目录未签名持久化等高风险项 | Defender/静态取证优先 |
| UNKNOWN_REVIEW | 无法确认来源 | 不删 |

## 工作软件保护规则

以下关键词命中时默认进入 `WORK_PROTECTED` 或 `PROTECTED_REVIEW`，除非有非常明确的恶意证据：

- Autodesk / AutoCAD / CAD；
- Glodon / 广联达 / GWS / GCCP / GrandDog / GSCServer；
- Booway / 博微；
- Senseshield / Virbox；
- 工程造价/清标/定额/加密锁/许可证；
- 打印机/扫描仪/签章/加密狗驱动；
- 用户明确标记为工作所需的软件。

即使这些组件有自启/服务，也不要仅凭“后台运行”删除。

---

# 五、360 / Qihoo 专项协议（v3 新增核心）

v3 **每次深度软件审计都自动检查 360/Qihoo 生态**。

但删除规则分两种模式：

1. **只读/普通磁盘审查**：只报告 360 组件，不自动卸载；
2. **用户已明确要求移除 360，或已授权“清理广告/流氓/垃圾软件”且 Agent 已确认目标属于 360/Qihoo**：可把确认的整套 360 组件视为一个已授权 vendor scope，不必对每个 360 子组件重复询问。

不要把“360 软件”客观描述成“病毒”；应描述为：

- 用户明确不要的软件；
- 广告/推广/壁纸/浏览器接管组件；
- vendor bundle（厂商组件集合）；
- 如有具体恶意证据，再单独标 `MALWARE_SUSPECT`。

## 360 识别证据

至少使用两个维度交叉确认：

- DisplayName/Publisher 包含 `360`、`Qihoo`、`Qihu`、`Beijing Qihu Technology`、`360安全中心`；
- 数字签名/CompanyName；
- 安装路径：`Program Files\360`、`AppData\...\360*`、`secoresdk\360se6`、`360Game5`、`360huabao`、`360SoftMgr` 等；
- 服务 BinaryPath；
- 计划任务 Action；
- Run 项 Value；
- 快捷方式 Target。

单纯文件名里有 “360” 不足以硬删未知文件。

## 360 清理顺序

当用户已授权后优先调用：

```powershell
# 先演练
powershell -ExecutionPolicy Bypass -File scripts\cleanup_360.ps1 -OutputDir .\cleanup-360

# 用户已明确授权后执行
powershell -ExecutionPolicy Bypass -File scripts\cleanup_360.ps1 -OutputDir .\cleanup-360 -Execute
```

脚本/Agent 必须按以下顺序：

### A. 先杀广告体验

优先停止并移除明确属于 360 的：

- 360huabao / 画报 / 壁纸 / 屏保；
- tray；
- 游戏大厅；
- 推广 helper；
- 对应 Run/Task。

用户“开机就是 360 壁纸”时，这一项排在正式卸载之前。

### B. 正规卸载软件本体

建议顺序：

1. 360 游戏大厅；
2. 360 看图/壁纸/画报；
3. 360 安全浏览器；
4. 360 软件管家/推广组件；
5. 360 安全卫士。

优先使用 UninstallString/官方卸载器。

### C. 清驻留

卸载后重扫并处理明确属于 360 的：

- Processes；
- Services；
- Scheduled Tasks；
- Run/Startup；
- Start Menu/Desktop shortcuts；
- URL/browser registration；
- updater/helper。

服务只有在 BinaryPath/Publisher/路径证据明确时才删除。

### D. 清残留目录

对已确认属于 360、且软件已经卸载的目录，可硬删。

重点复核：

```text
C:\Program Files\360
C:\Program Files (x86)\360
%APPDATA%\360*
%LOCALAPPDATA%\360*
%PROGRAMDATA%\360*
%APPDATA%\secoresdk\360se6
%APPDATA%\360Game5
%APPDATA%\360huabao
%APPDATA%\360SoftMgr
```

不要通配删除任何名字里带 360 的用户文件。

### E. 修浏览器

检查：

- HTTP/HTTPS ProgId；
- Edge startup/homepage/new-tab/search；
- Edge/Chrome policy；
- 浏览器扩展；
- 快捷方式 Arguments。

如果 HTTP/HTTPS 仍为 `360seURL`：

- 不伪造 Windows 11 `UserChoice` Hash；
- 打开 Windows `Default apps` 设置，提示用户把 Edge/Chrome 设回默认；
- 清除已经卸载的 360 URL handler 残留。

如果 Edge 启动仍进入 `hao.360.com` 等：

- 先备份 profile Preferences；
- 只修明确指向 360 的启动页/主页/扩展；
- 不删除整个 Edge profile。

### F. 360 Zero Audit

重启后必须再次检查：

| Category | 目标 |
|---|---|
| Installed apps | 360/Qihoo = 0 |
| Processes | 0 |
| Services | 0 |
| Scheduled tasks | 0 |
| Startup | 0 |
| Browser hijack | CLEAN |
| Wallpaper/ad components | 0 |
| Shortcuts | 0 或仅死链接待删 |
| Residual dirs | 仅 UNKNOWN_REVIEW 可保留 |

---

# 六、顽固删除 / “硬删除做不到”决策树（v3 核心）

当 Agent 遇到“删不掉”时，不要重复调用同一个 `Remove-Item`。

按以下固定顺序处理。

## Step 1：确认语义

- 用户说“删=移到待删除” → 使用 `stage_for_delete.py`；
- 用户说“直接删/硬删/不进回收站” → 使用 `fast_delete.py`；
- 未明确 → 用户数据默认暂存，软件残留可在已授权 scope 内硬删。

## Step 2：检查进程锁

先查相关进程/服务。

对软件组件目录：

- 只结束可执行路径明确位于目标软件目录的进程；
- 先 Stop 服务，再结束进程；
- 不要 `taskkill` 无关系统进程。

QQ/微信/浏览器等运行中时，先关闭再清其组件目录。

## Step 3：脚本与日志先写到可写目录，不要赌盘根权限

某些本地 Agent/沙箱会禁止直接在 `E:\`、`F:\` 等盘根创建脚本或日志。

因此：

- 临时 `.ps1/.py`、日志、JSON 先写到用户可写目录、Skill 自身 runtime 目录或 Agent 的 memory/tmp；
- 如果最终报告必须落到某个盘根，生成完成后再用同盘 rename/move 放过去；
- 不要因为盘根写入失败就误判“扫描/删除失败”。

## Step 4：不要依赖 stdout

本地 Agent/沙箱经常吞 PowerShell/Bash 回显。

所有重要操作必须：

- 写日志文件；
- 写 JSON/JSONL 结果；
- 完成后再 Read 文件确认。

不要仅凭“命令退出码看起来正常”判定成功。

## Step 5：避免 Bash → PowerShell 套娃

如果环境提供独立 PowerShell 执行能力，直接用它。

含中文、空格、`$Recycle.Bin`、`&` 等路径：

- 不放进 Bash 内联命令；
- 优先脚本文件 + 参数；
- Python `subprocess` 使用参数数组，避免 `shell=True`。

## Step 6：safe-delete 钩子拦截时

如果 `Remove-Item` 被 safe-delete 拦截、强制进回收站或返回 `SAFE_DELETE_FAIL_CLOSED`：

对**已经明确授权硬删且通过安全检查**的目录，使用：

```powershell
python scripts\fast_delete.py delete "E:\target" --execute
```

底层优先使用 Windows：

```text
cmd.exe /d /c rmdir /s /q <target>
```

对单文件使用 `del /f /q`。

## Step 7：权限不足

只有对**已确认目标目录**才允许：

1. 清只读/系统/隐藏属性；
2. `takeown`；
3. `icacls` 给 Administrators 完全控制；
4. 再次硬删。

禁止对整个 C:\、Users、Windows、Program Files 根做 takeown/icacls。

## Step 8：仍被锁定

优先：

1. 停目标服务；
2. 结束目标软件进程；
3. 同目录 rename 为 `.delete-pending-<timestamp>`；
4. 需要时安排重启后删除；
5. 重启后复核。

不要为了删一个第三方残留去关闭 Defender、关闭系统安全功能或删除未知驱动。

## Step 9：验证“真删”

硬删后必须验证三件事：

1. 原路径不存在；
2. 磁盘可用空间变化符合预期；
3. 没有只是被塞进回收站。

回收站验证优先：

```powershell
python scripts\verify_recycle.py "E:\original\path"
```

脚本直接遍历 `<盘符>:\$Recycle.Bin` 的 `$I*` 索引文件，不依赖 Shell.Application COM。

遍历 SID 子目录时遇到 Access Denied 必须跳过，不能让整个扫描崩溃。

---

# 七、“待删除”可逆暂存（推荐）

对聊天记录、媒体、项目文件、旧版本目录等，使用：

```powershell
python scripts\stage_for_delete.py stage "E:\source" --staging "E:\待删除" --execute
```

规则：

- 同盘优先 rename；
- 记录 source → destination manifest；
- 目标重名自动加时间戳；
- 不把 staged 大小算作“已释放空间”。

## 重复文件

```powershell
python scripts\stage_for_delete.py dedupe "E:\QQ" --staging "E:\待删除" --min-size-mb 100 --execute
```

必须先 SHA-256，确认一致后每组保留一份。

---

# 八、微信 / QQ / WPS 等应用数据

不要把整个 Tencent/WeChat/WPS 数据根当缓存删。

先区分：

- 数据库/聊天记录；
- 图片视频/附件；
- 接收文件；
- updater；
- cache/temp；
- 配置/模板/插件。

如果按年份清媒体：

1. 先列 `YYYY-MM`；
2. 把保留月份写成显式白名单；
3. 先预览总大小与文件数；
4. 默认移到同盘 `待删除`；
5. 用户确认后再最终硬删。

数据库（如 `.db`）和未确认迁移完整性的旧聊天目录，不自动硬删。

---

# 九、Defender 与恶意嫌疑

对以下项目优先进入安全调查，而不是直接当普通垃圾删：

- 服务可执行文件位于 Temp 随机目录；
- AppData 随机目录中未签名 EXE 以服务/任务长期驻留；
- 可疑 Defender exclusions；
- 伪装成 Office/CAD 的快捷方式实际指向 cmd/PowerShell/随机 AppData EXE；
- 无法解释的 WMI/Winlogon/AppInit/IFEO 驻留。

推荐顺序：

1. 文件 hash/signature/version info；
2. Defender custom scan；
3. 必要时 full scan；
4. 记录检测结果；
5. 再决定隔离/移除驻留。

不要因为 Defender “0 threats” 就自动把可疑 Temp 服务判成安全。

---

# 十、硬边界

通用 `fast_delete.py` 默认拒绝：

- 盘根；
- `C:\Windows`；
- `C:\Program Files` 根；
- `C:\Program Files (x86)` 根；
- `C:\ProgramData` 根；
- 用户 Profile 根；
- `pagefile.sys` / `hiberfil.sys` / `swapfile.sys`；
- Docker/WSL 虚拟磁盘；
- 未确认用途的数据库/文档/照片/视频；
- junction/symlink 指向未知位置。

对于 Program Files 下**已经正式卸载且 vendor evidence 明确**的软件残留，只能通过专用 vendor cleanup（例如 `cleanup_360.ps1`）进入硬删，不允许通用扫描器“看见大就删”。

---

# 十一、最终复核

每轮完成后必须重新获取：

1. 各磁盘剩余空间；
2. 本轮实际释放空间；
3. 进入待删除但未释放的空间；
4. 软件卸载状态；
5. 进程/服务/计划任务/自启残留；
6. 浏览器接管是否恢复；
7. 删除失败/权限不足/重启待处理项目；
8. 工作软件保护区是否 untouched。

推荐最终表格：

| 路径/项目 | 原大小 | 动作 | 结果 | 实际释放 |
|---|---:|---|---|---:|

对于 360 专项，再附 `360 Zero Audit`。

---

# 十二、Skill 根目录与脚本

不要写死 WorkBuddy/Codex/用户名路径。

以当前 `SKILL.md` 所在目录为 Skill 根目录。

v3 新增/重点脚本：

```text
scripts/scan.ps1                  # 原磁盘扫描
scripts/analyze.py                # 原分类分析
scripts/deep_audit_windows.ps1    # 软件生态深审
scripts/cleanup_360.ps1           # 360 专项 Dry Run / Execute
scripts/fast_delete.py            # 顽固硬删 + 日志 + 权限/锁处理
scripts/stage_for_delete.py       # 可逆待删除 + SHA-256 去重
scripts/verify_recycle.py         # 直接检查 $Recycle.Bin/$I
```

运行生成的审计 JSON、日志、清理报告、backup、待删除 manifest 不提交 Git。
