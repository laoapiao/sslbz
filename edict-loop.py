#!/usr/bin/env python3
"""
三省六部 · 数据刷新循环（Windows 原生版）
等价于 scripts/run_loop.sh，但用 Python 原生进程替代 MSYS bash，
规避 Cygwin fork 失败(errno 11 / Win32 error 299)导致的循环崩溃。

用法: python edict-loop.py [间隔秒数 [巡检间隔秒数]]
默认: 15 秒刷新、120 秒巡检、单脚本超时 30 秒、日志 10MB 轮转
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent  # 打包版：以脚本自身所在目录为项目根
SCRIPTS = BASE / "scripts"
PYTHON = sys.executable  # 与当前解释器一致

INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 15
SCAN_INTERVAL = int(sys.argv[2]) if len(sys.argv) > 2 else 120
SCRIPT_TIMEOUT = 30
DASHBOARD_PORT = os.environ.get("EDICT_DASHBOARD_PORT", "7891")
MAX_LOG_SIZE = 10 * 1024 * 1024

TMPDIR = Path(tempfile.gettempdir())
LOG = TMPDIR / "sansheng_liubu_refresh.log"
PIDFILE = TMPDIR / "sansheng_liubu_refresh.pid"

# 每个 tick 要执行的脚本（顺序与 run_loop.sh 一致）
TICK_SCRIPTS = [
    "sync_from_openclaw_runtime.py",
    "sync_agent_config.py",
    "apply_model_changes.py",
    "sync_officials_stats.py",
    "refresh_live_data.py",
]


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} [loop] {msg}\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)


def rotate_log() -> None:
    try:
        if LOG.exists() and LOG.stat().st_size > MAX_LOG_SIZE:
            os.replace(LOG, LOG.with_suffix(LOG.suffix + ".1"))
            log("日志已轮转")
    except OSError:
        pass


def safe_run(script: Path) -> None:
    try:
        with open(LOG, "ab") as out:
            subprocess.run(
                [PYTHON, str(script)],
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=SCRIPT_TIMEOUT,
                cwd=str(SCRIPTS),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
    except subprocess.TimeoutExpired:
        log(f"⚠️ 脚本超时({SCRIPT_TIMEOUT}s): {script.name}")
    except Exception as e:  # 单脚本失败不终止循环
        log(f"⚠️ 脚本异常 {script.name}: {e}")


def scan_stuck_tasks() -> None:
    try:
        data = json.dumps({"thresholdSec": 180}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{DASHBOARD_PORT}/api/scheduler-scan",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            out = resp.read().decode("utf-8", errors="replace").strip()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(out + "\n")
    except Exception:
        pass


def main() -> None:
    # 单实例保护（与 run_loop.sh 一致）
    if PIDFILE.exists():
        try:
            old_pid = int(PIDFILE.read_text().strip())
            if _pid_alive(old_pid):
                log(f"已有实例运行中 (PID={old_pid})，退出")
                return
        except (ValueError, OSError):
            pass
        try:
            PIDFILE.unlink()
        except OSError:
            pass
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")

    log(f"三省六部数据刷新循环启动 (PID={os.getpid()}, 间隔={INTERVAL}s, 巡检={SCAN_INTERVAL}s)")

    scan_counter = 0
    try:
        while True:
            rotate_log()
            for name in TICK_SCRIPTS:
                safe_run(SCRIPTS / name)

            scan_counter += INTERVAL
            if scan_counter >= SCAN_INTERVAL:
                scan_counter = 0
                scan_stuck_tasks()

            time.sleep(INTERVAL)
    finally:
        try:
            PIDFILE.unlink()
        except OSError:
            pass
        log("循环退出")


def _pid_alive(pid: int) -> bool:
    # Windows 上对残留 PID 调 os.kill(pid, 0) 可能抛 OSError 甚至
    # SystemError(WinError 87)，一律按"进程已死"处理
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
