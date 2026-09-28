"""对比不同 blocksize 的漂移，找出最稳的采集参数。

漂移来源：每次 record() 调用之间的调度间隙导致样本丢失。
块越小、调用越频繁，间隙累积越多；块越大，单次等待越久但间隙占比越低。
"""
import sys
import time
import warnings
import numpy as np
import soundcard as sc

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)

SR = 48000
DURATION = 6.0

spk = sc.default_speaker()
loop = sc.get_microphone(str(spk.name), include_loopback=True)

print(f"设备: {loop.name}")
print(f"{'blocksize':>10} | {'块数':>6} | {'点数':>8} | {'时长s':>7} | {'墙钟s':>7} | {'漂移%':>8}")
print("-" * 62)

for bs, frames_per_call in [(512, 512), (1024, 1024), (2048, 2048), (4800, 4800), (9600, 9600)]:
    try:
        total = 0
        t0 = time.time()
        with loop.recorder(samplerate=SR, channels=2, blocksize=bs) as rec:
            while time.time() - t0 < DURATION:
                d = rec.record(numframes=frames_per_call)
                total += len(d)
        el = time.time() - t0
        dur = total / SR
        drift = (dur - el) / el * 100
        print(f"{bs:>10} | {total//max(frames_per_call,1):>6} | {total:>8} | {dur:>7.2f} | {el:>7.2f} | {drift:>+7.2f}%")
    except Exception as e:
        print(f"{bs:>10} | FAILED: {type(e).__name__}: {e}")
