# v3.0.0 changelog

## Added

- Windows software-ecosystem deep audit (`deep_audit_windows.ps1`).
- 360/Qihoo vendor-scoped audit/removal workflow (`cleanup_360.ps1`).
- Browser hijack / URL association / shortcut-target audit.
- Work/engineering-software protection hints.
- Reversible same-volume `待删除` staging and SHA-256 dedupe (`stage_for_delete.py`).
- Raw `$Recycle.Bin/$I` verification without Shell.Application COM (`verify_recycle.py`).

## Changed

- `fast_delete.py` upgraded from simple fast deletion to a failure-aware workflow:
  - persistent JSONL logs;
  - process-under-target checks;
  - attribute clearing;
  - optional target-scoped ownership repair;
  - optional reboot deletion;
  - stronger critical-path refusal;
  - dedicated 360 vendor-scope allowance for confirmed Program Files residuals.
- `SKILL.md` now unifies disk cleanup, software/PUP cleanup, 360 removal, user-data staging, and hard-delete troubleshooting.

## Operational lessons incorporated

- Do not infer current disk state from memory; remeasure before each destructive round.
- Do not count data moved to Recycle Bin or `待删除` as released space.
- Avoid Bash→PowerShell nesting when a native PowerShell runner exists.
- For Chinese/space/`$` paths, use script arguments and parameter arrays instead of inline shell strings.
- Persist logs because some local-agent runtimes swallow stdout/stderr.
- Check/stop locking application processes before deleting their component directories.
- If safe-delete intercepts normal deletion, use the explicit hard-delete helper only for authorized targets.
- Verify hard deletion against path existence, free space, and Recycle Bin metadata.
