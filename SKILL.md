---
name: disk-cleaner-plus
description: >
  Windows 磁盘深度审查与激进清理 Skill。用于 C 盘爆满、全盘空间不足、AppData/聊天软件/开发缓存膨胀、
  安装包和重复大文件堆积等场景。先只读扫描并下钻大目录，再把项目分为可清理、可迁移、先改设置、禁止动；
  用户明确授权后可批量移入回收站，或对确认无价值的高文件数缓存使用 rmdir /s /q 与多线程硬删。
  不用于注册表优化，也不应在用户只要求“看看/分析”时自动删除任何文件。
---

# disk-cleaner-plus

Windows 全盘深度审查 + AI 辅助激进清理工作流。

## 核心原则

1. **先审查，后执行。** 首轮必须是只读扫描和归因，不因目录名像“cache/temp”就直接删除。
2. **明确授权后可以果断。** 用户已批准一组经过审查的目标后，可批量处理，不要逐文件重复询问。
3. **硬删是正式能力，不是异常路径。** 对确认无价值的高文件数缓存、更新残留、安装包等，允许使用不可恢复的极速硬删。
4. **未知大目录先识别来源。** 软件本体、用户数据、数据库、聊天记录与缓存必须区分。
5. **每轮都有账。** 记录清理前后磁盘剩余空间、处理路径、动作与实际释放量。

## 何时使用

- “C盘满了 / C盘红了”
- “电脑空间不够，帮我清理”
- “AppData 为什么这么大”
- “哪些安装包能删”
- “清理下载目录 / 重复文件”
- “微信 / QQ / WPS / Conda 怎么迁移”
- “帮我做一次磁盘深度审查”

## 何时不要直接执行删除

如果用户只说：

- “看看”
- “检查一下”
- “分析一下”
- “给我报告”
- “哪些可以删”

则本轮只读，不执行删除。

## Skill 根目录

不要写死 WorkBuddy、Codex 或某个用户名路径。

始终以当前 `SKILL.md` 所在目录作为 Skill 根目录，脚本都使用相对位置：

```text
scripts/scan.ps1
scripts/analyze.py
scripts/server.py
scripts/build_report.py
scripts/fast_delete.py
assets/report_template.html
```

## Stage 1：记录磁盘现状

先获取所有固定盘：

- 总容量
- 已使用
- 剩余空间
- 系统盘剩余比例

记录清理前基线。

## Stage 2：全盘只读扫描

优先调用：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\scan.ps1 -OutputPath scan.json
```

扫描内容包括：

- 所有固定磁盘容量；
- 常见用户目录大小；
- >=50 MB 的大文件、安装包、压缩包和媒体文件；
- >=100 MB 的重复文件候选并用 SHA256 核验；
- 已安装软件列表；
- AppData 中常见聊天、浏览器和开发缓存目录。

扫描阶段禁止修改文件。

## Stage 3：分类分析

```powershell
python scripts\analyze.py scan.json analysis.json
```

至少形成四类：

| 分类 | 典型内容 | 默认处理 |
|---|---|---|
| 🟢 可清理 | 临时文件、明确缓存、更新安装包、冗余 installer、重复副本 | 可回收站；确认后可硬删 |
| 🟡 可迁移 | 桌面、下载、视频、项目归档、素材 | 复制/迁移后核验 |
| 🟠 先改设置 | 微信、QQ、WPS、OneDrive、Conda、Docker、WSL | 先改软件路径，再迁移 |
| 🔴 禁止动 | Windows、Program Files、ProgramData、虚拟内存、虚拟磁盘 | 只解释，不手删 |

## Stage 4：系统盘深度下钻

当 C 盘明显偏满、用户明确要求“深度审查”，或基础扫描仍无法解释主要占用时，继续下钻 2～3 级，而不是停在一级目录。

重点检查：

- `C:\Users\<user>`
  - AppData\Local
  - AppData\Roaming
  - Desktop
  - Downloads
  - Python / Conda 环境
  - AI Agent 日志与运行时缓存
- `C:\Windows`
  - WinSxS
  - Installer
  - SoftwareDistribution
  - Logs
- `C:\Program Files`
- `C:\Program Files (x86)`
- `C:\ProgramData`
- `pagefile.sys`
- `hiberfil.sys`
- `swapfile.sys`
- >=300 MB 的最大单文件
- 当天持续增长的大日志 / sqlite / updater 缓存

目标是回答：**到底是什么在占空间、哪些是本体、哪些是缓存、哪些只是可以迁移。**

如果现有脚本没有覆盖某个大目录，允许使用 PowerShell / Python 做额外只读统计；这些临时分析代码不要写进 Skill 源码目录，运行结果也不要提交 Git。

## Stage 5：生成报告

交互式 Dashboard：

```powershell
python scripts\server.py analysis.json
```

静态报告：

```powershell
python scripts\build_report.py analysis.json disk-cleaner-report.html
```

报告至少说明：

- 空间大头；
- 可立即释放的安全空间；
- 可迁移空间；
- 需要卸载 / 改设置才能释放的空间；
- 不建议碰的系统占用；
- 是否存在仍在持续增长的目录或日志。

## Stage 6：解析用户授权范围

### 只读授权

“先看看”“先报告”“不要删” → 绝不执行删除。

### 普通清理授权

“这些全部清理”“按刚才方案执行” → 可处理用户明确指向的已审查项目，默认根据类型选择卸载、正规 cache clean、回收站或硬删。

### 明确硬删授权

“直接删”“硬删”“不用回收站”“这些缓存全部清掉” → 对已审查且属于硬删白名单语义的目标可使用 `fast_delete.py`。

不要因为用户授权了 A、B、C，就顺便把新发现的 D、E、F 一起删掉。新增目标必须重新说明。

## Stage 7：普通清理

优先采用软件原生方式：

- Conda → `conda clean`
- pip / npm / uv → 对应 cache 命令
- 软件本体 → Windows 正规卸载流程
- 微信 / WPS / QQ 数据 → 软件设置中的存储迁移或缓存管理

普通文件删除可由 Dashboard 移入回收站。

## Stage 8：极速硬删

适合：

- 数量极多的小缓存；
- 明确的 updater / installer 残留；
- 已核验的临时目录；
- 已确认要删除的重复副本；
- 用户明确不要的旧缓存分区。

### 策略 A：整目录 DIR Wipe

```python
import fast_delete
ok, msg = fast_delete.fast_delete_folder_windows(target_dir)
```

底层为 Windows `rmdir /s /q`，绕过回收站，不可恢复。

### 策略 B：多线程删除

```python
deleted, failed, errors = fast_delete.fast_delete_files_multithreaded(
    file_paths,
    max_workers=32,
)
```

### 策略 C：先移后炸

只有当“要保留的文件极少、垃圾文件极多”时使用：

```python
ok, msg = fast_delete.execute_smart_fast_cleanup(
    target_dir=target_dir,
    keep_files_list=keep_files,
)
```

先将保留项移到暂存目录，再整体硬删旧目录，再移回保留项。

### Defender 临时排除

这是可选的高速模式，不默认修改安全设置。

只有同时满足以下条件才考虑：

1. 用户明确同意临时排除；
2. 目录是来源明确的可信缓存；
3. 文件数量巨大且 Defender 明显成为删除瓶颈；
4. 排除范围足够窄。

可使用管理员 PowerShell：

```powershell
Add-MpPreference -ExclusionPath "E:\AppCache\KnownCache"
```

完成后优先移除：

```powershell
Remove-MpPreference -ExclusionPath "E:\AppCache\KnownCache"
```

禁止把整个 `C:\`、整个用户 Profile、Downloads、项目根目录或未知软件数据根加入排除。

## 硬边界

无论用户要求多激进，都不要直接硬删以下内容：

- 盘根；
- `C:\Windows` 及其子目录；
- `C:\Program Files` 及其子目录；
- `C:\Program Files (x86)` 及其子目录；
- `C:\ProgramData` 及其子目录；
- 用户 Profile 根目录；
- `pagefile.sys` / `hiberfil.sys` / `swapfile.sys`；
- Docker / WSL 虚拟磁盘；
- 未确认用途的数据库、聊天记录、文档、照片、视频；
- 符号链接 / junction 指向的未知位置。

系统占用需要释放时，走对应的 Windows 正规设置或软件卸载方式，而不是直接删文件。

## 微信 / QQ / WPS 等应用数据

不要把整个 `AppData\Roaming\Tencent`、`kingsoft` 等目录当缓存删除。

先区分：

- 数据库 / 聊天记录；
- 图片视频与附件；
- 下载目录；
- updater；
- cache / temp；
- 配置、模板、插件。

能在应用内修改存储路径的，优先改设置并迁移。

## 清理完成后的复核

必须再次获取：

1. 各磁盘剩余空间；
2. 本轮总释放空间；
3. 迁移出系统盘的空间；
4. 每个已执行目标的状态；
5. 删除失败 / 文件占用 / 权限不足项目；
6. 新发现但尚未授权的候选项。

最终输出建议使用：

| 路径/项目 | 原大小 | 动作 | 结果 | 释放空间 |
|---|---:|---|---|---:|

并给出：

- 清理前剩余空间；
- 清理后剩余空间；
- 实际释放；
- 下一轮候选，但不要自动继续扩大清理范围。
