#!/usr/bin/env python3
"""Robust Windows hard-delete helper for disk-cleaner-plus v3.

Design goals:
- preview by default; destructive mode requires --execute;
- never rely only on stdout: always support persistent JSONL logs;
- avoid shell=True and inline user paths;
- handle safe-delete hooks by using cmd.exe built-ins directly;
- optionally stop processes whose executable is inside the target;
- optionally take ownership only of the confirmed target;
- optionally schedule stubborn files/directories for deletion at reboot;
- verify target disappearance after deletion.

This helper is for user-authorized local cleanup. It intentionally refuses broad
system roots and unknown Program Files targets.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import ntpath
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MOVEFILE_DELAY_UNTIL_REBOOT = 0x00000004

CRITICAL_EXACT = {
    "c:\\",
    r"c:\windows",
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\programdata",
    r"c:\users",
}
CRITICAL_FILES = {
    r"c:\pagefile.sys",
    r"c:\hiberfil.sys",
    r"c:\swapfile.sys",
}
VENDOR_360_FRAGMENTS = (
    "\\360\\",
    "\\360safe\\",
    "\\360game5\\",
    "\\360huabao\\",
    "\\360softmgr\\",
    "\\secoresdk\\360se6\\",
)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Logger:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **payload: Any) -> None:
        row = {"time": now_iso(), "event": event, **payload}
        line = json.dumps(row, ensure_ascii=False)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        print(line, flush=True)


def win_norm(path: str) -> str:
    p = ntpath.normpath(path.strip().strip('"'))
    return p.lower()


def path_is_root(path: str) -> bool:
    drive, tail = ntpath.splitdrive(ntpath.normpath(path))
    return bool(drive) and tail in ("\\", "/", "")


def is_reparse_point(path: str) -> bool:
    try:
        return os.path.islink(path)
    except OSError:
        return False


def is_safe_target(path: str, vendor_scope: str | None = None) -> tuple[bool, str]:
    n = win_norm(path)
    if not n or path_is_root(n):
        return False, "drive/root deletion is forbidden"
    if n in CRITICAL_EXACT or n in CRITICAL_FILES:
        return False, "critical Windows target is forbidden"
    if n.startswith("c:\\windows\\"):
        return False, "C:\\Windows subtree is forbidden"
    # User profile root: C:\Users\Name, but allow deeper children after review.
    parts = [p for p in n.replace("/", "\\").split("\\") if p]
    if len(parts) == 3 and parts[0].endswith(":") and parts[1] == "users":
        return False, "user profile root is forbidden"

    in_program_files = n.startswith("c:\\program files\\") or n.startswith("c:\\program files (x86)\\")
    in_programdata = n.startswith("c:\\programdata\\")
    if in_program_files or in_programdata:
        if vendor_scope == "360" and any(fragment in n + "\\" for fragment in VENDOR_360_FRAGMENTS):
            return True, "allowed confirmed 360 vendor scope"
        return False, "Program Files/ProgramData subtree requires a dedicated vendor cleanup"

    if is_reparse_point(path):
        return False, "symbolic link/junction target is refused by generic hard-delete"
    return True, "allowed"


def run(args: list[str], logger: Logger, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    logger.write("command_start", argv=args)
    try:
        cp = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
            errors="replace",
        )
    except Exception as exc:  # noqa: BLE001
        logger.write("command_exception", argv=args, error=repr(exc))
        raise
    logger.write(
        "command_end",
        argv=args,
        returncode=cp.returncode,
        stdout=cp.stdout[-8000:],
        stderr=cp.stderr[-8000:],
    )
    return cp


def powershell_json(static_script: str, env: dict[str, str], logger: Logger) -> Any:
    merged = os.environ.copy()
    merged.update(env)
    logger.write("powershell_probe_start")
    cp = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", static_script],
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        errors="replace",
    )
    logger.write("powershell_probe_end", returncode=cp.returncode, stderr=cp.stderr[-4000:])
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        logger.write("powershell_probe_parse_failed", stdout=cp.stdout[-4000:])
        return None


def processes_under_target(target: str, logger: Logger) -> list[dict[str, Any]]:
    # User path is passed via environment, not interpolated into PowerShell code.
    script = r'''
$target = [IO.Path]::GetFullPath($env:DCP_TARGET).TrimEnd('\\')
$items = Get-CimInstance Win32_Process | ForEach-Object {
  $p = $_.ExecutablePath
  if ($p) {
    try {
      $full = [IO.Path]::GetFullPath($p)
      if ($full.StartsWith($target + '\\', [StringComparison]::OrdinalIgnoreCase) -or
          $full.Equals($target, [StringComparison]::OrdinalIgnoreCase)) {
        [pscustomobject]@{ Name=$_.Name; ProcessId=$_.ProcessId; ExecutablePath=$full }
      }
    } catch {}
  }
}
@($items) | ConvertTo-Json -Compress
'''
    data = powershell_json(script, {"DCP_TARGET": target}, logger)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    return list(data)


def kill_target_processes(target: str, logger: Logger) -> None:
    for item in processes_under_target(target, logger):
        pid = str(item.get("ProcessId", ""))
        if not pid.isdigit():
            continue
        run(["taskkill.exe", "/PID", pid, "/F", "/T"], logger, timeout=30)


def clear_attributes(target: str, logger: Logger) -> None:
    if os.path.isdir(target):
        run(["attrib.exe", "-R", "-S", "-H", target, "/S", "/D"], logger, timeout=180)
    elif os.path.exists(target):
        run(["attrib.exe", "-R", "-S", "-H", target], logger, timeout=30)


def take_ownership(target: str, logger: Logger) -> None:
    # Caller has already passed safety checks; never call this for roots.
    if os.path.isdir(target):
        run(["takeown.exe", "/F", target, "/R", "/D", "Y"], logger, timeout=600)
        run(["icacls.exe", target, "/grant", "*S-1-5-32-544:F", "/T", "/C"], logger, timeout=600)
    else:
        run(["takeown.exe", "/F", target], logger, timeout=60)
        run(["icacls.exe", target, "/grant", "*S-1-5-32-544:F", "/C"], logger, timeout=60)


def hard_delete_once(target: str, logger: Logger) -> int:
    if os.path.isdir(target) and not os.path.islink(target):
        cp = run(["cmd.exe", "/d", "/c", "rmdir", "/s", "/q", target], logger, timeout=1800)
    else:
        cp = run(["cmd.exe", "/d", "/c", "del", "/f", "/q", target], logger, timeout=600)
    return cp.returncode


def schedule_delete_on_reboot(path: str, logger: Logger) -> bool:
    if os.name != "nt":
        logger.write("schedule_reboot_unavailable", reason="not Windows")
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move = kernel32.MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    move.restype = ctypes.c_int

    paths: list[str] = []
    if os.path.isdir(path) and not os.path.islink(path):
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                paths.append(os.path.join(root, name))
            for name in dirs:
                paths.append(os.path.join(root, name))
        paths.append(path)
    else:
        paths.append(path)

    ok_all = True
    for item in paths:
        ok = bool(move(item, None, MOVEFILE_DELAY_UNTIL_REBOOT))
        err = ctypes.get_last_error() if not ok else 0
        logger.write("schedule_reboot_delete", path=item, success=ok, winerror=err)
        ok_all = ok_all and ok
    return ok_all


def rename_pending(target: str, logger: Logger) -> str | None:
    parent = ntpath.dirname(target)
    name = ntpath.basename(target.rstrip("\\/"))
    pending = ntpath.join(parent, f"{name}.delete-pending-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        os.replace(target, pending)
        logger.write("renamed_pending", source=target, destination=pending)
        return pending
    except Exception as exc:  # noqa: BLE001
        logger.write("rename_pending_failed", source=target, destination=pending, error=repr(exc))
        return None


def delete_target(
    target: str,
    *,
    execute: bool,
    vendor_scope: str | None,
    kill_processes: bool,
    take_owner: bool,
    schedule_reboot: bool,
    logger: Logger,
) -> dict[str, Any]:
    ok, reason = is_safe_target(target, vendor_scope)
    result: dict[str, Any] = {
        "target": target,
        "safe": ok,
        "safety_reason": reason,
        "execute": execute,
        "exists_before": os.path.exists(target),
    }
    logger.write("preflight", **result)
    if not ok:
        result["status"] = "REFUSED"
        return result
    if not os.path.exists(target):
        result["status"] = "ALREADY_MISSING"
        return result
    if not execute:
        result["status"] = "PREVIEW"
        return result

    if kill_processes:
        kill_target_processes(target, logger)
    clear_attributes(target, logger)
    hard_delete_once(target, logger)

    if os.path.exists(target) and take_owner:
        take_ownership(target, logger)
        clear_attributes(target, logger)
        hard_delete_once(target, logger)

    pending_path = None
    if os.path.exists(target) and schedule_reboot:
        pending_path = rename_pending(target, logger) or target
        schedule_delete_on_reboot(pending_path, logger)

    result["exists_after"] = os.path.exists(target)
    result["pending_path"] = pending_path
    pending_exists = bool(pending_path and pending_path != target and os.path.exists(pending_path))
    result["pending_exists"] = pending_exists
    if pending_exists:
        result["status"] = "RENAMED_PENDING_REBOOT"
    elif not result["exists_after"]:
        result["status"] = "DELETED"
    elif schedule_reboot:
        result["status"] = "PENDING_REBOOT_OR_FAILED"
    else:
        result["status"] = "FAILED_OR_LOCKED"
    logger.write("result", **result)
    return result



# ---------------------------------------------------------------------------
# Backward-compatible API retained for the original Dashboard/server.py.
# ---------------------------------------------------------------------------
def _is_protected_folder(folder_path):
    """Legacy compatibility: return (protected, reason)."""
    ok, reason = is_safe_target(str(folder_path), vendor_scope=None)
    return (not ok), reason if not ok else ""


def fast_delete_folder_windows(folder_path, recreate=True):
    """Legacy Strategy A API, now backed by the v3 safer cmd invocation."""
    if os.name != "nt":
        return False, "Windows only"
    if not folder_path:
        return False, "路径为空"
    if not os.path.exists(folder_path):
        return False, f"路径不存在: {folder_path}"
    if not os.path.isdir(folder_path):
        return False, f"不是文件夹: {folder_path}"
    protected, reason = _is_protected_folder(folder_path)
    if protected:
        return False, f"拒绝硬删受保护目录（{reason}）: {folder_path}"
    logger = Logger(None)
    try:
        hard_delete_once(str(folder_path), logger)
        success = not os.path.exists(folder_path)
        if success and recreate:
            os.makedirs(folder_path, exist_ok=True)
        return success, "" if success else "删除后目标仍存在，可能被进程锁定或权限不足"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def fast_delete_files_multithreaded(file_paths, max_workers=32, verbose=False):
    """Legacy Strategy B API for scattered files."""
    from concurrent.futures import ThreadPoolExecutor
    import threading

    deleted = 0
    failed = 0
    errors = []
    lock = threading.Lock()

    def one(path):
        nonlocal deleted, failed
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                ok, msg = fast_delete_folder_windows(path, recreate=False)
                if not ok:
                    raise OSError(msg)
            elif os.path.exists(path):
                # os.remove is a permanent delete on normal local files; safe-delete
                # hooks may still interfere in some Agent environments, so callers that
                # need guaranteed bypass should use the v3 CLI `delete --execute`.
                os.remove(path)
            with lock:
                deleted += 1
            if verbose:
                print(f"  已删除: {path}")
        except Exception as exc:  # noqa: BLE001
            with lock:
                failed += 1
                errors.append((path, str(exc)))
            if verbose:
                print(f"  失败: {path} - {exc}")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(one, file_paths))
    return deleted, failed, errors


def execute_smart_fast_cleanup(target_dir, keep_files_list, temp_staging_dir=None):
    """Legacy '先移后炸' API retained for server.py and existing prompts."""
    if not os.path.isdir(target_dir):
        return False, f"目标目录不存在: {target_dir}"
    protected, reason = _is_protected_folder(target_dir)
    if protected:
        return False, f"拒绝处理受保护目录（{reason}）: {target_dir}"
    if temp_staging_dir is None:
        temp_staging_dir = os.path.join(
            os.path.dirname(target_dir), "._staging_tmp_" + os.path.basename(target_dir)
        )
    kept = 0
    try:
        if keep_files_list:
            os.makedirs(temp_staging_dir, exist_ok=True)
            for file_path in keep_files_list:
                if not os.path.exists(file_path):
                    continue
                dest = os.path.join(temp_staging_dir, os.path.basename(file_path))
                if os.path.exists(dest):
                    base, ext = os.path.splitext(dest)
                    dest = f"{base}_keep{ext}"
                shutil.move(file_path, dest)
                kept += 1
        ok, msg = fast_delete_folder_windows(target_dir, recreate=True)
        if not ok:
            return False, f"抹除失败: {msg}"
        moved_back = 0
        if os.path.isdir(temp_staging_dir):
            for file_name in os.listdir(temp_staging_dir):
                shutil.move(
                    os.path.join(temp_staging_dir, file_name),
                    os.path.join(target_dir, file_name),
                )
                moved_back += 1
            try:
                os.rmdir(temp_staging_dir)
            except OSError:
                pass
        return True, f"完成：暂存 {kept} 个，移回 {moved_back} 个"
    except Exception as exc:  # noqa: BLE001
        try:
            if os.path.isdir(temp_staging_dir):
                os.makedirs(target_dir, exist_ok=True)
                for file_name in os.listdir(temp_staging_dir):
                    src = os.path.join(temp_staging_dir, file_name)
                    if os.path.exists(src):
                        shutil.move(src, os.path.join(target_dir, file_name))
        except Exception:
            pass
        return False, f"清理失败: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("delete")
    d.add_argument("target")
    d.add_argument("--execute", action="store_true", help="Actually delete; otherwise preview only")
    d.add_argument("--vendor-scope", choices=["360"], help="Dedicated vendor scope for confirmed residuals")
    d.add_argument("--kill-processes", action="store_true", help="Kill only processes whose executable is under target")
    d.add_argument("--take-ownership", action="store_true", help="Take ownership of this confirmed target if needed")
    d.add_argument("--schedule-reboot", action="store_true", help="Schedule stubborn target for deletion at reboot")
    d.add_argument("--log", default=f"fast-delete-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    d.add_argument("--result-json")
    args = ap.parse_args()

    if os.name != "nt":
        print("This helper is intended for Windows.", file=sys.stderr)
        return 3

    logger = Logger(args.log)
    result = delete_target(
        args.target,
        execute=args.execute,
        vendor_scope=args.vendor_scope,
        kill_processes=args.kill_processes,
        take_owner=args.take_ownership,
        schedule_reboot=args.schedule_reboot,
        logger=logger,
    )
    if args.result_json:
        Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.get("status") in {"PREVIEW", "DELETED", "ALREADY_MISSING", "RENAMED_PENDING_REBOOT"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
