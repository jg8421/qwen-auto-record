"""会议自动录音 —— 托盘图标 + 本地控制面板。

主线程跑托盘消息循环（pystray 在 win32 上要求主线程）；
监听器与控制面板各跑一个后台线程。全程无控制台窗口。

    python tray_app.py               # 托盘 + 面板
    python tray_app.py --no-panel    # 只要托盘
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import engine
from engine import (HERE, Watcher, capture_sessions, list_recordings, log,
                    recent_events, tail_log)

import pystray
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

PANEL_HTML = HERE / "panel.html"
LOCK_PATH = HERE / "auto_record.pid"


def _is_our_process(pid: int) -> bool:
    """辅助判据：该 pid 是否是「另一个」本工具实例。**不作为唯一依据**。

    必须排除自己的父进程：venv 的 pythonw.exe 是个转发器，它的命令行里
    同样带 tray_app.py，不排除就会被当成"已有实例"而自杀式退出。
    """
    if pid == os.getpid():
        return False
    try:
        if pid == os.getppid():
            return False
    except Exception:
        pass
    try:
        import psutil
        p = psutil.Process(pid)
        cmd = " ".join(p.cmdline() or []).lower()
        return "tray_app.py" in cmd or "qwen_auto_record.py" in cmd
    except Exception:
        return False


def instance_alive(host: str, port: int, timeout: float = 1.5) -> bool:
    """权威判据：控制面板是否已有实例在应答。

    比 PID 锁可靠得多 —— PID 会被系统回收、锁文件会残留成"僵尸锁"，
    而「端口上有没有人在应答」是本机事实。只有它明确应答才算已有实例。
    """
    import urllib.request
    try:
        url = f"http://{host}:{port}/api/status"
        # 显式绕过系统代理：本机代理（土星通信/Clash）开着时，
        # urlopen 默认会读注册表里的 ProxyServer，把 127.0.0.1 也送去代理 → 探测失败
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return "trigger_count" in data and "apps" in data
    except Exception:
        return False


def read_lock_pid() -> int | None:
    try:
        return int(LOCK_PATH.read_text(encoding="ascii").strip())
    except Exception:
        return None


def write_lock() -> None:
    try:
        LOCK_PATH.write_text(str(os.getpid()), encoding="ascii")
    except Exception:
        pass


def clear_lock(force: bool = False) -> None:
    """清锁。force=True 时无条件删除（专用于清理陈旧锁）。"""
    try:
        if force or read_lock_pid() == os.getpid():
            if LOCK_PATH.exists():
                LOCK_PATH.unlink()
    except Exception:
        pass

# 状态 -> 配色
COLORS = {
    "listening": (34, 197, 94),    # 绿：监听中
    "recording": (239, 68, 68),    # 红：正在录音
    "paused": (148, 163, 184),     # 灰：已暂停
    "down": (120, 120, 120),       # 深灰：未运行
}


def make_icon(state: str) -> Image.Image:
    """画一个圆形底 + 白色麦克风。"""
    color = COLORS.get(state, COLORS["down"])
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=color + (255,))
    white = (255, 255, 255, 255)
    # 麦头（圆角胶囊）
    d.rounded_rectangle((26, 14, 38, 38), radius=6, fill=white)
    # 拾音弧
    d.arc((19, 24, 45, 44), start=0, end=180, fill=white, width=3)
    # 支架 + 底座
    d.line((32, 44, 32, 50), fill=white, width=3)
    d.line((25, 51, 39, 51), fill=white, width=3)
    return img


class PanelHandler(BaseHTTPRequestHandler):
    watcher: Watcher = None            # 由 serve_panel 注入
    server_version = "QwenAutoRecord/1.0"

    # 安静处理：不往 stderr 刷日志
    def log_message(self, fmt, *args):
        return

    # ---------------- 工具 ----------------
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---------------- 路由 ----------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/panel.html"):
            if not PANEL_HTML.exists():
                self._send(500, b"panel.html not found", "text/plain")
                return
            self._send(200, PANEL_HTML.read_bytes(),
                       "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(self.watcher.snapshot())
            return
        if path == "/api/config":
            self._json(self.watcher.cfg)
            return
        if path == "/api/events":
            self._json({"events": recent_events(120)})
            return
        if path == "/api/log":
            self._json({"lines": tail_log(200)})
            return
        if path == "/api/recordings":
            self._json({"dir": str(engine.RECORDING_DIR),
                        "items": list_recordings(50)})
            return
        if path == "/api/sessions":
            self._json(capture_sessions())
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/config":
            body = self._read_json()
            try:
                cfg = engine.load_config(self.watcher.config_path)
                if "apps" in body:
                    for k, v in body["apps"].items():
                        cfg["apps"].setdefault(k, {}).update(v)
                for k in ("trigger", "recorder_process", "poll_interval_sec",
                          "start_debounce_sec", "retrigger_cooldown_sec"):
                    if k in body:
                        cfg[k] = body[k]
                engine.save_config(cfg, self.watcher.config_path)
                self.watcher.reload_config()
                self._json({"ok": True, "config": self.watcher.cfg})
            except Exception as e:
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return
        if path == "/api/pause":
            want = bool(self._read_json().get("paused", True))
            self.watcher.pause() if want else self.watcher.resume()
            self._json({"ok": True, "paused": self.watcher.paused})
            return
        if path == "/api/trigger":
            self.watcher.trigger_now()
            self._json({"ok": True})
            return
        self._json({"error": "not found"}, 404)


def pick_port(host: str, port: int, tries: int = 12) -> int:
    for p in range(port, port + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"{host} 上 {port}~{port+tries} 都被占用")


def serve_panel(watcher: Watcher, host: str, port: int) -> ThreadingHTTPServer:
    PanelHandler.watcher = watcher
    httpd = ThreadingHTTPServer((host, port), PanelHandler)
    t = threading.Thread(target=httpd.serve_forever, name="panel",
                         daemon=True)
    t.start()
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser(description="会议自动录音托盘版")
    ap.add_argument("--no-panel", action="store_true", help="不启动控制面板")
    ap.add_argument("--port", type=int, help="面板端口（默认取配置里的）")
    ap.add_argument("--config", help="指定配置文件路径")
    args = ap.parse_args()

    cfg_path = Path(args.config) if args.config else engine.CONFIG_PATH
    engine.CONFIG_PATH = cfg_path

    watcher = Watcher(cfg_path)
    host = watcher.cfg.get("panel", {}).get("host", "127.0.0.1")
    want_port = args.port or int(watcher.cfg.get("panel", {}).get("port", 8765))

    # ---- 防重 ----
    # 判据一（权威）：面板端口上有没有实例在应答
    if not args.no_panel and instance_alive(host, want_port):
        log(f"[dup] 已有实例在 {host}:{want_port} 应答，本进程退出")
        try:
            webbrowser.open(f"http://{host}:{want_port}/")
        except Exception:
            pass
        return 0

    # 判据二（辅助）：锁文件里的 pid 是否确实是本工具的存活进程
    stale = read_lock_pid()
    if stale and stale != os.getpid():
        if _is_our_process(stale):
            log(f"[dup] 检测到本工具的另一实例（PID={stale}），本进程退出")
            return 0
        # 死进程留下的僵尸锁 —— 必须清掉，否则会永远起不来
        log(f"[cleanup] 清理陈旧锁文件（PID={stale} 已不存在）")
        clear_lock(force=True)

    write_lock()

    url = ""
    httpd = None
    if not args.no_panel:
        try:
            port = pick_port(host, want_port)
            httpd = serve_panel(watcher, host, port)
            url = f"http://{host}:{port}/"
            log(f"[panel] 控制面板: {url}")
        except Exception as e:
            log(f"[warn] 控制面板启动失败: {type(e).__name__}: {e}")

    watcher.start()

    # ---------------- 菜单动作 ----------------
    def status_text(_item=None):
        s = watcher.snapshot()
        if not s["running"]:
            return "● 未运行"
        if s["paused"]:
            return "⏸ 已暂停"
        if s["recording"]:
            return "● 录音中"
        if s["active_calls"]:
            return f"● 通话中（{', '.join(s['active_calls'])}）"
        return "● 监听中"

    def do_open_panel(icon=None, item=None):
        if url:
            webbrowser.open(url)
        else:
            log("[warn] 面板未启动")

    def do_trigger(icon=None, item=None):
        watcher.trigger_now()

    def do_toggle(icon=None, item=None):
        watcher.resume() if watcher.paused else watcher.pause()

    def toggle_label(_item=None):
        return "恢复监听" if watcher.paused else "暂停监听"

    def do_open_log(icon=None, item=None):
        try:
            os.startfile(str(engine.LOG_PATH))
        except Exception as e:
            log(f"[warn] 打开日志失败: {e}")

    def do_open_recordings(icon=None, item=None):
        try:
            d = engine.RECORDING_DIR
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))
        except Exception as e:
            log(f"[warn] 打开录音目录失败: {e}")

    def do_quit(icon=None, item=None):
        watcher.stop()
        if httpd:
            try:
                httpd.shutdown()
            except Exception:
                pass
        clear_lock()
        icon.stop()

    menu = Menu(
        MenuItem(status_text, None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("打开控制面板", do_open_panel, default=True),
        MenuItem("立即录音一次", do_trigger),
        MenuItem(toggle_label, do_toggle),
        Menu.SEPARATOR,
        MenuItem("打开日志", do_open_log),
        MenuItem("打开录音文件夹", do_open_recordings),
        Menu.SEPARATOR,
        MenuItem("退出", do_quit),
    )

    icon = Icon("qwen_auto_record", make_icon("listening"),
                "千问自动录音", menu)

    def on_change(s: dict):
        """监听器状态变化时刷新托盘图标。"""
        try:
            if not s["running"]:
                st = "down"
            elif s["paused"]:
                st = "paused"
            elif s["recording"]:
                st = "recording"
            else:
                st = "listening"
            icon.icon = make_icon(st)
            seg = [status_text()]
            if s["hotkey"]:
                seg.append(f"触发键 {s['hotkey']}")
            if s["trigger_count"]:
                seg.append(f"已触发 {s['trigger_count']} 次")
            icon.title = "千问自动录音 · " + " · ".join(seg)
        except Exception:
            pass

    watcher.on_change = on_change
    on_change(watcher.snapshot())

    log("[tray] 托盘已启动")
    try:
        icon.run()
    except Exception as e:
        log(f"[error] 托盘运行失败: {type(e).__name__}: {e}")
        watcher.stop()
        clear_lock()
        return 1
    clear_lock()
    log("[tray] 已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
