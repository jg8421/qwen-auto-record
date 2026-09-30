"""枚举**全部**音频端点及其会话，排查"多设备盲区"。

之前的实现只看默认设备（GetMicrophone / GetAllSessions，属 eMultimedia 角色）。
通话类应用走 **eCommunications** 角色设备，两者可能是不同端点；
若如此，只看默认设备就会漏掉通话音频。
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import psutil
from comtypes import CLSCTX_ALL, CoInitialize, CoCreateInstance, GUID
from pycaw.constants import CLSID_MMDeviceEnumerator
from pycaw.pycaw import (IAudioSessionManager2, IAudioSessionControl2,
                         IMMDeviceEnumerator, EDataFlow, PROPERTYKEY)

CoInitialize()
STATE = {0: "Inactive", 1: "Active", 2: "Expired"}
PKEY_Device_FriendlyName = PROPERTYKEY(
    fmtid=GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), pid=14)


def nm(pid):
    if not pid:
        return "system"
    try:
        return (psutil.Process(pid).name() or "").lower()
    except Exception:
        return f"pid{pid}"


def friendly(device):
    try:
        return device.OpenPropertyStore(0).GetValue(
            PKEY_Device_FriendlyName).GetValue()
    except Exception:
        return "(取不到名字)"


def sessions_of(device):
    res = {}
    try:
        iface = device.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        mgr = iface.QueryInterface(IAudioSessionManager2)
        enum = mgr.GetSessionEnumerator()
        for i in range(enum.GetCount()):
            c = enum.GetSession(i).QueryInterface(IAudioSessionControl2)
            res[nm(c.GetProcessId())] = STATE.get(c.GetState(), "?")
    except Exception as e:
        res[f"(枚举失败 {type(e).__name__})"] = str(e)[:50]
    return res


enumerator = CoCreateInstance(CLSID_MMDeviceEnumerator,
                              IMMDeviceEnumerator, CLSCTX_ALL)

print("=" * 82)
print("全部音频端点（含所有采集/回放设备）及其实时会话")
print("=" * 82)
total_active = 0
for flow, flow_name in ((EDataFlow.eRender, "回放"), (EDataFlow.eCapture, "采集")):
    coll = enumerator.EnumAudioEndpoints(flow.value, 1)   # 1 = ACTIVE
    n = coll.GetCount()
    print(f"\n### {flow_name}端点 共 {n} 个")
    for i in range(n):
        d = coll.Item(i)
        name = friendly(d)
        print(f"\n  [{i}] {name}")
        s = sessions_of(d)
        if not s:
            print("      （无会话）")
        for k, v in sorted(s.items()):
            flag = "   <== Active" if v == "Active" else ""
            if v == "Active":
                total_active += 1
            print(f"      {k:<30} {v}{flag}")

print()
print("=" * 82)
print(f"当前 Active 会话总数 = {total_active}")
