"""验证修复后的 engine.trigger_hotkey 能否真的让千问开始录音。"""
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
from pathlib import Path

REC = engine.RECORDING_DIR


def folders():
    return {d.name for d in REC.iterdir() if d.is_dir()} if REC.exists() else set()


print("发键前：")
print(f"  录音文件夹数 = {len(folders())}")
print(f"  千问麦克风   = {engine.capture_sessions().get('qianwen.exe')}")
print(f"  解析 rightctrl+/ -> {engine.parse_hotkey('rightctrl+/')}")
print()
print('>>> engine.trigger_hotkey("rightctrl+/")')
try:
    engine.trigger_hotkey("rightctrl+/")
    print("    SendInput 成功")
except Exception as e:
    print(f"    失败: {type(e).__name__}: {e}")
    sys.exit(1)

base = folders()
ok = False
for i in range(12):
    time.sleep(1.0)
    nf = folders() - base
    mic = engine.capture_sessions().get("qianwen.exe")
    print(f"  {i + 1:>2}s  新文件夹={sorted(nf) if nf else '无'}  麦克风={mic}")
    if nf or mic == "Active":
        ok = True
        break

print()
if ok:
    print("★★★ engine 修复生效：千问开始录音了")
else:
    print("✗ 仍未触发")
