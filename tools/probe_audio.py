"""探测本机音频设备：能否同时用 WASAPI loopback（系统回放）和麦克风录音。

这个脚本是只读探测，不写任何文件，只打印设备清单和各通道的实测电平。
"""
import sys
import time
import numpy as np
import soundcard as sc

# Windows 控制台默认 GBK，设备名里有 ® 这类字符会炸；强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SR = 16000


def rms_db(x: np.ndarray) -> float:
    if x.size == 0:
        return -999.0
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
    return 20 * np.log10(max(r, 1e-9))


def peak(x: np.ndarray) -> float:
    return float(np.max(np.abs(x))) if x.size else 0.0


print("=" * 68)
print("1) 扬声器 (loopback 采集源)")
print("=" * 68)
spk = sc.all_speakers()
for i, s in enumerate(spk):
    print(f"  [{i}] {s.name}")
default_spk = sc.default_speaker()
print(f"  -> default_speaker = {default_spk.name if default_spk else None}")

print()
print("=" * 68)
print("2) 麦克风")
print("=" * 68)
mics = sc.all_microphones(include_loopback=False)
for i, m in enumerate(mics):
    print(f"  [{i}] {m.name}")
default_mic = sc.default_microphone()
print(f"  -> default_microphone = {default_mic.name if default_mic else None}")

print()
print("=" * 68)
print("3) 实测采集 3 秒（请随便放点声音 / 说句话）")
print("=" * 68)

results = {}

if default_spk is not None:
    try:
        loop = sc.get_microphone(str(default_spk.name), include_loopback=True)
        with loop.recorder(samplerate=SR, channels=2, blocksize=1024) as rec:
            t0 = time.time()
            frames = []
            while time.time() - t0 < 3.0:
                frames.append(rec.record(numframes=1600))
            data = np.concatenate(frames, axis=0)
        mono = data.mean(axis=1) if data.ndim > 1 else data
        results["loopback"] = (rms_db(mono), peak(mono), len(mono))
        print(f"  loopback : frames={len(mono):>7}  RMS={rms_db(mono):7.2f} dB  peak={peak(mono):.4f}")
    except Exception as e:
        print(f"  loopback : FAILED -> {type(e).__name__}: {e}")
else:
    print("  loopback : 没有默认扬声器，跳过")

if default_mic is not None:
    try:
        with default_mic.recorder(samplerate=SR, channels=1, blocksize=1024) as rec:
            t0 = time.time()
            frames = []
            while time.time() - t0 < 3.0:
                frames.append(rec.record(numframes=1600))
            data = np.concatenate(frames, axis=0)
        mono = data.mean(axis=1) if data.ndim > 1 else data
        results["mic"] = (rms_db(mono), peak(mono), len(mono))
        print(f"  mic      : frames={len(mono):>7}  RMS={rms_db(mono):7.2f} dB  peak={peak(mono):.4f}")
    except Exception as e:
        print(f"  mic      : FAILED -> {type(e).__name__}: {e}")
else:
    print("  mic      : 没有默认麦克风，跳过")

print()
print("=" * 68)
print("4) 同时开启两路（关键测试）")
print("=" * 68)
try:
    loop = sc.get_microphone(str(default_spk.name), include_loopback=True)
    with loop.recorder(samplerate=SR, channels=2, blocksize=1024) as lrec, \
         default_mic.recorder(samplerate=SR, channels=1, blocksize=1024) as mrec:
        t0 = time.time()
        lb, mb = [], []
        while time.time() - t0 < 3.0:
            lb.append(lrec.record(numframes=1600))
            mb.append(mrec.record(numframes=1600))
        lbd = np.concatenate(lb, axis=0)
        mbd = np.concatenate(mb, axis=0)
    lmono = lbd.mean(axis=1) if lbd.ndim > 1 else lbd
    mmono = mbd.mean(axis=1) if mbd.ndim > 1 else mbd
    print(f"  loopback : frames={len(lmono):>7}  RMS={rms_db(lmono):7.2f} dB")
    print(f"  mic      : frames={len(mmono):>7}  RMS={rms_db(mmono):7.2f} dB")
    print("  => 双路并行采集 成功")
    results["both"] = True
except Exception as e:
    print(f"  双路并行 FAILED -> {type(e).__name__}: {e}")
    results["both"] = False

print()
print("=" * 68)
print("结论")
print("=" * 68)
for k, v in results.items():
    print(f"  {k}: {v}")
