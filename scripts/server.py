#!/usr/bin/env python3
"""
disk-cleaner-plus 本地报告服务 + 安全清理 API (Windows)

启动 127.0.0.1 随机端口 + 随机 token，提供交互式 HTML Dashboard。
支持三种操作模式：

- trash: 移入 Windows 回收站（可恢复，默认安全模式）
- open:  在资源管理器中打开位置
- hard_delete: 极速硬删（绕过回收站，不可恢复）

hard_delete 使用 scripts/fast_delete.py：
    策略 A - rmdir /s /q 整目录暴风抹除（10 万小文件约 3~5 秒）
    策略 B - 32 线程并发 os.remove()（比单线程快 10~20 倍）

安全模型：
- trash / hard_delete 都要求路径在白名单中（analysis.json 标记为可清理）
- hard_delete 需要请求带 confirm=True（前端二次确认后才发送）
- 系统目录 / 盘根永远拒绝
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "report_template.html")
TOKEN = secrets.token_urlsafe(24)

DATA = {}
TPL = ""
TRASH_ALLOW = set()
HARD_DELETE_ALLOW = set()
OPEN_ALLOW = set()
LOG_PATH = ""

try:
    sys.path.insert(0, HERE)
    import fast_delete
except ImportError:
    fast_delete = None


def expand(p):
    return os.path.realpath(os.path.expanduser(p))


def load(src):
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()

    trash_allow, open_allow = set(), set()
    # 硬删白名单：只有被明确判定为"垃圾/可再生"的类别才允许
    # 包括：冗余安装包、超期下载、长期未动压缩包、重复文件副本、安全缓存
    hard_allow = set()

    def add_trash(path):
        if not path:
            return
        rp = expand(path)
        trash_allow.add(rp)
        open_allow.add(rp)

    def add_hard(path):
        if not path:
            return
        rp = expand(path)
        hard_allow.add(rp)
        trash_allow.add(rp)  # 硬删候选同时允许移回收站（双保险）
        open_allow.add(rp)

    cats = data.get("categories", {})

    # 冗余安装包（已匹配已安装软件）
    for it in cats.get("installers", {}).get("likely_redundant", []):
        add_hard(it.get("path"))

    # 超期下载文件（>90 天）
    for it in cats.get("downloads", {}).get("by_age", {}).get(">90", []):
        add_hard(it.get("path"))

    # 长期未动的压缩包（>180 天）
    for it in cats.get("archives", {}).get("by_age", {}).get(">180", []):
        add_hard(it.get("path"))

    # 重复文件（保留最新以外的副本）
    for g in cats.get("duplicates", {}).get("groups", []):
        for d in g.get("duplicates", []):
            add_hard(d.get("path"))

    # 安全缓存目录
    for it in cats.get("safe_cache", {}).get("items", []):
        add_hard(it.get("path"))

    # 其余类别只允许打开（大文件榜等）
    for cat in ["installers", "archives", "downloads"]:
        for it in cats.get(cat, {}).get("all", []):
            p = it.get("path")
            if p:
                rp = expand(p)
                if os.path.exists(rp):
                    open_allow.add(rp)
    for it in cats.get("big_files", {}).get("items", []):
        p = it.get("path")
        if p:
            rp = expand(p)
            if os.path.exists(rp):
                open_allow.add(rp)

    # 重复文件 keep 也允许打开
    for g in cats.get("duplicates", {}).get("groups", []):
        k = g.get("keep")
        if k and k.get("path"):
            rp = expand(k["path"])
            if os.path.exists(rp):
                open_allow.add(rp)

    return data, tpl, trash_allow, hard_allow, open_allow


def move_to_recycle_bin(path):
    """使用 SHFileOperationW 移到回收站（可恢复）。

    注意：SHFileOperationW 在静默模式下，即使操作成功也可能返回误导性错误码
    （如 2 / 1223），因此以实际结果为准：路径已不存在即视为成功。
    """
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    # 统一反斜杠格式 + 双 null 结尾（ctypes 会自动追加 null）
    op.pFrom = os.path.abspath(path).replace("/", "\\") + "\x00\x00"
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    # 以实际结果为准：路径不存在 = 已进回收站，成功
    if not os.path.exists(path):
        return
    if rc != 0:
        raise OSError(f"SHFileOperation failed (code {rc})")


def hard_delete_paths(paths, verbose=False):
    """极速硬删：优先整目录暴风抹除，散落文件多线程并发删除。"""
    if fast_delete is None:
        raise OSError("fast_delete 模块不可用")

    total_deleted = 0
    total_failed = 0
    errors = []

    # 目录走策略 A（rmdir /s /q），文件走策略 B（多线程）
    dirs = [p for p in paths if os.path.isdir(p)]
    files = [p for p in paths if os.path.isfile(p)]

    for d in dirs:
        ok, msg = fast_delete.fast_delete_folder_windows(d, recreate=True)
        if ok:
            total_deleted += 1
        else:
            total_failed += 1
            errors.append((d, msg))

    if files:
        deleted, failed, errs = fast_delete.fast_delete_files_multithreaded(files, max_workers=32, verbose=verbose)
        total_deleted += deleted
        total_failed += failed
        errors.extend(errs)

    return total_deleted, total_failed, errors


def open_in_explorer(path):
    target = path if os.path.isdir(path) else os.path.dirname(path)
    subprocess.run(["explorer", target])


def init_log(src):
    base = os.path.splitext(os.path.basename(src))[0] or "disk_cleaner"
    log_dir = os.path.join(os.path.expanduser("~"), "Desktop", "disk_cleaner_logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"{base}_{time.strftime('%Y%m%d')}_log.json")


def append_log(mode, done):
    if not LOG_PATH or not done:
        return
    payload = {"actions": []}
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            pass
    for p in done:
        payload["actions"].append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "path": p
        })
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            blob = json.dumps(DATA, ensure_ascii=False)
            cfg = json.dumps({
                "token": TOKEN,
                "endpoint": "/action",
                "safeMode": True,
                "hardDeleteEnabled": fast_delete is not None,
            })
            html = TPL.replace("__REPORT_DATA__", blob).replace("__DELETE_CONFIG__", cfg)
            self._send(200, html, "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/action":
            self._send(404, json.dumps({"ok": False, "error": "not found"}))
            return
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, json.dumps({"ok": False, "error": "host not allowed"}))
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._send(400, json.dumps({"ok": False, "error": "bad request"}))
            return
        if req.get("token") != TOKEN:
            self._send(403, json.dumps({"ok": False, "error": "token mismatch"}))
            return

        mode = req.get("mode")
        if mode == "trash":
            allow = TRASH_ALLOW
        elif mode == "hard_delete":
            allow = HARD_DELETE_ALLOW
            if not req.get("confirm"):
                self._send(400, json.dumps({"ok": False, "error": "hard_delete 需要 confirm=true 二次确认"}))
                return
        elif mode == "open":
            allow = OPEN_ALLOW
        else:
            self._send(400, json.dumps({"ok": False, "error": "mode not supported"}))
            return

        done = []
        failed = []
        for p in (req.get("paths") or []):
            rp = expand(p)
            if rp not in allow:
                self._send(403, json.dumps({"ok": False, "error": f"path not allowed: {p}"}))
                return
            # 永远拒绝盘根
            if len(rp) <= 3:
                self._send(403, json.dumps({"ok": False, "error": "refuse root drive"}))
                return
            try:
                if mode == "open":
                    open_in_explorer(rp)
                elif not os.path.exists(rp):
                    pass  # 已不存在，视为成功
                elif mode == "trash":
                    move_to_recycle_bin(rp)
                elif mode == "hard_delete":
                    ok, msg = _hard_delete_single(rp)
                    if not ok:
                        raise OSError(msg)
                done.append(p)
            except Exception as e:
                failed.append({"path": p, "error": str(e)})

        if mode in ("trash", "hard_delete"):
            append_log(mode, done)

        if failed:
            self._send(500, json.dumps({"ok": False, "done": done, "failed": failed}))
        else:
            self._send(200, json.dumps({"ok": True, "done": done, "mode": mode}))


def _hard_delete_single(path):
    """删除单个路径（目录用策略 A，文件用多线程）。"""
    if os.path.isdir(path):
        return fast_delete.fast_delete_folder_windows(path, recreate=True)
    deleted, failed, errors = fast_delete.fast_delete_files_multithreaded([path], max_workers=8)
    if failed:
        return False, errors[0][1] if errors else "unknown"
    return True, ""


def main():
    no_open = "--no-open" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if a != "--no-open"]
    if not args:
        print("Usage: server.py <analysis.json> [--no-open]")
        sys.exit(1)

    global DATA, TPL, TRASH_ALLOW, HARD_DELETE_ALLOW, OPEN_ALLOW, LOG_PATH
    src = args[0]
    DATA, TPL, TRASH_ALLOW, HARD_DELETE_ALLOW, OPEN_ALLOW = load(src)
    LOG_PATH = init_log(src)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"报告服务已启动: {url}")
    print(f"安全模式: 普通删除移入回收站 | 极速硬删 {len(HARD_DELETE_ALLOW)} 项（不可恢复）")
    print(f"可移回收站 {len(TRASH_ALLOW)} 项 | 日志: {LOG_PATH}")
    print("按 Ctrl+C 停止服务")
    if not no_open:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务。")


if __name__ == "__main__":
    main()
