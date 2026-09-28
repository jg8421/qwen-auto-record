"""测试 WASAPI 能否原生输出 16kHz —— 决定 arquitetura：原生则无需自己重采样。"""
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

spk = sc.default_speaker()
mic = sc.default_microphone()

for target_sr in (16000, 48000):
    for label, src, ch in (("loopback", sc.get_microphone(str(spk.name), include_loopback=True), 2),
                           ("mic", mic, 1)):
        try:
            with src.recorder(samplerate=target_sr, channels=ch, blocksize=1600) as rec:
                t0 = time.time()
                total = 0
                while time.time() - t0 < 2.0:
                    d = rec.record(numframes=1600)
                    total += len(d)
                el = time.time() - t0
            print(f"  请求 {target_sr:>6} Hz | {label:<9} -> 实得 {total:>7} 点 / {el:.2f}s "
                  f"= {total/el:>9.1f} Hz  (漂移 {(total/target_sr-el)/el*100:+.2f}%)")
        except Exception as e:
            print(f"  请求 {target_sr:>6} Hz | {label:<9} -> FAILED {type(e).__name__}: {e}")
