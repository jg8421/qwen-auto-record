"""探测 Windows 音频会话：哪些进程正在占用扬声器 / 麦克风。

这是「是否在通话中」最可靠的信号——比窗口标题稳得多：
通话时 Teams/Zoom/微信 一定会持有活动的麦克风（采集）会话。
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import psutil
from comtypes import CLSCTX_ALL
from pycaw.pycaw import (
    AudioUtilities,
    IAudioSessionManager2,
    IAudioSessionControl2,
)

STATE = {0: "Inactive", 1: "Active", 2: "Expired"}


def sessions_for(device, label):
    out = []
    try:
        iface = device.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        mgr = iface.QueryInterface(IAudioSessionManager2)
        enum = mgr.GetSessionEnumerator()
        for i in range(enum.GetCount()):
            ctl = enum.GetSession(i).QueryInterface(IAudioSessionControl2)
            pid = ctl.GetProcessId()
            name = "?"
            try:
                name = psutil.Process(pid).name()
            except Exception:
                pass
            state = ctl.GetState()
            out.append((label, pid, name, STATE.get(state, str(state)),
                        ctl.GetDisplayName() or ""))
    except Exception as e:
        print(f"  [{label}] 枚举失败: {type(e).__name__}: {e}")
    return out


print("=" * 72)
print("扬声器（回放）会话")
print("=" * 72)
spk = AudioUtilities.GetSpeakers()
rows = sessions_for(spk, "render")
for label, pid, name, state, disp in rows:
    print(f"  pid={pid:<7} {name:<28} {state:<9} {disp[:30]}")
if not rows:
    print("  (无)")

print()
print("=" * 72)
print("麦克风（采集）会话")
print("=" * 72)
try:
    mic = AudioUtilities.GetMicrophone()
    rows2 = sessions_for(mic, "capture")
    for label, pid, name, state, disp in rows2:
        print(f"  pid={pid:<7} {name:<28} {state:<9} {disp[:30]}")
    if not rows2:
        print("  (无)")
except Exception as e:
    print(f"  取默认麦克风失败: {type(e).__name__}: {e}")

print()
print("=" * 72)
print("目标进程是否在运行")
print("=" * 72)
targets = ["ms-teams", "teams", "zoom", "cptservice", "weixin", "wechat",
           "wechatappex", "wemeet", "dingtalk", "feishu", "lark"]
for p in psutil.process_iter(["pid", "name"]):
    try:
        n = (p.info["name"] or "").lower()
        if any(t in n for t in targets):
            print(f"  pid={p.info['pid']:<7} {p.info['name']}")
    except Exception:
        pass
