"""压测：验证长时间双路采集会不会丢帧 / 漂移，以及 WAV 落盘正确性。

听悟要求 PCM 16k 单声道。这里走完整的生产路径：
  48k 立体声 loopback + 48k 单声道 mic -> 混音 -> 重采样 16k -> s16le PCM
并统计实际采样点数和名义时长的偏差（漂移），这是判断能不能长时间跑的关键指标。
"""
import sys
import time
import wave
import warnings
import numpy as np
import soundcard as sc
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)

TARGET_SR = 16000
CAPTURE_SR = 48000
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0


def resample_to_16k(x: np.ndarray, src_sr: int) -> np.ndarray:
    """线性插值重采样。开会场景 48k->16k，对 ASR 足够。"""
    if src_sr == TARGET_SR:
        return x
    n_out = int(round(len(x) * TARGET_SR / src_sr))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    xp = np.linspace(0.0, 1.0, num=len(x), endpoint=False)
    xq = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(xq, xp, x).astype(np.float32)


def to_pcm16(x: np.ndarray) -> bytes:
    clipped = np.clip(x, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


spk = sc.default_speaker()
loop = sc.get_microphone(str(spk.name), include_loopback=True)
mic = sc.default_microphone()

print(f"loopback = {spk.name}")
print(f"mic      = {mic.name}")
print(f"采集 {DURATION:.0f} 秒 ...")

chunks_16k = []
disc = {"loopback": 0, "mic": 0}
t0 = time.time()
n_blocks = 0

with loop.recorder(samplerate=CAPTURE_SR, channels=2, blocksize=1024) as lrec, \
     mic.recorder(samplerate=CAPTURE_SR, channels=1, blocksize=1024) as mrec:
    while time.time() - t0 < DURATION:
        lb = lrec.record(numframes=1600)   # 1600/48000 = 33.3ms
        mb = mrec.record(numframes=1600)
        n_blocks += 1
        lmono = lb.mean(axis=1) if lb.ndim > 1 else lb
        mmono = mb.mean(axis=1) if mb.ndim > 1 else mb
        # 对齐长度（两路理论上等长，防御性处理）
        n = min(len(lmono), len(mmono))
        mixed = 0.5 * (lmono[:n] + mmono[:n])
        chunks_16k.append(resample_to_16k(mixed, CAPTURE_SR))

elapsed = time.time() - t0
audio = np.concatenate(chunks_16k) if chunks_16k else np.zeros(0, dtype=np.float32)
audio_sec = len(audio) / TARGET_SR

print()
print("=" * 60)
print(f"墙钟耗时      : {elapsed:.2f} s")
print(f"采集块数      : {n_blocks}")
print(f"音频采样点    : {len(audio)}")
print(f"音频时长      : {audio_sec:.2f} s")
print(f"漂移          : {audio_sec - elapsed:+.3f} s  ({(audio_sec-elapsed)/elapsed*100:+.2f}%)")
print(f"实时率        : {audio_sec/elapsed:.3f}x")
rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
print(f"整体 RMS      : {20*np.log10(max(rms,1e-9)):.2f} dB")
print("=" * 60)

out = str(Path(__file__).resolve().with_name("_probe_mix16k.wav"))
pcm = to_pcm16(audio)
with wave.open(out, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(TARGET_SR)
    w.writeframes(pcm)
print(f"已写出 : {out}")
print(f"PCM 字节数 : {len(pcm)} (期望 {len(audio)*2})")
