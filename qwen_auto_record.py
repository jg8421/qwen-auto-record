"""会议自动录音 —— 命令行入口。

    python qwen_auto_record.py                 # 前台常驻监听（调试用）
    python qwen_auto_record.py --once          # 只检测一次并打印
    python qwen_auto_record.py --list-sessions # 打印当前音频会话
    python qwen_auto_record.py --test-trigger  # 立刻触发一次录音

后台常驻 + 托盘图标 + 控制面板请用 tray_app.py（或「启动自动录音.bat」）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import engine
from engine import (Watcher, capture_sessions, detect_once, log,
                    processes_running)


def cmd_list_sessions() -> int:
    sess = capture_sessions()
    print(f"{'进程':<32} {'麦克风状态'}")
    print("-" * 46)
    for name, st in sorted(sess.items()):
        print(f"{name:<32} {st}")
    return 0


def cmd_once(cfg: dict) -> int:
    info = detect_once(cfg)
    print(f"千问是否在录音: {info['recording']}")
    print(f"检测到的通话  : {info['active_calls'] or '（无）'}")
    for key, spec in cfg["apps"].items():
        if not spec.get("enabled"):
            continue
        for proc in spec["processes"]:
            if proc.lower() in info["running"]:
                print(f"  {spec.get('label', key):<10} {proc:<20} "
                      f"麦克风={info['sessions'].get(proc.lower(), '无会话')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="会议自动录音（唤起千问录音纪要）")
    ap.add_argument("--once", action="store_true", help="只检测一次并打印")
    ap.add_argument("--test-trigger", action="store_true", help="立刻触发一次录音")
    ap.add_argument("--list-sessions", action="store_true", help="打印音频会话")
    ap.add_argument("--config", help="指定配置文件路径")
    args = ap.parse_args()

    cfg_path = Path(args.config) if args.config else engine.CONFIG_PATH
    engine.CONFIG_PATH = cfg_path
    cfg = engine.load_config(cfg_path)

    if args.list_sessions:
        return cmd_list_sessions()
    if args.test_trigger:
        log("[test] 手动触发一次 —— 千问录音窗口应当弹出并开始录音")
        engine.trigger(cfg)
        return 0
    if args.once:
        return cmd_once(cfg)

    w = Watcher(cfg_path)
    w.start()
    try:
        while w.running:
            engine.time.sleep(1)
    except KeyboardInterrupt:
        w.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
