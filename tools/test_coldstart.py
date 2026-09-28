"""测试用户提出的场景：千问没启动时，检测到通话能否自动拉起它并开始录音。"""
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import engine  # noqa: E402
from pathlib import Path

cfg = engine.load_config()
REC = engine.RECORDING_DIR


def folders():
    return {d.name for d in REC.iterdir() if d.is_dir()} if REC.exists() else set()


print("=" * 74)
print("起点状态")
print("=" * 74)
print(f"  千问在运行   : {engine.qianwen_running()}")
print(f"  录音文件夹数 : {len(folders())}")
print(f"  千问占麦克风 : {engine.capture_sessions().get('qianwen.exe')}")
print(f"  wait_ready_sec = {cfg.get('launcher', {}).get('wait_ready_sec')}")

base = folders()
t0 = time.time()

print()
print(">>> engine.trigger_with_launch(cfg)   （模拟：检测到通话 -> 拉起千问 -> 发快捷键）")
ok = engine.trigger_with_launch(cfg)
print(f"    返回 = {ok}   耗时 {time.time()-t0:.1f}s")

print()
print("观察 30 秒：")
hit = None
for i in range(30):
    time.sleep(1.0)
    nf = folders() - base
    mic = engine.capture_sessions().get("qianwen.exe")
    run = engine.qianwen_running()
    line = f"  {i+1:>2}s  千问在跑={run}  麦克风={mic}  新会话={sorted(nf) if nf else '无'}"
    print(line)
    if nf or mic == "Active":
        hit = (sorted(nf), mic)
        break

print()
print("=" * 74)
if hit:
    print("★★★ 成功：千问未启动的情况下，也能自动拉起并开始录音")
    print(f"    新录音会话 = {hit[0]}")
    print(f"    麦克风状态 = {hit[1]}")
else:
    print("✗ 失败：30 秒内没有录起来")
print("=" * 74)
