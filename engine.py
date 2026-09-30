"""会议自动录音 —— 核心引擎（检测 + 触发 + 可控制状态）。

被两个前端共用：
  - qwen_auto_record.py   命令行
  - tray_app.py           托盘图标 + 本地控制面板

「是否在通话」用 Windows 音频会话判定：通话时该应用必然持有活动的麦克风会话。
「唤起录音」发千问自己的快捷键（实测 = 右 Ctrl + /）。
"""
from __future__ import annotations

import collections
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import psutil
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioSessionManager2, IAudioSessionControl2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LOG_PATH = HERE / "auto_record.log"
STATE_PATH = HERE / "auto_record.state.json"
LOG_MAX_BYTES = 1_000_000
LOG_KEEP_LINES = 500
EVENT_RING = 300

DEFAULT_CONFIG = {
    "apps": {
        "teams": {"enabled": True, "label": "Teams",
                  "processes": ["ms-teams.exe", "teams.exe"]},
        "zoom": {"enabled": True, "label": "Zoom",
                 "processes": ["zoom.exe", "cptservice.exe"]},
        "wechat": {"enabled": True, "label": "微信",
                   "processes": ["weixin.exe", "wechatappex.exe", "wechat.exe"]},
        "tencent_meeting": {"enabled": False, "label": "腾讯会议",
                            "processes": ["wemeetapp.exe"]},
        "feishu": {"enabled": False, "label": "飞书",
                   "processes": ["feishu.exe", "lark.exe"]},
        "dingtalk": {"enabled": False, "label": "钉钉",
                     "processes": ["tblive.exe", "dingtalk.exe"]},
    },
    "trigger": {"method": "hotkey", "hotkey": "rightctrl+/"},
    "recorder_process": "qianwen.exe",
    "poll_interval_sec": 2.0,
    "start_debounce_sec": 3.0,
    "retrigger_cooldown_sec": 30.0,
    "panel": {"host": "127.0.0.1", "port": 8765},
    # ★ 防误触发：真通话是双向的（既采集麦克风、又回放对方声音），
    # 而语音输入法 / 录语音消息只有麦克风。所以要求近期也有回放活动。
    "require_playback": True,
    # 回放需「连续活跃」满这么多秒才算通话（挡掉通知音/提示音）
    "min_playback_sec": 3,
    # 输入法类进程永远不算"通话"（它们也用麦克风做语音输入）
    "exclude_processes": [
        "wetype_update.exe", "wetype_server.exe", "wetype_renderer.exe",
        "wetype_service.exe", "wechatinput.exe", "weixininput.exe",
        "sogouinput.exe", "qqpinyin.exe", "baiduime.exe", "ctfmon.exe",
        "chsime.exe", "textinputhost.exe", "inputmethod.exe",
    ],
    # 录音自动备份：把已结束的录音从千问目录复制到本目录下的 录音\
    "archive": {"enabled": True, "dir": "", "min_age_sec": 60, "interval_sec": 120},
    # 千问没在跑时，先把它拉起来再发快捷键（否则单独发键毫无作用）。
    # wait_ready_sec 是留给它加载录音插件、挂上键盘钩子的时间。
    "launcher": {"auto_launch": True, "exe": "", "wait_ready_sec": 12,
                 "retry_hotkey": 2, "retry_delay_sec": 7},
}

# 录音快捷键的真实来源（逆向确认）：
#   %LOCALAPPDATA%\qianwen\User Data\Default\Preferences
#   -> browser.quark.audio_record.keybinding.settings = " rightctrl + /"
# 输入法：它们会占用麦克风做语音输入，但绝不是"通话"
DEFAULT_EXCLUDE_PROCESSES = [
    "wetype_update.exe", "wetype_server.exe", "wetype_renderer.exe",
    "wetype_service.exe", "wechatinput.exe", "weixininput.exe",
    "sogouinput.exe", "qqpinyin.exe", "baiduime.exe", "ctfmon.exe",
    "chsime.exe", "textinputhost.exe", "inputmethod.exe",
]

RECORDING_DIR = Path(os.path.expandvars(
    r"%APPDATA%\Qianwen\qianwen-ai-record"))

# --------------------------------------------------------------------------
# 日志 + 内存事件环（供面板读取）
# --------------------------------------------------------------------------
_events: collections.deque = collections.deque(maxlen=EVENT_RING)


def log(msg: str, quiet: bool = False) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    _events.append(line)
    # pythonw.exe（无控制台）下 sys.stdout 为 None，直接 print 会报错
    if not quiet and sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-LOG_KEEP_LINES:]
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(tail)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def recent_events(n: int = 120) -> list[str]:
    return list(_events)[-n:]


def tail_log(n: int = 200) -> list[str]:
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            return [x.rstrip("\n") for x in f.readlines()[-n:]]
    except Exception:
        return []


# --------------------------------------------------------------------------
# 音频会话：哪个进程正在占用麦克风
# --------------------------------------------------------------------------
_com_tls = threading.local()


def ensure_com() -> None:
    """COM 必须「按线程」初始化。

    在后台线程（监听线程 / HTTP 面板线程）里不调用 CoInitialize，
    pycaw/comtypes 会抛 OSError -2147221008「尚未调用 CoInitialize」，
    表现为音频会话永远枚举不到、通话检测静默失效。
    """
    if getattr(_com_tls, "ready", False):
        return
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    _com_tls.ready = True


def capture_sessions() -> dict[str, str]:
    """返回 {进程名小写: 状态}。同名进程取「更活跃」的状态。"""
    ensure_com()
    result: dict[str, str] = {}
    try:
        mic = AudioUtilities.GetMicrophone()
        iface = mic.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        mgr = iface.QueryInterface(IAudioSessionManager2)
        enum = mgr.GetSessionEnumerator()
        for i in range(enum.GetCount()):
            ctl = enum.GetSession(i).QueryInterface(IAudioSessionControl2)
            pid = ctl.GetProcessId()
            state = {0: "Inactive", 1: "Active", 2: "Expired"}.get(
                ctl.GetState(), "Unknown")
            if pid == 0:
                name = "system"
            else:
                try:
                    name = (psutil.Process(pid).name() or "").lower()
                except Exception:
                    name = f"pid{pid}"
            if result.get(name) != "Active":
                result[name] = state
    except Exception as e:
        log(f"[warn] 枚举采集会话失败: {type(e).__name__}: {e}")
    return result


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).is_running()
    except Exception:
        return False


def render_sessions() -> dict[str, str]:
    """默认扬声器上「谁在出声」。用来区分"通话"和"只是用麦克风"。"""
    ensure_com()
    out: dict[str, str] = {}
    try:
        for s in AudioUtilities.GetAllSessions():
            pid = s.ProcessId
            if not pid:
                n = "system"
            else:
                try:
                    n = (psutil.Process(pid).name() or "").lower()
                except Exception:
                    n = f"pid{pid}"
            st = {0: "Inactive", 1: "Active", 2: "Expired"}.get(
                s.State, "Unknown")
            if out.get(n) != "Active":
                out[n] = st
    except Exception as e:
        log(f"[warn] 枚举回放会话失败: {type(e).__name__}: {e}")
    return out


def is_excluded_process(proc: str, cfg: dict) -> bool:
    """输入法之类的进程不算通话（它们也用麦克风做语音输入）。"""
    ex = cfg.get("exclude_processes")
    if ex is None:
        ex = DEFAULT_EXCLUDE_PROCESSES
    return proc.lower() in {str(p).lower() for p in ex}


def processes_running() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in psutil.process_iter(["pid", "name"]):
        try:
            n = (p.info["name"] or "").lower()
            if n and n not in out:
                out[n] = p.info["pid"]
        except Exception:
            pass
    return out


def detect_active_calls(cfg: dict, sessions: dict[str, str],
                        render: dict[str, str] | None = None,
                        render_since: dict[str, float] | None = None,
                        now: float | None = None) -> list[str]:
    """返回正在通话的应用 key 列表。

    判定 = 该应用**正在占用麦克风**，并且（默认要求）**近期还有回放活动**。

    为什么要求回放：真通话是双向的 —— 你说话（采集）+ 听对方说（回放）。
    而**语音输入法、录一条语音消息，都只有采集、没有回放**。
    只按"占麦克风"判定，就会把"用微信输入法语音打字"误判成"微信通话"。

    回放要求**连续**活跃 min_playback_sec 秒（默认 3）：
    真通话的播放流一直开着（WASAPI 会话即使当下静音也保持 Active），
    而通知音/提示音只响一两秒 —— 用"持续时长"区分这两者。
    """
    render = render or {}
    render_since = render_since if render_since is not None else {}
    now = now if now is not None else time.time()
    require_pb = cfg.get("require_playback", True)
    min_pb = float(cfg.get("min_playback_sec", 3))

    active = []
    for key, spec in cfg["apps"].items():
        if not spec.get("enabled"):
            continue
        for proc in spec["processes"]:
            p = proc.lower()
            if is_excluded_process(p, cfg):
                continue                     # 输入法一律不算
            if sessions.get(p) != "Active":
                continue                     # 没在用麦克风
            if require_pb:
                since = render_since.get(p)
                if since is None or (now - since) < min_pb:
                    continue                 # 没有持续回放 -> 不是通话
            active.append(key)
            break
    return active


def recorder_is_recording(cfg: dict, sessions: dict[str, str]) -> bool:
    rp = (cfg.get("recorder_process") or "").lower()
    return bool(rp) and sessions.get(rp) == "Active"


# --------------------------------------------------------------------------
# 触发：给千问发快捷键
# --------------------------------------------------------------------------
# ★ 关键：千问的录音快捷键是宿主用「低级键盘钩子」实现的，钩子看到的是**扫描码**。
# 只发虚拟键（wVk）它会完全没反应 —— 实测 VK_RCONTROL(0xA3)+VK_OEM_2(0xBF)
# 毫无动静，而扫描码 0x1D(带扩展标志)+0x35 一次就成功唤出录音。
# 所以这里一律用扫描码 + KEYEVENTF_SCANCODE 发送。
SCAN = {
    "ctrl": 0x1D, "control": 0x1D, "leftctrl": 0x1D,
    "rightctrl": 0x1D, "rctrl": 0x1D,
    "alt": 0x38, "leftalt": 0x38, "rightalt": 0x38, "ralt": 0x38,
    "shift": 0x2A, "leftshift": 0x2A, "rightshift": 0x36, "rshift": 0x36,
    "win": 0x5B,
    "/": 0x35, "?": 0x35, "space": 0x39, ".": 0x34, ",": 0x33,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "q": 0x10, "r": 0x13,
    "n": 0x31, "m": 0x32, "v": 0x2F, "z": 0x2C,
}
SCAN_EXTENDED = {"rightctrl", "rctrl", "rightalt", "ralt", "win"}

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTunion(ctypes.Union):
    # ★ 必须把 MOUSEINPUT 也放进来：x64 上 INPUT 的真实大小（40 字节）由它决定。
    # 只放 KEYBDINPUT、或用小尺寸 padding，会让 sizeof(INPUT)=32；
    # SendInput 会因 cbSize 不合法而**返回 0 且不设 LastError** —— 静默失败，
    # 表现为"日志说发了快捷键，实际什么都没发"。
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


def _send_scan(scan: int, up: bool = False, extended: bool = False) -> None:
    """按扫描码发送一次按键事件（千问的钩子只认扫描码）。"""
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT(type=INPUT_KEYBOARD,
                u=_INPUTunion(ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags,
                                            time=0, dwExtraInfo=None)))
    n = ctypes.windll.user32.SendInput(1, ctypes.byref(inp),
                                       ctypes.sizeof(INPUT))
    if n != 1:
        raise OSError(
            f"SendInput 失败（返回 {n}；sizeof(INPUT)={ctypes.sizeof(INPUT)}，"
            f"x64 应为 40）")


def parse_hotkey(spec: str) -> list[tuple[int, bool]]:
    """解析 'rightctrl+/' -> [(扫描码, 是否带扩展标志), ...]。"""
    keys: list[tuple[int, bool]] = []
    for part in spec.lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part not in SCAN:
            raise ValueError(
                f"不认识的按键: {part!r}（可用: {', '.join(sorted(SCAN))}）")
        keys.append((SCAN[part], part in SCAN_EXTENDED))
    return keys


def trigger_hotkey(spec: str) -> None:
    """按下并释放组合键。修饰键先按后放，主键按 50ms 后放。"""
    keys = parse_hotkey(spec)
    if not keys:
        raise ValueError("快捷键为空")
    *mods, main = keys
    main_scan, main_ext = main
    try:
        for scan, ext in mods:
            _send_scan(scan, extended=ext)
        _send_scan(main_scan, extended=main_ext)
        time.sleep(0.05)
        _send_scan(main_scan, up=True, extended=main_ext)
    finally:
        for scan, ext in reversed(mods):
            _send_scan(scan, up=True, extended=ext)


# --------------------------------------------------------------------------
# 确保千问在跑（没跑就先启动）
# --------------------------------------------------------------------------
QIANWEN_EXE_HINTS = [
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\QianwenApp\qianwen.exe")),
]


def qianwen_running() -> bool:
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == "qianwen.exe":
                return True
        except Exception:
            pass
    return False


def find_qianwen_exe(cfg: dict | None = None) -> Path | None:
    """定位千问主程序：配置指定 -> qianwen:// 协议注册表 -> 常见安装位置。

    读协议注册表最稳：装的路径变了也能找到，因为 Windows 靠它来唤起应用。
    """
    p = ((cfg or {}).get("launcher", {}) or {}).get("exe")
    if p and Path(p).exists():
        return Path(p)
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Classes\qianwen\shell\open\command") as k:
            val = winreg.QueryValueEx(k, "")[0]      # '"C:\...\qianwen.exe" "%1"'
        m = re.match(r'\s*"([^"]+)"', val)
        if m and Path(m.group(1)).exists():
            return Path(m.group(1))
    except Exception:
        pass
    try:
        with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT,
                r"qianwen\shell\open\command") as k:
            val = winreg.QueryValueEx(k, "")[0]
        m = re.match(r'\s*"([^"]+)"', val)
        if m and Path(m.group(1)).exists():
            return Path(m.group(1))
    except Exception:
        pass
    for h in QIANWEN_EXE_HINTS:
        if h.exists():
            return h
    return None


def ensure_qianwen(cfg: dict, wait_ready: float | None = None) -> bool:
    """确保千问在运行。没运行就启动，并等它把录音快捷键的钩子挂上。

    为什么必须等：录音快捷键是录音插件启动时才注册的，而插件启动排在
    ipc / bridge / user / route 之后，冷启动要好几秒。刚启动就发键会打空。
    """
    if qianwen_running():
        return True

    la = cfg.get("launcher", {}) or {}
    if not la.get("auto_launch", True):
        log("[launch] 千问未运行，且配置里关了自动启动 -> 放弃本次触发")
        return False

    exe = find_qianwen_exe(cfg)
    if not exe:
        log("[launch] 找不到千问主程序，无法自动启动（可在 config.json 里指定 launcher.exe）")
        return False

    log(f"[launch] 千问未运行，正在启动：{exe}")
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([str(exe)], close_fds=True, creationflags=flags)
    except Exception as e:
        log(f"[launch] 启动失败: {type(e).__name__}: {e}")
        return False

    deadline = time.time() + 25
    while time.time() < deadline:
        if qianwen_running():
            break
        time.sleep(0.5)
    else:
        log("[launch] 启动后 25 秒仍未见到 qianwen.exe，放弃")
        return False

    warm = float(wait_ready if wait_ready is not None
                 else la.get("wait_ready_sec", 12))
    log(f"[launch] 千问已启动，等 {warm:.0f} 秒让它加载完录音插件…")
    time.sleep(warm)
    return True


def trigger_with_launch(cfg: dict) -> bool:
    """需要的话先拉起千问，再发录音快捷键。"""
    if not ensure_qianwen(cfg):
        return False
    return trigger(cfg)


def trigger(cfg: dict) -> bool:
    t = cfg.get("trigger", {})
    if t.get("method", "hotkey") == "hotkey":
        spec = t.get("hotkey", "rightctrl+/")
        trigger_hotkey(spec)
        log(f"[trigger] 已发送快捷键 {spec}")
        return True
    log(f"[trigger] 未知方式: {t.get('method')}")
    return False


# --------------------------------------------------------------------------
# 配置 / 状态
# --------------------------------------------------------------------------
def load_config(path: Path | None = None) -> dict:
    p = path or CONFIG_PATH
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            merged = json.loads(json.dumps(DEFAULT_CONFIG))
            merged.update({k: v for k, v in cfg.items() if k != "apps"})
            if "apps" in cfg:
                for k, v in cfg["apps"].items():
                    merged["apps"].setdefault(k, {}).update(v)
            return merged
        except Exception as e:
            log(f"[warn] config.json 解析失败，用默认配置: {e}")
    else:
        try:
            p.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
                         encoding="utf-8")
            log(f"[init] 已生成默认配置 {p}")
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: dict, path: Path | None = None) -> None:
    p = path or CONFIG_PATH
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def list_recordings(limit: int = 50) -> list[dict]:
    """列出千问录音文件（本地音频；转写与纪要在云端）。"""
    out: list[dict] = []
    if not RECORDING_DIR.exists():
        return out
    for d in sorted(RECORDING_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        total = 0
        name = ""
        for f in d.rglob("*"):
            if f.is_file() and f.name != ".qianwen-recorder":
                total += f.stat().st_size
                name = f.name
        out.append({
            "folder": d.name,
            "path": str(d),
            "bytes": total,
            "file": name,
            "mtime": datetime.fromtimestamp(d.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"),
        })
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# 录音自动备份
# --------------------------------------------------------------------------
class RecordingArchiver(threading.Thread):
    """把「已经录完」的录音从千问目录复制到备份目录。

    只复制最近 60 秒内没有再被改写的会话（说明录音已结束），
    避免把正在录的文件拷成半截。已备份过的按名字记住，不会重复拷。
    """

    def __init__(self, cfg: dict):
        super().__init__(name="archiver", daemon=True)
        self.cfg = cfg
        self._stop = threading.Event()
        self._done: set[str] = set()
        self._primed = False
        self.last_error = ""

    def stop(self) -> None:
        self._stop.set()

    def dest_root(self) -> Path:
        a = self.cfg.get("archive", {})
        return Path(a["dir"]) if a.get("dir") else (HERE / "录音")

    def sweep(self) -> int:
        a = self.cfg.get("archive", {})
        if not a.get("enabled", True):
            return 0
        if not RECORDING_DIR.exists():
            return 0
        dest = self.dest_root()
        dest.mkdir(parents=True, exist_ok=True)

        # 首次运行：把备份目录里已有的当作"已备份"，避免重复拷
        if not self._primed:
            for d in dest.iterdir():
                if d.is_dir():
                    self._done.add(d.name)
            self._primed = True

        min_age = float(a.get("min_age_sec", 60))
        now = time.time()
        copied = 0
        for d in RECORDING_DIR.iterdir():
            if not d.is_dir() or d.name in self._done:
                continue
            files = [f for f in d.rglob("*") if f.is_file()]
            if not files:
                continue
            newest = max(f.stat().st_mtime for f in files)
            if now - newest < min_age:
                continue                      # 可能还在录，下次再说
            target = dest / d.name
            try:
                shutil.copytree(d, target, dirs_exist_ok=True)
                size = sum(f.stat().st_size for f in target.rglob("*")
                           if f.is_file())
                self._done.add(d.name)
                copied += 1
                log(f"[archive] 已备份录音 {d.name}（{size / 1048576:.1f} MB）")
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log(f"[archive] 备份 {d.name} 失败: {self.last_error}")
        return copied

    def run(self) -> None:
        interval = float(self.cfg.get("archive", {}).get("interval_sec", 120))
        while not self._stop.wait(interval):
            try:
                self.sweep()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log(f"[archive] 扫描异常: {self.last_error}")


# --------------------------------------------------------------------------
# 监听器（可暂停 / 可查询状态）
# --------------------------------------------------------------------------
class Watcher:
    def __init__(self, config_path: Path | None = None,
                 on_change=None):
        self.config_path = config_path or CONFIG_PATH
        self.cfg = load_config(self.config_path)
        self.on_change = on_change
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()      # set = 暂停
        self._lock = threading.RLock()
        state = load_state()
        self.last_trigger_at = float(state.get("last_trigger_at", 0))
        self.last_app = state.get("last_app", "")
        self.trigger_count = int(state.get("trigger_count", 0))
        self.started_at: float | None = None
        self.last_error = ""
        self._active: list[str] = []
        self._recording = False
        self._sessions: dict[str, str] = {}
        self._running: dict[str, int] = {}
        self._pending: dict[str, float] = {}
        self._fired: set[str] = set()      # 本次通话已处理过（防重复触发）
        self._tick_n = 0                   # 轮询计数（用于给进程枚举降频）
        self._render_since: dict[str, float] = {}  # 各进程"回放连续活跃"的起点
        self.archiver = RecordingArchiver(self.cfg)

    # ---------------- 控制 ----------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._loop, name="watcher",
                                        daemon=True)
        self._thread.start()
        if self.cfg.get("archive", {}).get("enabled", True):
            self.archiver.start()
            try:
                self.archiver.sweep()      # 启动时先补备份一轮
            except Exception as e:
                log(f"[archive] 首次扫描异常: {type(e).__name__}: {e}")
        enabled = [v.get("label", k) for k, v in self.cfg["apps"].items()
                   if v.get("enabled")]
        log(f"[start] 监听中，目标应用: {', '.join(enabled)}；"
            f"触发键 {self.cfg['trigger'].get('hotkey')}")

    def stop(self) -> None:
        self._stop.set()
        self.archiver.stop()
        if self._thread:
            self._thread.join(timeout=3)
        log("[stop] 监听已停止")

    def pause(self) -> None:
        self._paused.set()
        log("[pause] 已暂停监听")
        self._notify()

    def resume(self) -> None:
        self._paused.clear()
        log("[resume] 已恢复监听")
        self._notify()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def reload_config(self) -> None:
        with self._lock:
            self.cfg = load_config(self.config_path)
        log("[config] 已重新载入配置")
        self._notify()

    def trigger_now(self) -> None:
        """手动触发一次（面板 / 托盘菜单用）。"""
        try:
            trigger_with_launch(self.cfg)
            self.last_trigger_at = time.time()
            self.last_app = "manual"
            self.trigger_count += 1
            save_state({"last_trigger_at": self.last_trigger_at,
                        "last_app": self.last_app,
                        "trigger_count": self.trigger_count})
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log(f"[error] 手动触发失败: {self.last_error}")
        self._notify()

    # ---------------- 状态 ----------------
    def snapshot(self) -> dict:
        with self._lock:
            uptime = time.time() - self.started_at if self.started_at else 0
            return {
                "running": self.running,
                "paused": self.paused,
                "recording": self._recording,
                "active_calls": list(self._active),
                "pending": list(self._pending.keys()),
                "last_app": self.last_app,
                "last_trigger_at": self.last_trigger_at,
                "trigger_count": self.trigger_count,
                "uptime_sec": round(uptime, 1),
                "last_error": self.last_error,
                "hotkey": self.cfg["trigger"].get("hotkey", ""),
                "cooldown_sec": self.cfg.get("retrigger_cooldown_sec", 30),
                "apps": [
                    {
                        "key": k,
                        "label": v.get("label", k),
                        "enabled": bool(v.get("enabled")),
                        "processes": v.get("processes", []),
                        "running": [p for p in v.get("processes", [])
                                    if p.lower() in self._running],
                        "mic": next((self._sessions.get(p.lower())
                                     for p in v.get("processes", [])
                                     if p.lower() in self._running), None),
                    }
                    for k, v in self.cfg["apps"].items()
                ],
            }

    def _verify_soon(self, app: str, attempt: int = 1) -> None:
        """发完快捷键后回头确认千问真的开始录了；没成功就补发几次。

        为什么必须有这一步：刚冷启动千问时，录音插件要几秒才把键盘钩子挂上，
        第一次发键很容易打空。没有「验证 + 重试」，日志会显示"已触发"、
        实际却没录到任何东西。
        """
        la = self.cfg.get("launcher", {}) or {}
        max_attempt = int(la.get("retry_hotkey", 2)) + 1
        delay = float(la.get("retry_delay_sec", 7))
        hotkey = self.cfg.get("trigger", {}).get("hotkey")

        def worker():
            time.sleep(delay)
            try:
                if recorder_is_recording(self.cfg, capture_sessions()):
                    log(f"[verify] {app}: 千问已开始录音 ✓（第 {attempt} 次尝试）")
                    return
                if attempt < max_attempt:
                    log(f"[verify] 第 {attempt} 次未生效，{delay:.0f} 秒后重试…")
                    try:
                        trigger(self.cfg)
                    except Exception as e:
                        log(f"[verify] 重试发送失败: {type(e).__name__}: {e}")
                        return
                    self._verify_soon(app, attempt + 1)
                else:
                    log(f"[verify] ⚠ 试了 {attempt} 次，千问仍未开始录音 —— "
                        f"请确认千问里录音快捷键确实是 {hotkey}，"
                        f"以及千问是否处于已登录状态")
            except Exception as e:
                log(f"[verify] 校验异常: {type(e).__name__}: {e}")

        threading.Thread(target=worker, daemon=True,
                         name="verify-trigger").start()

    def _notify(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.snapshot())
            except Exception:
                pass

    # ---------------- 主循环 ----------------
    def _loop(self) -> None:
        prev_active: set[str] = set()
        while not self._stop.is_set():
            try:
                self._tick(prev_active)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log(f"[error] 主循环异常: {self.last_error}")
            interval = float(self.cfg.get("poll_interval_sec", 2.0))
            self._stop.wait(interval)

    def _tick(self, prev_active: set[str]) -> None:
        now_t = time.time()
        sessions = capture_sessions()
        # 回放活动：谁在出声。用于把"通话"和"只用麦克风（语音输入）"分开
        need_pb = self.cfg.get("require_playback", True)
        render = render_sessions() if need_pb else {}
        for _p, _st in render.items():
            if _st == "Active":
                self._render_since.setdefault(_p, now_t)   # 记住连续起点
            else:
                self._render_since.pop(_p, None)           # 断了就清零
        active = set(detect_active_calls(self.cfg, sessions, render,
                                         self._render_since, now_t))
        rec = recorder_is_recording(self.cfg, sessions)

        # 进程列表只用于面板展示，没必要每轮都全量枚举（省一半 CPU）。
        # 每 5 轮刷新一次；首次(_running 为空)立即刷。
        self._tick_n += 1
        if self._tick_n % 5 == 1 or not self._running:
            running = processes_running()
        else:
            running = self._running

        with self._lock:
            self._sessions = sessions
            self._running = running
            self._active = sorted(active)
            self._recording = rec

        if self.paused:
            # 暂停期间仍然跟踪真实通话状态，恢复后不会「补触发」已在进行中的通话
            prev_active.clear()
            prev_active.update(active)
            self._notify()
            return

        debounce = float(self.cfg.get("start_debounce_sec", 3.0))
        cooldown = float(self.cfg.get("retrigger_cooldown_sec", 30.0))
        now = time.time()

        # 通话开始：必须连续稳定 debounce 秒才触发。
        # 注意：判定不能只在「首次发现该通话」那一个 tick 做 —— 否则 debounce
        # 大于轮询间隔时，下一 tick 就因 app 已在 prev_active 里而被 continue，
        # 永远等不到 debounce 满足，表现为「检测得到但永不触发」。
        for app in active:
            first_seen = self._pending.get(app)
            if first_seen is None:
                self._pending[app] = now          # 本通话第一次看到，开始计时
                continue
            if now - first_seen < debounce:
                continue                          # 还没稳定够，继续等
            if app in self._fired:
                continue                          # 本次通话已处理过
            if rec:
                self._fired.add(app)
                log(f"[skip] 检测到 {app} 通话，但千问正在录音，忽略")
                continue
            if now - self.last_trigger_at < cooldown:
                continue                          # 冷却中：保留计时，下个 tick 再试
            log(f"[detect] {app} 通话开始 -> 唤起千问录音纪要")
            try:
                trigger_with_launch(self.cfg)
                self.last_trigger_at = time.time()
                self.last_app = app
                self.trigger_count += 1
                save_state({"last_trigger_at": self.last_trigger_at,
                            "last_app": app,
                            "trigger_count": self.trigger_count})
                self._fired.add(app)
                self._verify_soon(app)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                self._fired.add(app)
                log(f"[error] 触发失败: {self.last_error}")

        for app in list(self._pending):
            if app not in active:
                self._pending.pop(app, None)
        self._fired &= active                     # 只保留仍在进行的通话

        for app in prev_active - active:
            log(f"[end] {app} 通话结束（录音需在千问窗口手动结束）")

        prev_active.clear()
        prev_active.update(active)
        self._notify()


def detect_once(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    sess = capture_sessions()
    run = processes_running()
    return {
        "recording": recorder_is_recording(cfg, sess),
        "active_calls": detect_active_calls(cfg, sess, run),
        "sessions": sess,
        "running": run,
    }
