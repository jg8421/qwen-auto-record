"""排查：微信输入法语音输入为什么会误触发。

同时打印「采集(麦克风)」与「回放(扬声器)」两类会话 ——
真通话是双向的（既有采集也有回放），而语音输入通常只有采集。
用法：python diag_wechat_ime.py [监控秒数]
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


def name_of(pid: int) -> str:
    if not pid:
        return "system"
    try:
        return (psutil.Process(pid).name() or "").lower()
    except Exception:
        return f"pid{pid}"


def sessions(device, label):
    out = {}
    try:
        iface = device.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        mgr = iface.QueryInterface(IAudioSessionManager2)
        enum = mgr.GetSessionEnumerator()
        for i in range(enum.GetCount()):
            c = enum.GetSession(i).QueryInterface(IAudioSessionControl2)
            n = name_of(c.GetProcessId())
            st = STATE.get(c.GetState(), "?")
            if out.get(n) != "Active":
                out[n] = st
    except Exception as e:
        print(f"  [{label}] 枚举失败: {type(e).__name__}: {e}")
    return out


def render_sessions():
    """默认扬声器上的回放会话（谁在出声）。"""
    out = {}
    try:
        for s in AudioUtilities.GetAllSessions():
            n = name_of(s.ProcessId)
            st = STATE.get(s.State, "?")
            if out.get(n) != "Active":
                out[n] = st
    except Exception as e:
        print(f"  [render] 失败: {type(e).__name__}: {e}")
    return out


def capture_sessions():
    """默认麦克风上的采集会话（谁在收音）。"""
    try:
        return sessions(AudioUtilities.GetMicrophone(), "cap")
    except Exception as e:
        print(f"  取麦克风失败: {e}")
        return {}


secs = float(sys.argv[1]) if len(sys.argv) > 1 else 0

WATCH = ("weixin.exe", "wechatappex.exe", "wetype_update.exe",
         "wetype_server.exe", "wetype_renderer.exe", "wetype_service.exe",
         "ms-teams.exe", "zoom.exe", "wemeetapp.exe", "qianwen.exe")

print("=" * 78)
print(f"{'时间':<10}{'进程':<26}{'麦克风':<11}{'扬声器'}")
print("=" * 78)


def dump(tag=""):
    cap = capture_sessions()
    ren = render_sessions()
    keys = sorted({k for k in list(cap) + list(ren) if k in WATCH})
    if not keys:
        print(f"  {tag}（目标进程都没有音频会话）")
        return
    for k in keys:
        c = cap.get(k, "-")
        r = ren.get(k, "-")
        mark = ""
        if c == "Active" and r != "Active":
            mark = "   <== 只有麦克风、没有扬声器（疑似语音输入）"
        elif c == "Active" and r == "Active":
            mark = "   <== 双向音频（像真通话）"
        print(f"  {datetime.now():%H:%M:%S}  {k:<26}{c:<11}{r}{mark}")


if secs <= 0:
    print("\n当前快照：")
    dump()
    print("\n提示：加上秒数参数可持续监控，例如 diag_wechat_ime.py 90")
else:
    print(f"\n持续监控 {secs:.0f} 秒（期间请做：① 微信输入法语音输入 ② 微信语音通话）")
    t0 = time.time()
    while time.time() - t0 < secs:
        dump()
        time.sleep(1.0)
