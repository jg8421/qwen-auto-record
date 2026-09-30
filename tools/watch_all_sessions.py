"""全量音频会话监控：任何进程的任何会话都打印（不限白名单）。

用来抓「微信输入法语音输入」到底动了哪个进程、哪一路。
用法：python watch_all_sessions.py [秒数]
"""
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import psutil
from comtypes import CLSCTX_ALL, CoInitialize
from pycaw.pycaw import (AudioUtilities, IAudioSessionManager2,
                         IAudioSessionControl2)

CoInitialize()
STATE = {0: "Inactive", 1: "Active", 2: "Expired"}


def nm(pid):
    if not pid:
        return "system"
    try:
        return (psutil.Process(pid).name() or "").lower()
    except Exception:
        return f"pid{pid}"


def cap_all():
    out = {}
    try:
        mic = AudioUtilities.GetMicrophone()
        iface = mic.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        mgr = iface.QueryInterface(IAudioSessionManager2)
        enum = mgr.GetSessionEnumerator()
        for i in range(enum.GetCount()):
            c = enum.GetSession(i).QueryInterface(IAudioSessionControl2)
            out[nm(c.GetProcessId())] = STATE.get(c.GetState(), "?")
    except Exception as e:
        print(f"  cap 失败: {e}")
    return out


def ren_all():
    out = {}
    try:
        for s in AudioUtilities.GetAllSessions():
            out[nm(s.ProcessId)] = STATE.get(s.State, "?")
    except Exception as e:
        print(f"  render 失败: {e}")
    return out


secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
print(f"全量监控 {secs:.0f} 秒 —— 请在此期间用微信输入法做语音输入")
print("=" * 78)
t0 = time.time()
prev = None
while time.time() - t0 < secs:
    cap, ren = cap_all(), ren_all()
    keys = sorted(set(cap) | set(ren))
    snap = tuple((k, cap.get(k, "-"), ren.get(k, "-")) for k in keys)
    if snap != prev:                       # 只在有变化时打印，避免刷屏
        ts = datetime.now().strftime("%H:%M:%S")
        active = [k for k in keys
                  if cap.get(k) == "Active" or ren.get(k) == "Active"]
        if active:
            for k in keys:
                if cap.get(k) == "Active" or ren.get(k) == "Active":
                    print(f"  {ts}  {k:<28} 麦克风={cap.get(k,'-'):<10} "
                          f"扬声器={ren.get(k,'-')}")
        prev = snap
    time.sleep(0.5)
print("=" * 78)
print("监控结束。")
