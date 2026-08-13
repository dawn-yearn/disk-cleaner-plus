#!/usr/bin/env python3
"""
disk-cleaner-plus 智能分类分析器

读取 scan.ps1 输出的 JSON，执行规则分类：
1. 冗余安装包（已安装软件残留 / 明显 setup/installer 名称）
2. 大体积压缩包（按大小/时间分层）
3. 超期下载文件（30/90/180 天未修改）
4. 重复文件（跨盘，保留最新）
5. 开发/缓存/系统保留项
6. 禁止动项（系统目录、Docker/WSL 虚拟磁盘等）

输出 analysis.json 供 server.py / build_report.py 使用。
"""
import json
import os
import re
import sys
import time
from datetime import datetime


def parse_size(text):
    """从 '12.3 GB' / '45.2 MB' 解析为 GB 浮点数。"""
    if text is None:
        return 0.0
    m = re.search(r"([\d.]+)\s*(TB|GB|MB|KB|B)?", str(text), re.I)
    if not m:
        return 0.0
    v = float(m.group(1))
    u = (m.group(2) or "GB").upper()
    if u == "TB":
        return v * 1024
    if u == "MB":
        return v / 1024
    if u == "KB":
        return v / 1024 / 1024
    if u == "B":
        return v / 1024 / 1024 / 1024
    return v


def bytes_to_gb(b):
    return round(b / (1024 ** 3), 2)


def human_size(b):
    n = float(b)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit in ("B", "KB"):
                return f"{int(n)} {unit}"
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{b} B"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 规则：已安装软件识别
# ---------------------------------------------------------------------------

def normalize_name(name):
    if not name:
        return ""
    n = name.lower()
    for ch in "-_.()[]{}:;/\\":
        n = n.replace(ch, " ")
    return " ".join(n.split())


def extract_product_tokens(name):
    """从文件名提取产品标识 token（忽略版本号、架构词）。"""
    n = normalize_name(name)
    skip = {"setup", "install", "installer", "x64", "x86", "amd64", "win32", "windows", "v", "version",
            "x32", "x64", "64", "32", "bit", "edition", "professional", "home", "enterprise", "ultimate",
            "stable", "beta", "alpha", "preview", "release", "final", "latest", "online", "offline",
            "user", "userdata", "exe", "msi", "zip", "rar", "iso"}
    tokens = []
    for t in n.split():
        if re.match(r"^v?\d+(\.\d+)*$", t):
            continue
        if t in skip or len(t) <= 1:
            continue
        tokens.append(t)
    return tokens


def fuzzy_match_installed(installer_name, program_name):
    """宽松匹配：安装包名与已安装软件名是否指向同一产品。

    策略：
    1. 先做 token 交集（严格匹配）
    2. 再对长 token 做子串包含匹配（如 vscodeusersetup ~ Visual Studio Code 的
       vscode / code 片段；wechatsetup ~ wechat）
    3. 返回 (matched: bool, score: float)
    """
    inst_tokens = extract_product_tokens(installer_name)
    prog_tokens = extract_product_tokens(program_name)
    if not inst_tokens or not prog_tokens:
        return False, 0.0

    inst_set = set(inst_tokens)
    prog_set = set(prog_tokens)

    # 1. 严格 token 交集
    inter = inst_set & prog_set
    if inter:
        score = len(inter) / max(len(inst_set), len(prog_set))
        return True, round(max(score, 0.6), 2)

    # 2. 子串匹配：安装包 token 与软件名 token 做包含检测
    #    例如 "vscodeusersetup" 包含 "vscode" 或 "code"；"wechatsetup" 包含 "wechat"
    flat_inst = "".join(inst_tokens)
    flat_prog = "".join(prog_tokens)

    # 2a. 安装包名包含软件全名压缩串（如 wechatsetup 包含 wechat）
    for p in prog_tokens:
        if p in flat_inst and len(p) >= 4:
            return True, 0.9
    # 2b. 软件名包含安装包 token（如 "visual studio code" 的 token 之一 code 在 vscodeusersetup 中）
    for it in inst_tokens:
        for pt in prog_tokens:
            if (it in pt or pt in it) and len(pt) >= 4 and len(it) >= 3:
                return True, 0.75

    # 3. 前缀匹配：安装包 token 与软件 token 共享 >=4 字符前缀
    for it in inst_tokens:
        for pt in prog_tokens:
            common = 0
            for i in range(min(len(it), len(pt))):
                if it[i] == pt[i]:
                    common += 1
                else:
                    break
            if common >= 4 and (common / min(len(it), len(pt))) >= 0.6:
                return True, 0.65

    return False, 0.0


def match_installed_program(installers, programs):
    """为每个安装包判断是否有已安装软件匹配（宽松模糊匹配）。"""
    prog_names = [p.get("name") or "" for p in programs if p.get("name")]

    for inst in installers:
        inst["matched_program"] = None
        inst["match_score"] = 0
        best_name = None
        best_score = 0
        for pname in prog_names:
            ok, score = fuzzy_match_installed(inst.get("name", ""), pname)
            if ok and score > best_score:
                best_score = score
                best_name = pname
        if best_name:
            inst["matched_program"] = best_name
            inst["match_score"] = round(best_score, 2)
    return installers


# ---------------------------------------------------------------------------
# 规则：时间分层
# ---------------------------------------------------------------------------

def bucket_by_age(file_items, thresholds=(30, 90, 180)):
    buckets = {f">{t}": [] for t in thresholds}
    buckets["recent"] = []
    for item in file_items:
        days = item.get("modified_days", 0)
        # 放入最严格的桶：>180 > >90 > >30
        placed = False
        for t in sorted(thresholds, reverse=True):
            if days > t:
                buckets[f">{t}"].append(item)
                placed = True
                break
        if not placed:
            buckets["recent"].append(item)
    return buckets


# ---------------------------------------------------------------------------
# 规则：禁止路径
# ---------------------------------------------------------------------------

NEVER_TOUCH_PATHS = [
    r"^C:\\Windows",
    r"^C:\\Program Files",
    r"^C:\\Program Files \(x86\)",
    r"^C:\\ProgramData",
    r"^C:\\System Volume Information",
    r"^C:\\Recovery",
    r"^C:\\$Recycle.Bin",
    r"pagefile\.sys$",
    r"hiberfil\.sys$",
    r"swapfile\.sys$",
    r"ext4\.vhdx$",
    r"\\Docker\\Desktop\\vms\\",
    r"\\WSL\\",
]


def is_never_touch(path):
    p = path or ""
    for pat in NEVER_TOUCH_PATHS:
        if re.search(pat, p, re.I):
            return True
    return False


# ---------------------------------------------------------------------------
# 规则：可清理缓存目录
# ---------------------------------------------------------------------------

SAFE_CACHE_PATHS = [
    ("User Temp", r"\\AppData\\Local\\Temp$"),
    ("Windows Temp", r"C:\\Windows\\Temp$"),
    ("Windows Update Cache", r"C:\\Windows\\SoftwareDistribution\\Download$"),
    ("Chrome Cache", r"\\Google\\Chrome\\User Data\\.*\\Cache$"),
    ("Edge Cache", r"\\Microsoft\\Edge\\User Data\\.*\\Cache$"),
    ("Firefox Cache", r"\\Mozilla\\Firefox\\Profiles\\.*\\cache2$"),
    ("IE/Explorer Cache", r"\\Microsoft\\Windows\\INetCache$"),
    ("ThumbCache", r"\\Microsoft\\Windows\\Explorer\\ThumbCacheToDelete$"),
    ("npm cache", r"\\npm-cache$"),
    ("pip cache", r"\\pip\\Cache$"),
    ("Yarn cache", r"\\Yarn$"),
    ("uv cache", r"\\uv$"),
    ("go-build cache", r"\\go-build$"),
]


def classify_appdata_folders(appdata_folders):
    safe, change_settings, blocked = [], [], []
    for f in appdata_folders:
        path = f.get("path", "")
        name = f.get("name", "")
        matched = False
        for label, pat in SAFE_CACHE_PATHS:
            if re.search(pat, path, re.I):
                f["clean_label"] = label
                safe.append(f)
                matched = True
                break
        if matched:
            continue
        if "WeChat" in path:
            f["app"] = "WeChat"
            change_settings.append(f)
        elif "QQ" in path or "Tencent" in path:
            f["app"] = "QQ"
            change_settings.append(f)
        elif "OneDrive" in path:
            f["app"] = "OneDrive"
            change_settings.append(f)
        elif is_never_touch(path):
            blocked.append(f)
        else:
            change_settings.append(f)
    return safe, change_settings, blocked


# ---------------------------------------------------------------------------
# 主分析流程
# ---------------------------------------------------------------------------

def build_installer_analysis(installers):
    matched = [x for x in installers if x.get("matched_program")]
    unmatched = [x for x in installers if not x.get("matched_program")]
    return {
        "title": "冗余安装包",
        "description": "已安装软件的残留安装包，或带 setup/installer/version 字样的可执行文件。",
        "all": installers,
        "likely_redundant": matched,
        "unmatched": unmatched,
        "count": len(installers),
        "total_bytes": sum(x.get("size_bytes", 0) for x in installers),
        "redundant_bytes": sum(x.get("size_bytes", 0) for x in matched),
    }


def build_archive_analysis(archives):
    buckets = bucket_by_age(archives)
    return {
        "title": "大体积压缩包",
        "description": ".zip/.rar/.7z 等压缩文件，按最后修改时间分层。",
        "all": archives,
        "count": len(archives),
        "total_bytes": sum(x.get("size_bytes", 0) for x in archives),
        "by_age": buckets,
    }


def build_download_analysis(all_files):
    downloads = [x for x in all_files if "download" in x.get("path", "").lower() or "下载" in x.get("path", "")]
    buckets = bucket_by_age(downloads)
    return {
        "title": "下载文件夹杂乱文件",
        "description": "位于 Downloads / 下载 目录中的大文件，按未使用时间分层。",
        "all": downloads,
        "count": len(downloads),
        "total_bytes": sum(x.get("size_bytes", 0) for x in downloads),
        "by_age": buckets,
    }


def build_duplicate_analysis(dup_groups):
    return {
        "title": "跨盘重复文件",
        "description": "按 SHA256 相同且体积 >=100MB 的重复文件，保留最新一份。",
        "groups": dup_groups,
        "count": sum(g.get("count", 0) for g in dup_groups),
        "wasted_bytes": sum(g.get("wasted_bytes", 0) for g in dup_groups),
    }


def build_big_files_analysis(big_files):
    return {
        "title": "大文件榜",
        "description": "全盘中 >=50MB 的单个大文件，仅定位，需人工确认。",
        "items": big_files,
        "count": len(big_files),
    }


def build_drive_summary(drives, estimates):
    out = []
    for d in drives:
        letter = d.get("letter", "")
        reclaim = estimates.get("per_drive", {}).get(letter, 0)
        out.append({
            **d,
            "reclaimable_gb": round(bytes_to_gb(reclaim), 2),
        })
    return out


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "scan.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "analysis.json"

    data = load_json(src)
    drives = data.get("drives", [])
    programs = data.get("installed_programs", [])
    appdata = data.get("appdata_folders", [])
    files = data.get("files", {})
    dup_groups = data.get("duplicate_groups", [])
    common_folders = data.get("common_folders", [])

    installers = match_installed_program(files.get("installers", []), programs)
    archives = files.get("archives", [])
    all_big = files.get("all_big", [])

    # 移除禁止路径中的文件
    installers = [x for x in installers if not is_never_touch(x.get("path", ""))]
    archives = [x for x in archives if not is_never_touch(x.get("path", ""))]
    all_big = [x for x in all_big if not is_never_touch(x.get("path", ""))]

    inst_analysis = build_installer_analysis(installers)
    arch_analysis = build_archive_analysis(archives)
    dl_analysis = build_download_analysis(all_big)
    dup_analysis = build_duplicate_analysis(dup_groups)
    big_analysis = build_big_files_analysis(all_big)

    safe_cache, change_settings_apps, blocked_appdata = classify_appdata_folders(appdata)

    # 计算各盘可清理潜力
    per_drive_reclaim = {d.get("letter", ""): 0 for d in drives}
    for x in inst_analysis["likely_redundant"]:
        per_drive_reclaim[x.get("drive", "")] = per_drive_reclaim.get(x.get("drive", ""), 0) + x.get("size_bytes", 0)
    for x in dl_analysis["by_age"].get(">90", []):
        per_drive_reclaim[x.get("drive", "")] = per_drive_reclaim.get(x.get("drive", ""), 0) + x.get("size_bytes", 0)
    for x in arch_analysis["by_age"].get(">180", []):
        per_drive_reclaim[x.get("drive", "")] = per_drive_reclaim.get(x.get("drive", ""), 0) + x.get("size_bytes", 0)
    for g in dup_groups:
        for p in g.get("duplicates", []):
            per_drive_reclaim[p.get("drive", "")] = per_drive_reclaim.get(p.get("drive", ""), 0) + p.get("size_bytes", 0)

    total_reclaim = sum(per_drive_reclaim.values())

    # 汇总报告
    summary = {
        "overview": f"全盘整盘扫描完成，预计可通过规则清理约 {bytes_to_gb(total_reclaim):.1f} GB 空间。",
        "total_reclaimable_gb": round(bytes_to_gb(total_reclaim), 2),
        "installers_redundant_gb": round(bytes_to_gb(inst_analysis["redundant_bytes"]), 2),
        "downloads_old_gb": round(bytes_to_gb(sum(x.get("size_bytes", 0) for x in dl_analysis["by_age"].get(">90", []))), 2),
        "archives_old_gb": round(bytes_to_gb(sum(x.get("size_bytes", 0) for x in arch_analysis["by_age"].get(">180", []))), 2),
        "duplicates_wasted_gb": round(bytes_to_gb(dup_analysis["wasted_bytes"]), 2),
    }

    # 时间戳
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scan": data,
        "system": data.get("system", {}),
        "summary": summary,
        "drives": build_drive_summary(drives, {"per_drive": per_drive_reclaim}),
        "common_folders": common_folders,
        "categories": {
            "installers": inst_analysis,
            "archives": arch_analysis,
            "downloads": dl_analysis,
            "duplicates": dup_analysis,
            "big_files": big_analysis,
            "safe_cache": {
                "title": "系统/浏览器/开发缓存",
                "description": "可安全清理的缓存目录，删除后应用会自动重建。",
                "items": safe_cache,
                "total_bytes": sum(x.get("size_bytes", 0) for x in safe_cache),
            },
            "change_settings_first": {
                "title": "先改设置再迁移",
                "description": "微信、QQ、OneDrive、conda、Docker、WSL 等，需要先在软件内修改路径。",
                "items": change_settings_apps,
                "total_bytes": sum(x.get("size_bytes", 0) for x in change_settings_apps),
            },
            "do_not_touch": {
                "title": "禁止移动或删除",
                "description": "Windows 系统目录、Program Files、虚拟磁盘等，不要手动操作。",
                "paths": NEVER_TOUCH_PATHS,
                "appdata_items": blocked_appdata,
            },
        },
    }

    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"分析完成: {out}")
    print(f"预计可清理: {summary['total_reclaimable_gb']:.2f} GB")


if __name__ == "__main__":
    main()
